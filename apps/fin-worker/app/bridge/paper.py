"""Paper Service 客户端 —— **fin-worker 唯一改账本的通道**。

`01方案 §7.4` / §11.3：推理区不能绕过执行区改账本。fin-worker 没有账本库的连接串、
依赖里也没有任何 DB 驱动，它想改账本，只能打这里的 HTTP 接口。

调用约定：
- 每个请求都带 `X-Hunter-Internal-Key`（`paper` 除 `/healthz` 外一律要它，见 `app/security.py`）。
  **L06 起这个头带的是「执行凭证」`HUNTER_EXEC_KEY`**（不是行情读取凭证）——
  paper 的执行门校验的正是它，且过了门还要过执行允许名单（默认拒绝）。
- 非 2xx 一律抛 `PaperError`（带状态码与原文），**不吞**。Activity 让 Temporal 重试；
  重试用的还是同一个幂等键，所以「重试」不会变成「重复下单」。
- **404 是语义**：`get_calendar` 的 404 表示「这一天没有日历」——
  调用方据此走「不知道 = 不当交易日」的分支，不是异常。
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from app import config

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class PaperError(RuntimeError):
    """paper 返回了非 2xx，或根本连不上。带状态码与响应原文。"""

    def __init__(self, status: int, detail: str):
        super().__init__(f"paper HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class IdempotencyConflict(PaperError):
    """同一幂等键、不同内容 —— paper 返回 409（`01方案 §11.1`）。**不覆盖**。"""


class PaperClient:
    def __init__(self, base_url: Optional[str] = None, key: Optional[str] = None,
                 client: Optional[httpx.Client] = None):
        self._base = (base_url or config.paper_base_url()).rstrip("/")
        # **执行凭证**（`HUNTER_EXEC_KEY`）—— 打 paper 走这一把（L06）。
        self._key = key if key is not None else config.exec_key()
        self._client = client or httpx.Client(timeout=TIMEOUT)

    # ── 底层 ──────────────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        return {"X-Hunter-Internal-Key": self._key, "Content-Type": "application/json"}

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        url = f"{self._base}{path}"
        try:
            return self._client.request(method, url, headers=self._headers(), **kw)
        except httpx.HTTPError as exc:  # 连不上 / 超时 —— 交给 Temporal 重试
            raise PaperError(0, f"无法连接 paper（{exc.__class__.__name__}: {exc}）") from exc

    def _json(self, method: str, path: str, *, allow: tuple[int, ...] = (), **kw) -> Optional[dict]:
        resp = self._request(method, path, **kw)
        if resp.status_code in allow:
            return None
        if resp.status_code == 409:
            raise IdempotencyConflict(409, resp.text)
        if resp.status_code >= 400:
            raise PaperError(resp.status_code, resp.text)
        if not resp.content:
            return None
        return resp.json()

    # ── 健康 ──────────────────────────────────────────────────────────────
    def healthz(self) -> dict:
        resp = self._request("GET", "/healthz")
        if resp.status_code >= 400:
            raise PaperError(resp.status_code, resp.text)
        return resp.json()

    # ── 项目（读）────────────────────────────────────────────────────────
    def list_projects(self, status: str = "active") -> list[dict]:
        """每个项目带 `markets`（`fin_project_market` 的行，P2）—— 逐项透传，不重排。

        工作流侧 `workflows.projects_for_market` 读的就是这个字段（「选中集合包含
        该市场」），所以这里**原样带回**、不做裁剪；老响应没有该键时上游按
        `None` 兼容（退回单值 `market_scope` 语义）。
        """
        data = self._json("GET", "/api/v1/projects", params={"status": status})
        return (data or {}).get("items", [])

    def get_project(self, project_id: str, market: Optional[str] = None) -> Optional[dict]:
        """项目 + 账本视图。`market` 必须传 —— `MULTI` 项目不传会被 paper 回 400
        （「该项目有多个市场，请指定 market」，P1 起不再静默落到 A 股）。

        单市场项目传不传都一样（paper 按项目 `market_scope` 兜底）。
        """
        params = {"market": market} if market else None
        data = self._json(
            "GET", f"/api/v1/projects/{project_id}", params=params, allow=(404,)
        )
        return data

    def get_instrument(self, code: str) -> Optional[dict]:
        """标的元数据（`fin_instrument` 的一行：`lot_size` / `market` / 涨跌幅…）。

        账本里没有这一行 → `None`（paper 回 404）。示例策略据此拿**每手股数**
        给港美股定量 —— 拿不到就不出委托，不猜一个手数。
        """
        return self._json("GET", f"/api/v1/instruments/{code}", allow=(404,))

    # ── 交易日历 ─────────────────────────────────────────────────────────
    def get_calendar(self, trade_date: str, market: str = "CN_A") -> Optional[dict]:
        """`None` = 这一天没有日历（404）。**不代表「非交易日」**，代表「不知道」。

        日历按 `(market, trade_date)` 读（0029 起）；缺省 `CN_A` 保持旧调用方行为。
        """
        return self._json("GET", f"/api/v1/market-calendar/{trade_date}",
                          params={"market": market}, allow=(404,))

    def upsert_calendar(self, trade_date: str, *, is_trading: bool,
                        sessions: list[dict[str, str]], note: str,
                        market: str = "CN_A") -> dict:
        return self._json(
            "PUT",
            f"/api/v1/market-calendar/{trade_date}",
            json={"trade_date": trade_date, "is_trading": is_trading,
                  "sessions": sessions, "note": note, "market": market},
        )

    def upsert_instrument(self, body: dict) -> dict:
        """把一条标的元数据写进 `fin_instrument`（幂等 upsert）。

        **同步失败不写** —— 调用方拿不到 `available=true` 的元数据就别调这个，
        让风控第 4 条去拒绝那个标的（`总控规则 §八`）。
        """
        code = body["code"]
        return self._json("PUT", f"/api/v1/instruments/{code}", json=body)

    # ── 委托（唯一写账本的入口）───────────────────────────────────────────
    def place_order(self, body: dict[str, Any]) -> dict:
        return self._json("POST", "/api/v1/orders", json=body)

    # ── R7 · 影子臂模拟撮合（**不是下单**：纸面路径，不写 fin_trade / fin_order）──
    def shadow_simulate(self, body: dict[str, Any]) -> dict:
        """把一个快照上两臂的决策跑一遍，拿回影子里程碑（`POST /api/v1/shadow/simulate`）。

        调用的是 `paper/app/shadow.py` 的 `simulate_arms` —— 它只依赖 `DecisionRecorder`、
        **从不调用执行端**（红线 12）。所以这里虽然打的是 paper，但**一个订单都不会下**。
        """
        return self._json("POST", "/api/v1/shadow/simulate", json=body)

    def match_open(self, project_id: str, market: Optional[str] = None) -> dict:
        params = {"market": market} if market else None
        return self._json("POST", f"/api/v1/projects/{project_id}/orders/match-open",
                          params=params)

    def expire_open(self, project_id: str, at_iso: str, reason: str = "close",
                    market: Optional[str] = None) -> dict:
        params = {"market": market} if market else None
        return self._json(
            "POST",
            f"/api/v1/projects/{project_id}/orders/expire",
            params=params,
            json={"at": at_iso, "reason": reason},
        )

    def cancel_order(self, order_id: str, memo: str) -> dict:
        return self._json(
            "POST", f"/api/v1/orders/{order_id}/cancel", json={"memo": memo}
        )

    # ── 日切 / 估值 / 对账 ────────────────────────────────────────────────
    def confirm_t1(self, project_id: str, market: Optional[str] = None) -> dict:
        """T+1 日切。`market` 对多市场项目**必填**（否则 paper 回 400）—— 见接口注释。"""
        params = {"market": market} if market else None
        return self._json("POST", f"/api/v1/projects/{project_id}/confirm-t1", params=params)

    def make_valuation(self, project_id: str, as_of_iso: str,
                       market: Optional[str] = None) -> dict:
        return self._json(
            "POST", f"/api/v1/projects/{project_id}/valuation",
            json={"as_of": as_of_iso, "market": market},
        )

    def run_recon(self, project_id: str, as_of_iso: str,
                  market: Optional[str] = None) -> dict:
        return self._json(
            "POST", f"/api/v1/projects/{project_id}/recon",
            json={"as_of": as_of_iso, "market": market},
        )

    # ── 参考数据（调度用）────────────────────────────────────────────────
    def market_rules(self) -> list[dict]:
        """三个市场的规则行（时区 / 调度时点 / 币种）。fin-worker 的调度从它读。"""
        data = self._json("GET", "/api/v1/market-rules")
        return (data or {}).get("items", [])

    # ── 长任务（业务检查点）──────────────────────────────────────────────
    def submit_job(self, *, job_type: str, params: dict, project_id: str,
                   idempotency_key: str) -> dict:
        return self._json(
            "POST",
            "/api/v1/jobs",
            json={"type": job_type, "params": params,
                  "project_id": project_id, "idempotency_key": idempotency_key},
        )

    def mark_job_running(self, job_id: str) -> Optional[dict]:
        """把任务标成 RUNNING。

        已经 RUNNING 的（Worker 重启后重放走到这里）会返回 409 —— **故意吞掉**：
        它不是错误，是「这一步上次已经做过了」的正常信号（幂等重放）。
        """
        return self._json(
            "POST", f"/api/v1/jobs/{job_id}/running", allow=(404, 409)
        )

    def set_checkpoint(self, job_id: str, checkpoint: dict) -> Optional[dict]:
        """只写业务检查点，**不推进状态**（`01方案 §11.2` 长计算中断按检查点恢复）。"""
        return self._json(
            "POST",
            f"/api/v1/jobs/{job_id}/checkpoint",
            json={"checkpoint": checkpoint},
            allow=(404, 409),
        )

    def succeed_job(self, job_id: str, result_ref: str, checkpoint: dict) -> Optional[dict]:
        return self._json(
            "POST",
            f"/api/v1/jobs/{job_id}/succeed",
            json={"result_ref": result_ref, "checkpoint": checkpoint},
            allow=(404, 409),
        )

    def fail_job(self, job_id: str) -> Optional[dict]:
        return self._json("POST", f"/api/v1/jobs/{job_id}/fail", allow=(404, 409))

    def cancel_job(self, job_id: str) -> Optional[dict]:
        """请求取消一个长任务（L08 · `§10.4` 的「可请求取消」）。

        paper 的 `request_cancel` 是**幂等**的：已经终态的任务原样返回（`changed:false`），
        不会报错；RUNNING → `CANCEL_REQUESTED`，ACCEPTED → `CANCELLED`。
        404 吞掉（工作流已撤回、job 不存在也是「没什么可取消的」）。
        """
        return self._json("POST", f"/api/v1/jobs/{job_id}/cancel", allow=(404,))
