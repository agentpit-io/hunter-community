"""用户信号订阅设置 — GET/POST /api/signal-settings"""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from app.services.database import get_signal_subscriptions, set_signal_subscription

router = APIRouter()

ALL_SIGNAL_TYPES = ["cpi", "oil", "fomc", "spacex", "northbound"]


def _get_user_id(request: Request) -> str:
    uid = getattr(request.state, "user_id", "") or ""
    if not uid:
        raise HTTPException(status_code=401, detail="未登录")
    return uid


@router.get("/signal-settings")
async def get_settings(request: Request):
    uid = _get_user_id(request)
    subs = get_signal_subscriptions(uid)
    return subs


class SettingsUpdate(BaseModel):
    signal_type: str
    enabled: bool


@router.post("/signal-settings")
async def update_settings(body: SettingsUpdate, request: Request):
    uid = _get_user_id(request)
    if body.signal_type not in ALL_SIGNAL_TYPES:
        raise HTTPException(status_code=400, detail="invalid signal_type")
    set_signal_subscription(uid, body.signal_type, body.enabled)
    return {"ok": True}
