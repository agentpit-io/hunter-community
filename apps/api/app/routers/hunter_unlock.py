"""Hunter platform key · status / save / clear  —  GET·PUT·DELETE /api/hunter/unlock

Backs the bottom-left "解锁全部工具" button in the chat sidebar. The UI needs to
know three things and this router answers all of them in one GET:

  · configured  — is there a key at all (so the button reads 解锁 vs 已解锁)
  · unlocked    — does that key actually work upstream (a revoked key is
                  configured but not unlocked · the UI must say so, not
                  silently fail on the first tool call)
  · tools       — the list to show, returned even while locked so the sidebar
                  can display everything and prompt on click

Auth: any logged-in user of this instance. It's software you run on your own
machine; there is no reason to gate it behind an admin role.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services import hunter_key

router = APIRouter(prefix="/hunter", tags=["hunter-unlock"])


def _uid(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, "需要登录")
    return str(uid)


class KeyIn(BaseModel):
    key: str


@router.get("/unlock")
async def status(request: Request):
    _uid(request)
    key = hunter_key.resolve()
    m = await hunter_key.manifest(key)
    return {
        "configured": bool(key),
        "unlocked": bool(m.get("unlocked")),
        "masked": hunter_key.masked(key),
        "env_locked": hunter_key.env_locked(),
        "apply_url": m.get("apply_url") or hunter_key.APPLY_URL,
        "message": m.get("message"),
        "tools": m.get("tools") or [],
        "upstream_error": bool(m.get("upstream_error")),
    }


@router.put("/unlock")
async def save(body: KeyIn, request: Request):
    """Verify before storing — saving a typo'd key and only finding out on the
    first tool call is the worst version of this flow."""
    _uid(request)
    if hunter_key.env_locked():
        raise HTTPException(
            409, "这台实例的 key 来自 .env（HUNTER_API_KEY），请改 .env 后重启容器")
    plain = body.key.strip()
    if not plain:
        raise HTTPException(400, "key 不能为空")

    m = await hunter_key.manifest(plain)
    if m.get("upstream_error"):
        raise HTTPException(503, m.get("message") or "连不上 Hunter 服务器")
    if not m.get("unlocked"):
        raise HTTPException(400, "这把 key 无效或已吊销，请到 " + hunter_key.APPLY_URL + " 重新申请")

    hunter_key.save(plain)
    return await status(request)


@router.delete("/unlock")
async def clear(request: Request):
    _uid(request)
    if hunter_key.env_locked():
        raise HTTPException(409, "key 来自 .env，请改 .env 后重启容器")
    hunter_key.clear()
    return {"ok": True, "configured": False, "unlocked": False}
