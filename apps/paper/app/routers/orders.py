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
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from app import db, idempotency, ledger
from app.matching import engine
from app.schemas import CancelIn, ExpireIn, OrderIn

router = APIRouter(tags=["orders"])


@router.post("/api/v1/orders")
def place_order(body: OrderIn) -> dict:
    try:
        with db.cursor(commit=True) as cur:
            return engine.execute(cur, body.model_dump())
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
        return engine.cancel_order(cur, order["project_id"], order_id, memo)


@router.post("/api/v1/projects/{project_id}/orders/expire")
def expire_orders(project_id: str, body: ExpireIn) -> dict:
    """收盘未成交 → 撤单（`expired`）并**解冻**。

    `reason='close'`：撤当日全部挂单 —— 这是 `05 §3.2` M-12 第③步的最后一段。
    `reason='validity'`：只撤越过 `valid_until` 的（「过期不补单」）。
    """
    with db.cursor(commit=True) as cur:
        if not ledger.get_project(cur, project_id):
            raise HTTPException(404, f"项目不存在：{project_id}")
        return engine.expire_open_orders(cur, project_id, body.at, reason=body.reason)


@router.post("/api/v1/projects/{project_id}/orders/match-open")
def match_open(project_id: str) -> dict:
    try:
        with db.cursor(commit=True) as cur:
            if not ledger.get_project(cur, project_id):
                raise HTTPException(404, f"项目不存在：{project_id}")
            return engine.match_open_orders(cur, project_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
