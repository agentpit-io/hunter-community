"""公司行为的**人工登记口**（`L05` 第 4 项）。

**数据源未接** —— 分红 / 拆合股 / 送股只由这里登记，`source` 如实写 `manual`。
登记即应用：账务（现金入账 / 股数与成本价调整）在同一事务里落账本，
事件行 `fin_corporate_action` **只追加**（触发器挡 UPDATE / DELETE）。

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/corporate-actions` | 登记一次公司行为并立即应用账务 |
| GET  | `/api/v1/corporate-actions` | 列出事件（可按项目 / 标的过滤） |

**这是账本级的写操作**（改持仓 / 现金），所以走 `db.cursor(commit=True)` 的写事务、
并且经 `ledger.apply_corporate_action` 里的**单写者闸门**（与下单互斥）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app import db, ledger
from app.schemas import CorporateActionIn

router = APIRouter(tags=["corporate-actions"])


@router.post("/api/v1/corporate-actions")
def register(body: CorporateActionIn) -> dict:
    """登记并应用。**核对字段一致性由 `CorporateActionIn` 把关**（422）。"""
    with db.cursor(commit=True) as cur:
        try:
            return ledger.apply_corporate_action(
                cur, body.project_id,
                action_type=body.action_type, code=body.code, ex_date=body.ex_date,
                ratio=body.ratio, cash_per_share=body.cash_per_share,
                actor=body.actor, memo=body.memo,
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc


@router.get("/api/v1/corporate-actions")
def list_actions(project_id: str = Query(...), code: str | None = None,
                 limit: int = Query(200, ge=1, le=1000)) -> dict:
    with db.cursor() as cur:
        return {"items": ledger.list_corporate_actions(cur, project_id, code, limit)}
