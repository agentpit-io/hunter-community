"""委托提交：唯一改变账本的入口。

一个请求 = 一个事务：风控 → 落委托（过 / 不过都落）→ 过则成交 + 流水 + 持仓 + 版本。
失败落一条 `rejected` 委托并带 `decline_reason`；**没有任何分支读 `source`**
（`09 §六-10`），AI 的单与人下的单走完全同一条路。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app import db, ledger
from app.schemas import OrderIn

router = APIRouter(tags=["orders"])


@router.post("/api/v1/orders")
def place_order(body: OrderIn) -> dict:
    try:
        with db.cursor(commit=True) as cur:
            return ledger.place_order(cur, body.model_dump())
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
