"""hunter-community api 客户端 —— 取「数据面」的东西，不碰账本。

两件事：

1. **交易日历**：`GET /api/internal/calendar/trading-days`（api 侧用 akshare 的
   A 股交易日历，**前视**的，能回答「今天是不是交易日」—— 从 klines 反推的日历
   只能回答过去，回答不了今天早上）。
2. **K 线 ETL 触发**：`POST /api/internal/etl/run-market`。这是 `01方案 §5.3`
   那一条的落点 —— **「何时拉数据」从 api 进程内的 asyncio 定时循环挪到 Temporal**，
   但**拉数的实现函数一个字符都没改**（api 侧端点直接调原来的 `klines_etl.run_market`）。
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from app import config

TIMEOUT = httpx.Timeout(120.0, connect=10.0)  # ETL 是长任务，给足预算


class ApiError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"api HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class HunterApiClient:
    def __init__(self, base_url: Optional[str] = None, internal_key: Optional[str] = None,
                 client: Optional[httpx.Client] = None):
        self._base = (base_url or config.api_base_url()).rstrip("/")
        self._key = internal_key if internal_key is not None else config.internal_key()
        self._client = client or httpx.Client(timeout=TIMEOUT)

    def _headers(self) -> dict[str, str]:
        return {"X-Hunter-Internal-Key": self._key, "Content-Type": "application/json"}

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        url = f"{self._base}{path}"
        try:
            return self._client.request(method, url, headers=self._headers(), **kw)
        except httpx.HTTPError as exc:
            raise ApiError(0, f"无法连接 api（{exc.__class__.__name__}: {exc}）") from exc

    def trading_days(self, market: str, start: str, end: str) -> list[str]:
        """返回 [start, end] 区间内的**交易日**（YYYY-MM-DD 升序）。

        失败抛 `ApiError` —— 调用方据此走「日历未知 → 不当交易日」的分支，
        **不许自己按星期几补一个**。
        """
        resp = self._request(
            "GET", "/api/internal/calendar/trading-days",
            params={"market": market, "start": start, "end": end},
        )
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, resp.text)
        return list(resp.json().get("trading_days", []))

    def run_market_etl(self, market: str, limit: Optional[int] = None,
                       bars: Optional[int] = None) -> dict[str, Any]:
        params: dict[str, Any] = {"market": market}
        if limit is not None:
            params["limit"] = limit
        if bars is not None:
            params["bars"] = bars
        resp = self._request("POST", "/api/internal/etl/run-market", params=params)
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, resp.text)
        return resp.json()

    def fin_instruments(self, codes: Optional[list[str]] = None,
                        market: str = "cn") -> dict[str, Any]:
        """标的元数据（涨跌停 / ST / 板块），从 `company_master` / `stock_universe` 解析。

        `codes` 留空 = 全量。返回体里每条带 `available`：**判不出的那条 `available=false`
        且带 `reason`** —— 调用方跳过它，**不许拿代码形态硬凑一个幅度**写进 `fin_instrument`。
        """
        params: dict[str, Any] = {"market": market}
        if codes:
            params["codes"] = ",".join(codes)
        resp = self._request("GET", "/api/internal/fin/instruments", params=params)
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, resp.text)
        return resp.json()

    def quote(self, code: str) -> Optional[dict[str, Any]]:
        """当前报价（统一行情结构：`last_price` / `prev_close` / `event_time` / `quote_quality`…）。

        **只读**（api 侧 `GET /internal/fin/quote/{code}` 不落库）。P2 用它给
        「只接限价单的市场」取一个**真实**的参考价 —— 限价单必须带价，而港美股不许
        用市价单（拍板 §四），所以这个价只能来自行情源，**不编数字**。

        `None` = 这一只此刻没有报价（api 回 404，正常的「没有」）——
        调用方据此**不出委托**，不拿上一次的价顶替。
        """
        resp = self._request("GET", f"/api/internal/fin/quote/{code}")
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, resp.text)
        return resp.json()

    def generate_report(self, project_id: str, trade_date: str) -> dict[str, Any]:
        """触发每日报告生成（M5）。

        数字在 api 侧由**确定性指标代码**从账本算出（`fin_report_fact`），AI 只写文字，
        生成后跑回读校验；这里只负责「触发」与把结果带回来。
        """
        resp = self._request(
            "POST", "/api/internal/fin/reports/generate",
            json={"project_id": project_id, "trade_date": trade_date},
        )
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, resp.text)
        return resp.json()

    # ── R3 · 统一经验系统（Memory Service 的唯一入口，经内网口令）──────────────
    def memory_query(self, project_id: str, *, market: Optional[str] = None,
                     for_decision: bool = False, freeze: bool = False,
                     purpose: str = "decision", trade_date: Optional[str] = None,
                     point: Optional[str] = None,
                     as_of: Optional[str] = None) -> dict[str, Any]:
        """读经验（**唯一读入口**）。`freeze=True` 时顺带冻结，返回 `memory_snapshot_id`。

        过滤**一律在服务端**：这里没有 `include_holdout` / `debug` 之类的口子，
        `holdout_only` / 跨项目 / 基准时刻之后形成的经验都读不到 ——
        「不实现就不可能被误开」。

        `as_of` 是**回放基准**：决策路径传决策那一刻（`now`），
        于是这份冻结集合里不会有「事后才形成的经验」。
        """
        resp = self._request(
            "POST", "/api/internal/fin/memory/query",
            json={"project_id": project_id, "market": market,
                  "for_decision": bool(for_decision), "freeze": bool(freeze),
                  "purpose": purpose, "trade_date": trade_date, "point": point,
                  "as_of": as_of},
        )
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, resp.text)
        return resp.json()

    def memory_append(self, *, project_id: str, kind: str, statement: str,
                      evidence: list[dict[str, Any]], market: Optional[str] = None,
                      applicability: Optional[str] = None,
                      invalidation_condition: Optional[str] = None,
                      method: Optional[str] = None, sample_size: Optional[int] = None,
                      as_of: Optional[str] = None,
                      memory_layer: Optional[str] = None,
                      polarity: Optional[str] = None,
                      symbols: Optional[list[str]] = None,
                      strategy_keys: Optional[list[str]] = None,
                      regime_tags: Optional[list[str]] = None,
                      regime_source: Optional[str] = None) -> dict[str, Any]:
        """写经验（**唯一写入口**）。`source` 由服务端按证据真值 / 调用方决定，这里传不了。

        八条硬校验都在服务端（`statement` 含阿拉伯数字、证据至少一行、引用必须真实存在…）。
        任一不过回 **400**，本方法抛 `ApiError` —— **不吞**（吞掉就等于让一条编出来的经验静默入库）。

        R5 起可带结构化标签（`symbols` / `regime_tags` / `regime_source` / `polarity` /
        `memory_layer` / `strategy_keys`）—— 形状与闭集由服务端校验，这一层只搬运。
        """
        body: dict[str, Any] = {
            "project_id": project_id, "kind": kind, "statement": statement,
            "evidence": evidence,
        }
        for key, value in (("market", market), ("applicability", applicability),
                           ("invalidation_condition", invalidation_condition),
                           ("method", method), ("sample_size", sample_size),
                           ("as_of", as_of),
                           ("memory_layer", memory_layer), ("polarity", polarity),
                           ("symbols", symbols), ("strategy_keys", strategy_keys),
                           ("regime_tags", regime_tags), ("regime_source", regime_source)):
            if value is not None:
                body[key] = value
        resp = self._request("POST", "/api/internal/fin/memory/evidence", json=body)
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, resp.text)
        return resp.json()

    # ── R3 · 复核（复盘）回路 ────────────────────────────────────────────────
    def review_collect(self, project_id: str, trade_date: str,
                       market: Optional[str] = None) -> dict[str, Any]:
        """取当日报告（含 `self_review`）+ 事实行 + 成交。**只读。**"""
        resp = self._request(
            "POST", "/api/internal/fin/review/collect",
            json={"project_id": project_id, "trade_date": trade_date, "market": market},
        )
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, resp.text)
        return resp.json()

    def review_propose(self, project_id: str, trade_date: str,
                       market: Optional[str] = None) -> dict[str, Any]:
        """产出候选经验（模型只写文字 + 回读校验）。**不写库** —— 写走 `memory_append`。

        api 侧要调模型，可能几十秒到几分钟 —— 单独放宽这次请求的超时。
        """
        resp = self._request(
            "POST", "/api/internal/fin/review/propose",
            json={"project_id": project_id, "trade_date": trade_date, "market": market},
            timeout=300.0,
        )
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, resp.text)
        return resp.json()

    # ── R7 · 影子验证（内网口令通道；唯一服务模块是 api 的 services/fin/evolution.py）──
    def evolution_proposals(self, project_id: str, limit: int = 200) -> list[dict[str, Any]]:
        """列出某项目下的提案（含冻结计划）。`fin.shadow` 工作流据此挑出待验证的提案。"""
        resp = self._request("POST", "/api/internal/fin/evolution/proposals",
                             json={"project_id": project_id, "limit": limit})
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, resp.text)
        return list(resp.json().get("items", []))

    def evolution_shadow_validate(self, proposal_id: str, market: str,
                                  trade_date: str) -> dict[str, Any]:
        """把提案推进到 `validating` 并记下**验证窗口起点**（幂等）。"""
        resp = self._request("POST", "/api/internal/fin/evolution/validate",
                             json={"proposal_id": proposal_id, "market": market,
                                   "trade_date": trade_date})
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, resp.text)
        return resp.json()

    def evolution_shadow_prepare(self, proposal_id: str,
                                 market: Optional[str] = None) -> dict[str, Any]:
        """影子一步的准备数据：两臂配置 / 两臂当前状态 / 初始资金 / 计划。"""
        resp = self._request("POST", "/api/internal/fin/evolution/shadow/prepare",
                             json={"proposal_id": proposal_id, "market": market})
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, resp.text)
        return resp.json()

    def evolution_shadow_record(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """把两臂的影子里程碑**追加**进影子事件表（唯一键幂等）。"""
        resp = self._request("POST", "/api/internal/fin/evolution/shadow/record",
                             json={"records": records})
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, resp.text)
        return resp.json()

    def evolution_shadow_evaluate(self, proposal_id: str, market: str,
                                  trade_date: str) -> dict[str, Any]:
        """算两臂指标并按冻结计划判定（终局才追加 passed / failed / inconclusive）。"""
        resp = self._request("POST", "/api/internal/fin/evolution/shadow/evaluate",
                             json={"proposal_id": proposal_id, "market": market,
                                   "trade_date": trade_date})
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, resp.text)
        return resp.json()
