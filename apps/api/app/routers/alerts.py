"""价格提醒 CRUD — 每只股票、每个用户可独立设置条件，触发后推飞书。"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from app.services.database import (
    list_price_alerts, add_price_alert, delete_price_alert,
)

router = APIRouter()

VALID_CONDITIONS = {"price_below", "price_above", "change_pct_below", "volatility_above"}


def _uid(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, "需要登录")
    return uid


class AlertIn(BaseModel):
    label: str = ""
    condition_type: str
    threshold: float
    threshold2: Optional[float] = None
    cooldown_minutes: int = 60


@router.get("/alerts/{code}")
async def get_alerts(code: str, request: Request):
    user_id = _uid(request)
    return {"alerts": list_price_alerts(user_id, code)}


@router.post("/alerts/{code}")
async def create_alert(code: str, body: AlertIn, request: Request):
    user_id = _uid(request)
    if body.condition_type not in VALID_CONDITIONS:
        raise HTTPException(400, f"condition_type 必须是 {VALID_CONDITIONS}")
    if body.threshold <= 0:
        raise HTTPException(400, "threshold 必须大于 0")
    alert_id = add_price_alert(
        user_id, code, body.label, body.condition_type,
        body.threshold, body.threshold2, body.cooldown_minutes,
    )
    return {"ok": True, "id": alert_id}


@router.delete("/alerts/{alert_id}")
async def remove_alert(alert_id: int, request: Request):
    user_id = _uid(request)
    ok = delete_price_alert(alert_id, user_id)
    if not ok:
        raise HTTPException(404, "提醒不存在或无权限")
    return {"ok": True}
