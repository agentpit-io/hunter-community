"""用户自定义 MCP · Bridge 内部端点
供 /opt/opencode-mcp/hunter_user_mcp.py 反调
- GET  /api/internal/user_mcp/tools_bundle · 按 user_id 拉所有 tool
- POST /api/internal/user_mcp/call · 转发 tool call

复用 user_mcp.py 的 _fetch_tools / _forward_call · 加密 API key 解密只在这层做
"""
from __future__ import annotations
import json
import os
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from app.services.database import get_conn
from app.services.mcp_crypto import decrypt
from app.routers.user_mcp import (_fetch_tools, _forward_call, _redact,
                                  _SELECT_COLS, _row_to_dict)


router = APIRouter(prefix="/internal/user_mcp", tags=["user-mcp-bridge"])

_INTERNAL_KEY = os.getenv("HUNTER_INTERNAL_KEY", "hunter-internal-2026")

# 缓存过期时间（tools cache 内存优先，DB 兜底）
_TOOLS_CACHE_TTL = 900  # 15 min


def _auth(request: Request) -> str:
    """验共享 secret · 返回 user_id（从 X-Hunter-User-Id header 拿）· 失败 401。"""
    key = request.headers.get("X-Hunter-Internal-Key", "")
    if key != _INTERNAL_KEY:
        raise HTTPException(401, "internal auth failed")
    user_id = request.headers.get("X-Hunter-User-Id", "").strip()
    return user_id  # 允许空 · 交由端点决定


# ═════════════════════════════════════════════════════════════════
# GET /api/internal/user_mcp/tools_bundle
# ═════════════════════════════════════════════════════════════════

@router.get("/tools_bundle")
async def tools_bundle(request: Request):
    uid = _auth(request)
    if not uid:
        return {"mcps": []}
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(f"""
            SELECT {_SELECT_COLS} FROM user_mcp_registrations
             WHERE user_id=%s AND enabled = TRUE
             ORDER BY id
        """, (uid,))
        mcps = cur.fetchall()
        # 拉 cache
        cur.execute("""
            SELECT mcp_id, tools, EXTRACT(EPOCH FROM (NOW() - fetched_at))::int AS age
              FROM user_mcp_tools_cache
             WHERE mcp_id = ANY(%s)
        """, ([m[0] for m in mcps],))
        cache_map = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
    finally:
        conn.close()

    result = []
    for row in mcps:
        d = _row_to_dict(row, include_encrypted=True)
        mcp_id = d["id"]
        tools = []
        cached = cache_map.get(mcp_id)
        if cached and cached[1] < _TOOLS_CACHE_TTL:
            tools = cached[0] or []
        else:
            # cache miss / expired · 异步刷（本次请求快返回，后台单独刷）
            # 简化：本次同步刷 · 若太慢再改异步
            api_key = ""   # 先占位:decrypt 自身失败时 except 里也要能用
            try:
                api_key = decrypt(d.get("_api_key_enc"))
                tools = await _fetch_tools(d["transport"], d["endpoint"], d["headers"],
                                            api_key, d["timeout_ms"] or 15000)
                # 写 cache
                cur = get_conn().cursor()
                try:
                    cur.execute("""
                        INSERT INTO user_mcp_tools_cache (mcp_id, tools, fetched_at)
                        VALUES (%s, %s::jsonb, NOW())
                        ON CONFLICT (mcp_id) DO UPDATE SET
                          tools = EXCLUDED.tools, fetched_at = NOW()
                    """, (mcp_id, json.dumps(tools)))
                    cur.connection.commit()
                finally:
                    cur.connection.close()
            except Exception as e:
                logger.warning("[bridge] fetch tools failed for {}: {}",
                               d["slug"], _redact(e, api_key))
                tools = []

        result.append({
            "slug": d["slug"],
            "display_name": d["name"],
            "tools": tools[:30],   # 单 MCP 最多 30 tool
        })

    return {"mcps": result}


# ═════════════════════════════════════════════════════════════════
# POST /api/internal/user_mcp/call · 转发 tool 调用
# ═════════════════════════════════════════════════════════════════

class CallIn(BaseModel):
    slug: str
    tool: str
    args: dict = {}


@router.post("/call")
async def call_tool(body: CallIn, request: Request):
    uid = _auth(request)
    if not uid:
        return {"error": "not authenticated"}

    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(f"""
            SELECT {_SELECT_COLS} FROM user_mcp_registrations
             WHERE user_id=%s AND slug=%s AND enabled=TRUE
        """, (uid, body.slug))
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return {"error": f"MCP '{body.slug}' 不存在或已禁用"}

    d = _row_to_dict(row, include_encrypted=True)
    mcp_id = d["id"]
    api_key = decrypt(d.get("_api_key_enc"))

    t0 = time.time()
    status, error_code = "ok", None
    try:
        text = await _forward_call(d["transport"], d["endpoint"], d["headers"],
                                    api_key, body.tool, body.args or {},
                                    d["timeout_ms"] or 15000)
        result = {"type": "user_mcp_result", "text": text}
    except Exception as e:
        status = "err"; error_code = type(e).__name__
        # key 可能内嵌在 endpoint 的 {API_KEY} 占位符里 → 报错带 URL,回给模型前先抹
        text = json.dumps({"error": f"{type(e).__name__}: {_redact(e, api_key)[:200]}"},
                          ensure_ascii=False)
        result = {"type": "user_mcp_error", "text": text}
    finally:
        dur = int((time.time() - t0) * 1000)
        # 埋点
        try:
            conn = get_conn(); cur = conn.cursor()
            cur.execute("""
                INSERT INTO user_mcp_call_log (user_id, mcp_id, tool_name, status, duration_ms, error_code)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (uid, mcp_id, body.tool, status, dur, error_code))
            cur.execute("UPDATE user_mcp_registrations SET call_count=call_count+1 WHERE id=%s",
                        (mcp_id,))
            conn.commit(); conn.close()
        except Exception as e:
            logger.warning("[bridge] log write failed: {}", e)

    return result
