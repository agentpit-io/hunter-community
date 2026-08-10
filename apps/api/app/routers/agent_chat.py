"""Agent Chat V2 · 主对话 SSE 端点。

POST /api/agent/chat/stream — 主对话流

详见 doc/codex/03-开发需求与技术方案.md §3.2
"""
from __future__ import annotations
import json
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel

from app.services.agent.orchestrator import ChatOrchestrator
from app.services.agent.stream_bus import SSEEvent

router = APIRouter(prefix="/agent", tags=["agent-chat-v2"])


class ChatIn(BaseModel):
    session_id: Optional[str] = None
    stock_code: Optional[str] = None
    stock_name: Optional[str] = None
    query: str
    client_capabilities: dict = {}


# ─────────────────────────────── helpers ───────────────────────────────
def _new_session_id() -> str:
    return f"sess_{uuid.uuid4().hex[:16]}"


def _get_or_create_session(session_id: Optional[str], user_id: str,
                            stock_code: str, stock_name: str) -> str:
    """轻量版：若 session_id 无效则新建。复用 assistant_sessions 表。"""
    from app.services.database import get_conn
    if session_id:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT id FROM assistant_sessions WHERE id = %s AND user_id = %s",
                    (session_id, user_id))
        row = cur.fetchone()
        conn.close()
        if row:
            return session_id
    # 新建
    sid = _new_session_id()
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO assistant_sessions (id, user_id, focus_stock_code, focus_stock_name)
            VALUES (%s, %s, %s, %s)
        """, (sid, user_id, stock_code or "", stock_name or ""))
        conn.commit()
    finally:
        conn.close()
    return sid


def _load_history(session_id: str, limit: int = 8) -> list[dict]:
    """加载最近 N 条消息（正序）"""
    from app.services.database import get_conn
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT role, content FROM assistant_messages
            WHERE session_id = %s ORDER BY created_at DESC LIMIT %s
        """, (session_id, limit))
        rows = cur.fetchall()
    finally:
        conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]


# ─────────────────────────────── SSE 心跳 ───────────────────────────────
KEEPALIVE_INTERVAL_SEC = 15


# ─────────────────────────────── endpoint ───────────────────────────────
@router.post("/chat/stream")
async def chat_stream(body: ChatIn, request: Request):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(400, "请输入问题")
    if len(query) > 500:
        raise HTTPException(400, "问题过长（≤500 字）")

    session_id = _get_or_create_session(
        body.session_id, user_id,
        body.stock_code or "", body.stock_name or "",
    )
    history = _load_history(session_id, limit=8)

    orch = ChatOrchestrator(
        user_id=user_id, session_id=session_id,
        stock_code=body.stock_code, stock_name=body.stock_name,
    )

    async def _stream():
        # 首行心跳注释，通过某些中间层的 buffer
        yield b": ok\n\n"
        try:
            async for evt in orch.run(query, history):
                if await request.is_disconnected():
                    logger.info("[agent_chat] client disconnected sid={}", session_id)
                    break
                yield evt.encode()
        except Exception as e:
            logger.exception("[agent_chat] 未处理异常: {}", e)
            err_evt = SSEEvent("error", {
                "code": "INTERNAL",
                "message": f"服务器内部错误: {type(e).__name__}",
                "recoverable": False,
            })
            yield err_evt.encode()

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",   # nginx 关闭 buffer
        "Content-Type": "text/event-stream; charset=utf-8",
    }
    return StreamingResponse(_stream(), media_type="text/event-stream", headers=headers)


@router.get("/session/{session_id}")
async def get_session_detail(session_id: str, request: Request, limit: int = 50):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")
    from app.services.database import get_conn
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, focus_stock_code, focus_stock_name, message_count, last_message_at
            FROM assistant_sessions WHERE id = %s AND user_id = %s
        """, (session_id, user_id))
        srow = cur.fetchone()
        if not srow:
            raise HTTPException(404, "session 不存在")
        cur.execute("""
            SELECT id, role, content, intent, extra, created_at
            FROM assistant_messages WHERE session_id = %s
            ORDER BY created_at DESC LIMIT %s
        """, (session_id, limit))
        rows = cur.fetchall()
    finally:
        conn.close()

    messages = []
    for r in reversed(rows):
        extra_val = r[4]
        if isinstance(extra_val, str):
            try:
                extra_val = json.loads(extra_val)
            except Exception:
                extra_val = {}
        messages.append({
            "id": r[0], "role": r[1], "content": r[2],
            "intent": r[3], "extra": extra_val or {},
            "created_at": r[5].isoformat() if r[5] else None,
        })
    return {
        "session_id": srow[0], "focus_stock_code": srow[1],
        "focus_stock_name": srow[2], "message_count": srow[3],
        "last_message_at": srow[4].isoformat() if srow[4] else None,
        "messages": messages,
    }


@router.get("/health")
async def health():
    """无鉴权（由 middleware whitelist 处理）· 检查网关连通"""
    from app.services.agent.tool_registry import ToolRegistry, load_all_tools
    load_all_tools()
    return {"ok": True, "tools": ToolRegistry.known_tools()}


class FeedbackIn(BaseModel):
    session_id: str
    message_ts: int              # 前端 msg.ts（用于关联具体一条 assistant 消息）
    positive: bool
    reason: str | None = None    # 可选文本


@router.post("/feedback")
async def submit_feedback(payload: FeedbackIn, request: Request):
    """用户对某条 assistant 回复的 👍/👎 反馈。埋点 + 落 Redis List 供人工看板。"""
    from loguru import logger
    from app.services.agent.alert import _redis as _r    # 复用 alert 里的 redis 连接
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")
    logger.info("[agent_v2] event=feedback user_id={} session_id={} positive={} reason={}",
                user_id, payload.session_id, payload.positive, (payload.reason or "")[:80])
    if _r is not None:
        try:
            key = f"agent:feedback:{'up' if payload.positive else 'down'}"
            _r.lpush(key, json.dumps({
                "user_id": user_id, "session_id": payload.session_id,
                "message_ts": payload.message_ts, "reason": payload.reason,
                "at": _now_iso(),
            }, ensure_ascii=False))
            _r.ltrim(key, 0, 999)  # 保留最近 1000 条
        except Exception:
            pass
    return {"ok": True}


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
