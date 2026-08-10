"""用户偏好记忆 API

GET  /api/user/preference          — 读取当前用户偏好
PUT  /api/user/preference          — 保存/更新当前用户偏好
PUT  /api/user/entry-preference    — 保存 A股 / 美港股入口偏好 (Redis)
GET  /api/user/flags               — 读取所有客户端 feature flag（灰度用）
PUT  /api/user/flag/{key}          — 内部/测试手动打开某个 flag
"""
import hashlib
import os

import redis as redis_lib
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from app.services.database import get_user_preference, upsert_user_preference

router = APIRouter()

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_redis = redis_lib.from_url(_REDIS_URL, decode_responses=True)


class PreferenceIn(BaseModel):
    investment_style: str = ""
    risk_tolerance:   str = ""
    holding_period:   str = ""
    focus_sectors:    List[str] = []
    market_scope:     str = "A股"
    push_focus:       str = ""


@router.get("/user/preference")
async def get_preference(request: Request):
    user_id = request.state.user_id
    return get_user_preference(user_id)


@router.put("/user/preference")
async def update_preference(request: Request, payload: PreferenceIn):
    user_id = request.state.user_id
    result = upsert_user_preference(
        user_id=user_id,
        investment_style=payload.investment_style,
        risk_tolerance=payload.risk_tolerance,
        holding_period=payload.holding_period,
        focus_sectors=payload.focus_sectors,
        market_scope=payload.market_scope,
        push_focus=payload.push_focus,
    )
    return result


class EntryPrefIn(BaseModel):
    end: str  # "wx" (A股) | "gm" (美港股)


@router.put("/user/entry-preference")
async def update_entry_preference(request: Request, payload: EntryPrefIn):
    """记录用户入口偏好, 供微信 OAuth callback 直接 302 到目标工作台,
    省一次 /entry 客户端 replace (P0-b 优化)。
    """
    user_id = request.state.user_id
    if payload.end not in ("wx", "gm"):
        raise HTTPException(status_code=400, detail="end 必须为 'wx' 或 'gm'")
    _redis.set(f"user_pref:{user_id}:end", payload.end, ex=365 * 24 * 3600)
    return {"ok": True, "end": payload.end}


# ─────────────────────── Feature Flags（灰度开关）───────────────────────
# 环境变量优先级：
#   1. AGENT_CHAT_V2_FORCE_ON=1  → 全量强开（不看 Redis / 百分比）
#   2. Redis 用户 override（`flag:{user_id}:agent_chat_v2` = "on" | "off"）
#   3. 灰度百分比 AGENT_CHAT_V2_ROLLOUT_PCT (0-100) 按 user_id hash 判定
#   4. 默认 off
_ALLOWED_FLAGS = ("agent_chat_v2",)


def _rollout_pct(env_key: str, default: int = 0) -> int:
    """百分比优先级：Redis 动态覆盖 > 环境变量 > default。
    Redis key 格式：flag_rollout:{key}  (值为 0-100 字符串)"""
    # 从 env_key 反推 flag key: AGENT_CHAT_V2_ROLLOUT_PCT → agent_chat_v2
    flag_key = env_key.replace("_ROLLOUT_PCT", "").lower()
    try:
        override = _redis.get(f"flag_rollout:{flag_key}")
        if override is not None:
            return max(0, min(100, int(override)))
    except Exception:
        pass
    try:
        v = int(os.getenv(env_key, str(default)))
    except ValueError:
        v = default
    return max(0, min(100, v))


def _hash_bucket(user_id: str, salt: str) -> int:
    """稳定百分位 (0-99)"""
    h = hashlib.sha256(f"{salt}:{user_id}".encode()).hexdigest()
    return int(h[:8], 16) % 100


def _resolve_flag(user_id: str, key: str) -> bool:
    if os.getenv(f"{key.upper()}_FORCE_ON", "").lower() in ("1", "true", "yes"):
        return True
    override = _redis.get(f"flag:{user_id}:{key}")
    if override in ("on", "true", "1"):
        return True
    if override in ("off", "false", "0"):
        return False
    pct = _rollout_pct(f"{key.upper()}_ROLLOUT_PCT", 0)
    if pct <= 0:
        return False
    if pct >= 100:
        return True
    return _hash_bucket(user_id, key) < pct


@router.get("/user/flags")
async def get_user_flags(request: Request):
    """一次性拿到所有客户端 feature flag，减少往返"""
    user_id = request.state.user_id
    return {k: _resolve_flag(user_id, k) for k in _ALLOWED_FLAGS}


class FlagIn(BaseModel):
    value: bool


@router.put("/user/flag/{key}")
async def set_user_flag(key: str, payload: FlagIn, request: Request):
    """内部/内测：手动打开或关闭当前用户的某个 flag（覆盖百分比灰度）"""
    if key not in _ALLOWED_FLAGS:
        raise HTTPException(400, f"未知 flag: {key}")
    user_id = request.state.user_id
    _redis.set(f"flag:{user_id}:{key}", "on" if payload.value else "off",
               ex=30 * 24 * 3600)
    return {"ok": True, "key": key, "value": payload.value}


class RolloutIn(BaseModel):
    pct: int  # 0-100


# 允许操作 rollout 的管理员 user_id 白名单（逗号分隔）
_ADMIN_USER_IDS = {
    x for x in (os.getenv("HERMES_ADMIN_USER_IDS", "") or "")
             .replace(" ", "").split(",")
    if x
}


@router.put("/user/flag/{key}/rollout")
async def set_flag_rollout(key: str, payload: RolloutIn, request: Request):
    """管理员：动态调整某 flag 的灰度百分比（Redis 覆盖，无需 restart）。
    环境变量白名单 HERMES_ADMIN_USER_IDS 控制谁能调；空则任何登录用户可调（内测方便）。"""
    if key not in _ALLOWED_FLAGS:
        raise HTTPException(400, f"未知 flag: {key}")
    if not 0 <= payload.pct <= 100:
        raise HTTPException(400, "pct 必须 0-100")
    user_id = request.state.user_id
    if _ADMIN_USER_IDS and user_id not in _ADMIN_USER_IDS:
        raise HTTPException(403, "非管理员")
    _redis.set(f"flag_rollout:{key}", str(payload.pct))
    return {"ok": True, "key": key, "pct": payload.pct,
            "set_by": user_id}


@router.get("/user/flag/{key}/rollout")
async def get_flag_rollout(key: str):
    """公开：查询某 flag 当前灰度百分比（便于运维/看板）"""
    if key not in _ALLOWED_FLAGS:
        raise HTTPException(400, f"未知 flag: {key}")
    try:
        override = _redis.get(f"flag_rollout:{key}")
        if override is not None:
            return {"key": key, "pct": int(override), "source": "redis"}
    except Exception:
        pass
    env_pct = _rollout_pct(f"{key.upper()}_ROLLOUT_PCT", 0)
    return {"key": key, "pct": env_pct, "source": "env"}
