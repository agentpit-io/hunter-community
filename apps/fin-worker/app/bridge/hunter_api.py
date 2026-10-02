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
