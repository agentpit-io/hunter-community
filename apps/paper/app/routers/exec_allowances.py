"""执行允许名单的**登记入口**（L06）。

升到 L06 之后，`paper` 默认**谁的执行请求都不放行**（空名单 = 默认拒绝，见
`app/allowlist.py`）。这个入口就是「显式登记」的落点 —— 谁、什么时候、给了哪些权限，
全部落进 `fin_exec_allowance`（**只追加**，撤销 = 再追加一条 `revoked`，不删）。

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/exec-allowances` | 登记一条**放行**（scope=project/instrument/tool） |
| POST | `/api/v1/exec-allowances/revoke` | 登记一条**撤销**（留痕，不删） |
| GET  | `/api/v1/exec-allowances` | 看现行清单 + 登记流水（排查用） |

**保护**：与其余路由一样走 `app/security.py` 的**执行门**（`X-Hunter-Internal-Key`
必须等于 `HUNTER_EXEC_KEY`）—— 能登记权限的钥匙就是能下单的那把，不另开一把。

`granted_by` 必填：这是**留痕**，不是装饰。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app import allowlist, db
from app.schemas import AllowanceIn

router = APIRouter(tags=["exec-allowances"])


@router.post("/api/v1/exec-allowances")
def register(body: AllowanceIn) -> dict:
    """登记一条放行。返回新追加的那一行（含 `allowance_id` / `granted_at`）。"""
    try:
        with db.cursor(commit=True) as cur:
            return allowlist.register(
                cur, scope=body.scope, subject=body.subject,
                granted_by=body.granted_by, note=body.note,
            )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/v1/exec-allowances/revoke")
def revoke(body: AllowanceIn) -> dict:
    """撤销一条。**不删** —— 追加一条 `status='revoked'`（留痕）。"""
    try:
        with db.cursor(commit=True) as cur:
            return allowlist.revoke(
                cur, scope=body.scope, subject=body.subject,
                granted_by=body.granted_by, note=body.note,
            )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/v1/exec-allowances")
def list_allowances(limit: int = Query(200, ge=1, le=1000)) -> dict:
    """现行清单（`effective`）与登记流水（`history`）—— 只读，排查用。"""
    with db.cursor() as cur:
        return {
            "effective": allowlist.effective(cur),
            "count_active": allowlist.count_active(cur),
            "history": allowlist.history(cur, limit=limit),
        }
