"""委托：唯一改变账本的入口。

| 端点 | 干什么 |
|---|---|
| `POST /api/v1/orders` | 走完四步链路（取快照 → 过规则 → 撮合 → 记账） |
| `POST /api/v1/orders/{order_id}/cancel` | 撤一张挂单并解冻 |
| `POST /api/v1/projects/{id}/orders/expire` | 批量撤单：收盘（`close`）或过期（`validity`） |
| `POST /api/v1/projects/{id}/orders/match-open` | 拿新快照再撮一遍挂单（M4 的时点工作流调它） |

一个请求 = 一个事务：幂等记录、委托、成交、流水、持仓、版本号**要么一起提交、
要么一起回滚**（`01方案 §11.1`：幂等记录与账本变更必须原子关联）。

**没有任何分支读 `source`**（`09 §六-10`）：AI 的单与人下的单走完全同一条路。

**L06 · 执行允许名单（默认拒绝）**：这四个会改账本的端点，入口先查 `app/allowlist.py`
的名单 —— 项目不在名单上（或标的 / 工具维度已启用却不含本次）就 **403**。这是
「服务端鉴权」之外的第二层：口令对 ≠ 被授权执行（`plan/L06.md` §1.2）。鉴权（401）
由 `app/security.py` 在进路由之前完成，这里只管授权（403）。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from app import allowlist, db, idempotency, ledger
from app.matching import engine
from app.schemas import CancelIn, ExpireIn, OrderIn

router = APIRouter(tags=["orders"])


def _require_project(cur, project_id: str, market: str | None = None) -> dict:
    """项目必须在；`MULTI` 项目必须有显式 `market`（否则 400，**不许落到 A 股**）。

    与 `routers/projects.py:_require_project` 同口径（两个模块各留一份，不跨路由 import）。
    另外**不认未知市场名** —— 静默当 A 股就是拿 A 股顶替港美股。
    单市场项目不传 `market` 行为逐字不变。
    """
    project = ledger.get_project(cur, project_id)
    if not project:
        raise HTTPException(404, f"项目不存在：{project_id}")
    try:
        ledger.require_market(project, market)
    except ledger.MarketRequired as exc:
        raise HTTPException(400, str(exc)) from exc
    if market is not None and market not in ledger.MARKET_CURRENCY:
        raise HTTPException(400, f"未知市场：{market}（只认 CN_A / HK / US）")
    return project


@router.post("/api/v1/orders")
def place_order(body: OrderIn) -> dict:
    try:
        with db.cursor(commit=True) as cur:
            # 执行允许名单（默认拒绝）：项目 / 标的 / 工具三维都过才放行。
            allowlist.check(cur, project_id=body.project_id, code=body.code,
                            tool=allowlist.TOOL_PLACE_ORDER)
            return engine.execute(cur, body.model_dump())
    except allowlist.AllowanceDenied as exc:
        # 403：口令对了，但这次执行没被授权（与 401「没钥匙」分清）。
        raise HTTPException(403, str(exc)) from exc
    except idempotency.IdempotencyConflict as exc:
        # 409：同一键、不同内容。**不覆盖**旧记录（`01方案 §11.1`）。
        raise HTTPException(409, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/v1/orders/{order_id}/cancel")
def cancel_order(order_id: str, body: Optional[CancelIn] = None) -> dict:
    memo = body.memo if body and body.memo else "人工撤单"
    with db.cursor(commit=True) as cur:
        order = ledger.get_order(cur, order_id)
        if not order:
            raise HTTPException(404, f"委托不存在：{order_id}")
        try:
            allowlist.check(cur, project_id=order["project_id"], code=order.get("code"),
                            tool=allowlist.TOOL_CANCEL_ORDER)
        except allowlist.AllowanceDenied as exc:
            raise HTTPException(403, str(exc)) from exc
        return engine.cancel_order(cur, order["project_id"], order_id, memo)


@router.post("/api/v1/projects/{project_id}/orders/expire")
def expire_orders(project_id: str, body: ExpireIn, market: str | None = None) -> dict:
    """收盘未成交 → 撤单（`expired`）并**解冻**。

    `reason='close'`：撤当日全部挂单 —— 这是 `05 §3.2` M-12 第③步的最后一段。
    `reason='validity'`：只撤越过 `valid_until` 的（「过期不补单」）。

    **按市场**（P2）：多市场项目在每个市场收盘各撤各的，不互相牵连（见 `engine`）。
    """
    with db.cursor(commit=True) as cur:
        _require_project(cur, project_id, market)
        try:
            allowlist.check(cur, project_id=project_id, tool=allowlist.TOOL_EXPIRE_ORDERS)
        except allowlist.AllowanceDenied as exc:
            raise HTTPException(403, str(exc)) from exc
        return engine.expire_open_orders(cur, project_id, body.at, reason=body.reason,
                                         market=market)


@router.post("/api/v1/projects/{project_id}/orders/match-open")
def match_open(project_id: str, market: str | None = None) -> dict:
    try:
        with db.cursor(commit=True) as cur:
            _require_project(cur, project_id, market)
            allowlist.check(cur, project_id=project_id, tool=allowlist.TOOL_MATCH_OPEN)
            return engine.match_open_orders(cur, project_id, market=market)
    except allowlist.AllowanceDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
