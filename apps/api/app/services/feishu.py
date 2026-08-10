"""飞书 Open API 客户端：拿 tenant_access_token 并发送消息。

只用 HTTP，不依赖飞书 SDK。token 缓存 1 小时（飞书返回的 expire 通常 7200s，留余量）。
"""
import os
import time
from typing import Any, Dict, Optional

import httpx
from loguru import logger

_OPEN_API = "https://open.feishu.cn/open-apis"
_HERMES_ENV = os.path.expanduser("~/.hermes/.env")

# token 缓存：全局 token 用 "" key，多租户用 user_id 作 key
_TOKEN_CACHE: Dict[str, Any] = {}  # {user_id_or_"": {"token": ..., "expire_at": ...}}


def _read_hermes_env() -> Dict[str, str]:
    """读取 ~/.hermes/.env，优先用进程 env，支持前端配置后立即生效"""
    result = {}
    try:
        for line in open(_HERMES_ENV).read().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                result[k.strip()] = v.strip()
    except Exception:
        pass
    return result


async def get_tenant_access_token(user_id: str = "") -> Optional[str]:
    """获取飞书 tenant_access_token。user_id 为空时用全局配置，否则用该用户的配置。"""
    cache_key = user_id or ""
    now = time.time()
    cached = _TOKEN_CACHE.get(cache_key, {})
    if cached.get("token") and cached.get("expire_at", 0) > now + 60:
        return cached["token"]

    # 多租户：优先从 DB 读用户配置
    if user_id:
        from app.services.database import get_feishu_config
        cfg = get_feishu_config(user_id)
        app_id     = cfg["app_id"]     if cfg else ""
        app_secret = cfg["app_secret"] if cfg else ""
    else:
        hermes_env = _read_hermes_env()
        app_id     = os.environ.get("FEISHU_APP_ID")     or hermes_env.get("FEISHU_APP_ID", "")
        app_secret = os.environ.get("FEISHU_APP_SECRET") or hermes_env.get("FEISHU_APP_SECRET", "")

    if not app_id or not app_secret:
        logger.warning("FEISHU_APP_ID/FEISHU_APP_SECRET 未配置")
        return None

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{_OPEN_API}/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
            data = r.json()
            if data.get("code") != 0:
                logger.error("飞书 token 获取失败: {}", data)
                return None
            _TOKEN_CACHE[cache_key] = {
                "token": data["tenant_access_token"],
                "expire_at": now + min(3600, data.get("expire", 7200) - 60),
            }
            return _TOKEN_CACHE[cache_key]["token"]
    except Exception as e:
        logger.error("拿飞书 token 异常: {}", e)
        return None


async def send_message(chat_id: str, msg_type: str, content: Dict[str, Any],
                       user_id: str = "") -> Dict[str, Any]:
    """msg_type: text | interactive。content 是飞书要求的对应结构，会被 json.dumps 后塞进 content 字段。"""
    import json
    token = await get_tenant_access_token(user_id)
    if not token:
        return {"ok": False, "error": "no_token"}

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{_OPEN_API}/im/v1/messages?receive_id_type=chat_id",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "receive_id": chat_id,
                    "msg_type": msg_type,
                    "content": json.dumps(content, ensure_ascii=False),
                },
            )
            data = r.json()
            if data.get("code") == 0:
                return {"ok": True, "message_id": data.get("data", {}).get("message_id")}
            return {"ok": False, "error": data.get("msg") or str(data)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def send_text(chat_id: str, text: str) -> Dict[str, Any]:
    return await send_message(chat_id, "text", {"text": text})


async def send_card(chat_id: str, card: Dict[str, Any], user_id: str = "") -> Dict[str, Any]:
    return await send_message(chat_id, "interactive", card, user_id=user_id)


async def send_text_p2p(open_id: str, text: str) -> Dict[str, Any]:
    """用企业 Bot 给指定 open_id 用户发私聊文本消息（不需要 chat_id）。"""
    import json as _json
    token = await get_tenant_access_token()
    if not token:
        return {"ok": False, "error": "no_token"}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{_OPEN_API}/im/v1/messages?receive_id_type=open_id",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "receive_id": open_id,
                    "msg_type": "text",
                    "content": _json.dumps({"text": text}, ensure_ascii=False),
                },
            )
            data = r.json()
            if data.get("code") == 0:
                return {"ok": True}
            logger.warning("send_text_p2p failed: {}", data)
            return {"ok": False, "error": data.get("msg") or str(data)}
    except Exception as e:
        logger.error("send_text_p2p exception: {}", e)
        return {"ok": False, "error": str(e)}
