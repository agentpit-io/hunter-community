"""/chat 会话归属 —— 用户隔离的权威数据源。

背景:opencode 的 session 按 project(目录)分组,没有"用户"概念,
      `GET /session` 谁调都返回全部。直接暴露给前端 = 所有人看到彼此的对话。

做法:不动 opencode 一行(改它要动 packages/*,触碰 fork 治理红线),
      在我们这层维护 session ↔ user 映射,由 web BFF 调用本模块做过滤与鉴权。

调用方:web/app/api/opencode/[...path]/route.ts

⚠️ 鉴权必须在服务端完成 —— 前端过滤只是"看不见",直接调 API 照样拿得到别人的数据。
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services.database import get_conn

log = logging.getLogger(__name__)
router = APIRouter()


def _uid(request: Request) -> str:
    """当前登录用户。middleware 已验签 JWT 并注入 request.state.user_id。"""
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, "需要登录")
    return str(uid)


# ── 归属读写 ────────────────────────────────────────────

def _owned_ids(user_id: str) -> list[str]:
    c = get_conn(); cur = c.cursor()
    cur.execute("""SELECT session_id FROM chat_session_owner
                   WHERE user_id = %s AND NOT archived
                   ORDER BY last_used_at DESC""", (user_id,))
    out = [r[0] for r in cur.fetchall()]
    c.close()
    return out


def _owns(user_id: str, session_id: str) -> bool:
    c = get_conn(); cur = c.cursor()
    cur.execute("""SELECT 1 FROM chat_session_owner
                   WHERE session_id = %s AND user_id = %s AND NOT archived""",
                (session_id, user_id))
    ok = cur.fetchone() is not None
    c.close()
    return ok


@router.get("/chat/sessions")
async def my_sessions(request: Request):
    """我拥有的 session id 列表。BFF 用它过滤 opencode 返回的全量会话。"""
    uid = _uid(request)
    try:
        ids = _owned_ids(uid)
    except Exception as e:
        log.warning("[chat] 读会话归属失败: %s", e)
        raise HTTPException(503, "会话数据暂不可用")
    return {"user_id": uid, "session_ids": ids, "count": len(ids)}


class ClaimIn(BaseModel):
    session_id: str
    title: str = ""


@router.post("/chat/sessions")
async def claim_session(body: ClaimIn, request: Request):
    """登记归属。BFF 在 opencode 建完 session 后立即调用。

    ON CONFLICT DO NOTHING:同一个 session 只能属于第一个认领的人,
    防止 B 通过重复认领把 A 的会话据为己有。
    """
    uid = _uid(request)
    sid = (body.session_id or "").strip()
    if not sid:
        raise HTTPException(400, "session_id 不能为空")
    c = get_conn(); cur = c.cursor()
    cur.execute("""INSERT INTO chat_session_owner (session_id, user_id, title)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (session_id) DO NOTHING""",
                (sid, uid, (body.title or "")[:200]))
    created = cur.rowcount > 0
    c.commit(); c.close()
    return {"ok": True, "session_id": sid, "created": created}


class TouchIn(BaseModel):
    title: str | None = None


@router.patch("/chat/sessions/{session_id}")
async def touch_session(session_id: str, body: TouchIn, request: Request):
    """更新最后使用时间 / 标题。非本人的直接 403。"""
    uid = _uid(request)
    if not _owns(uid, session_id):
        raise HTTPException(403, "无权访问该对话")
    c = get_conn(); cur = c.cursor()
    if body.title is not None:
        cur.execute("""UPDATE chat_session_owner
                       SET title = %s, last_used_at = NOW()
                       WHERE session_id = %s AND user_id = %s""",
                    (body.title[:200], session_id, uid))
    else:
        cur.execute("""UPDATE chat_session_owner SET last_used_at = NOW()
                       WHERE session_id = %s AND user_id = %s""", (session_id, uid))
    c.commit(); c.close()
    return {"ok": True}


@router.get("/chat/sessions/{session_id}/owned")
async def check_owned(session_id: str, request: Request):
    """BFF 在转发 /session/{id}/* 前调用。"""
    uid = _uid(request)
    return {"owned": _owns(uid, session_id), "user_id": uid}


@router.delete("/chat/sessions/{session_id}")
async def release_session(session_id: str, request: Request):
    """归档归属记录(软删)。opencode 侧的删除由 BFF 转发完成。"""
    uid = _uid(request)
    if not _owns(uid, session_id):
        raise HTTPException(403, "无权访问该对话")
    c = get_conn(); cur = c.cursor()
    cur.execute("""UPDATE chat_session_owner SET archived = TRUE
                   WHERE session_id = %s AND user_id = %s""", (session_id, uid))
    c.commit(); c.close()
    return {"ok": True}


# ── 存量会话认领(一次性 · 仅管理员) ────────────────────

class AdoptIn(BaseModel):
    session_ids: list[str]


@router.post("/chat/sessions/adopt")
async def adopt_orphans(body: AdoptIn, request: Request):
    """把无主的历史会话认领到当前管理员名下。

    上线前跑一次:生产库里 59 条会话全是老板和我们的测试数据,
    不认领的话它们会变成"谁也看不见"的孤儿(不影响功能,但排查时不方便)。
    """
    from app.routers.backtest import _require_admin
    _require_admin(request)          # 与展位后台、回测配置同一口径
    uid = _uid(request)
    if not body.session_ids:
        return {"ok": True, "adopted": 0}
    c = get_conn(); cur = c.cursor()
    n = 0
    for sid in body.session_ids[:500]:
        cur.execute("""INSERT INTO chat_session_owner (session_id, user_id, title)
                       VALUES (%s, %s, '')
                       ON CONFLICT (session_id) DO NOTHING""", (sid.strip(), uid))
        n += cur.rowcount
    c.commit(); c.close()
    return {"ok": True, "adopted": n}
