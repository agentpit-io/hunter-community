"""用户自定义 MCP · CRUD + 测试 + 用量
API 前缀 /api/user_mcp/* · 走 JWT 中间件（普通用户 API）

对应 doc/codex/自定义MCP/01-方案总纲.md §3.2 API 契约
"""
from __future__ import annotations
import json
import re
import time
from typing import Optional

from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from app.services.database import get_conn
from app.services.mcp_crypto import encrypt, decrypt, key_hint


router = APIRouter(prefix="/user_mcp", tags=["user-mcp"])


# ═════════════════════════════════════════════════════════════════
# 通用工具
# ═════════════════════════════════════════════════════════════════

MAX_MCP_PER_USER = 10
_ALLOWED_TRANSPORT = {"sse", "http"}
_SLUG_RE = re.compile(r"^[a-z0-9_]{1,32}$")


def _uid(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, "需要登录")
    return str(uid)


def _slugify(name: str, user_id: str) -> str:
    """从 name 派生 slug · 冲突加数字后缀。"""
    base = re.sub(r"[^a-z0-9_]", "", name.lower().replace(" ", "_"))[:24] or "custom"
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT slug FROM user_mcp_registrations WHERE user_id=%s", (user_id,))
        taken = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()
    if base not in taken:
        return base
    for i in range(2, 100):
        cand = f"{base}{i}"[:32]
        if cand not in taken:
            return cand
    raise HTTPException(400, "slug 生成失败,请换个名字")


def _validate_endpoint(endpoint: str, transport: str) -> None:
    ep = (endpoint or "").strip()
    if not ep:
        raise HTTPException(400, "endpoint 不能为空")
    if not (ep.startswith("https://") or ep.startswith("http://")):
        raise HTTPException(400, "endpoint 必须是 https:// 或 http://")
    # SSRF 防护 · 拒绝内网地址
    lower = ep.lower()
    for bad in ("localhost", "127.", "10.", "172.", "192.168.", "169.254.", "0.0.0.0"):
        if bad in lower:
            raise HTTPException(400, f"endpoint 不允许内网地址({bad})")
    if transport not in _ALLOWED_TRANSPORT:
        raise HTTPException(400, f"transport 必须是 {'/'.join(_ALLOWED_TRANSPORT)}")


def _row_to_dict(row: tuple, include_encrypted: bool = False) -> dict:
    """DB 行转 JSON · 默认脱敏(不返 api_key_enc)。"""
    d = {
        "id":            row[0],
        "name":          row[2],
        "slug":          row[3],
        "transport":     row[4],
        "endpoint":      row[5],
        "headers":       row[6] or {},
        "api_key_hint":  row[8] or "",
        "enabled":       row[9],
        "timeout_ms":    row[10],
        "last_ok_at":    row[11].isoformat() if row[11] else None,
        "last_err":      row[12] or "",
        "call_count":    row[13],
        "error_count":   row[14],
        "created_at":    row[15].isoformat() if row[15] else None,
        "updated_at":    row[16].isoformat() if row[16] else None,
        "has_api_key":   bool(row[7]),
    }
    if include_encrypted:
        d["_api_key_enc"] = row[7]
    return d


_SELECT_COLS = ("id, user_id, name, slug, transport, endpoint, headers, api_key_enc, "
                "api_key_hint, enabled, timeout_ms, last_ok_at, last_err, "
                "call_count, error_count, created_at, updated_at")


# ═════════════════════════════════════════════════════════════════
# GET /api/user_mcp · 列出当前用户所有 MCP
# ═════════════════════════════════════════════════════════════════

@router.get("")
async def list_mcps(request: Request):
    uid = _uid(request)
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(f"SELECT {_SELECT_COLS} FROM user_mcp_registrations "
                    f"WHERE user_id=%s ORDER BY created_at DESC", (uid,))
        items = [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return {"items": items, "max": MAX_MCP_PER_USER}


# ═════════════════════════════════════════════════════════════════
# POST /api/user_mcp · 新建
# ═════════════════════════════════════════════════════════════════

class McpCreateIn(BaseModel):
    name: str
    transport: str = Field("sse", description="'sse' | 'http'")
    endpoint: str
    headers: dict = Field(default_factory=dict)
    api_key: Optional[str] = None


@router.post("")
async def create_mcp(body: McpCreateIn, request: Request):
    uid = _uid(request)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    if len(name) > 40:
        raise HTTPException(400, "名称最多 40 字")
    _validate_endpoint(body.endpoint, body.transport)

    # 上限
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT count(*) FROM user_mcp_registrations WHERE user_id=%s", (uid,))
        count = cur.fetchone()[0]
        if count >= MAX_MCP_PER_USER:
            raise HTTPException(400, f"最多注册 {MAX_MCP_PER_USER} 个 MCP · 请先删除不用的")

        slug = _slugify(name, uid)
        enc = encrypt(body.api_key or "")
        hint = key_hint(body.api_key or "")

        cur.execute(f"""
            INSERT INTO user_mcp_registrations
              (user_id, name, slug, transport, endpoint, headers, api_key_enc, api_key_hint)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            RETURNING {_SELECT_COLS}
        """, (uid, name, slug, body.transport, body.endpoint.strip(),
              json.dumps(body.headers or {}), enc, hint))
        row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    return {"ok": True, "item": _row_to_dict(row)}


# ═════════════════════════════════════════════════════════════════
# PATCH /api/user_mcp/{id} · 编辑
# ═════════════════════════════════════════════════════════════════

class McpPatchIn(BaseModel):
    name:      Optional[str] = None
    endpoint:  Optional[str] = None
    transport: Optional[str] = None
    headers:   Optional[dict] = None
    api_key:   Optional[str] = None  # 空串 = 保留旧 · None = 不改
    enabled:   Optional[bool] = None
    timeout_ms: Optional[int] = None


@router.patch("/{mcp_id}")
async def patch_mcp(mcp_id: int, body: McpPatchIn, request: Request):
    uid = _uid(request)
    fields, values = [], []
    if body.name is not None:
        n = body.name.strip()
        if not n or len(n) > 40:
            raise HTTPException(400, "名称需 1-40 字")
        fields.append("name = %s"); values.append(n)
    if body.endpoint is not None:
        transport = body.transport or _get_current_transport(uid, mcp_id)
        _validate_endpoint(body.endpoint, transport)
        fields.append("endpoint = %s"); values.append(body.endpoint.strip())
    if body.transport is not None:
        if body.transport not in _ALLOWED_TRANSPORT:
            raise HTTPException(400, f"transport 必须是 {'/'.join(_ALLOWED_TRANSPORT)}")
        fields.append("transport = %s"); values.append(body.transport)
    if body.headers is not None:
        fields.append("headers = %s::jsonb"); values.append(json.dumps(body.headers))
    if body.api_key is not None and body.api_key != "":
        # 空串保留旧 · 只有非空才更新
        fields.append("api_key_enc = %s"); values.append(encrypt(body.api_key))
        fields.append("api_key_hint = %s"); values.append(key_hint(body.api_key))
    if body.enabled is not None:
        fields.append("enabled = %s"); values.append(body.enabled)
    if body.timeout_ms is not None:
        fields.append("timeout_ms = %s"); values.append(max(1000, min(60000, int(body.timeout_ms))))

    if not fields:
        return {"ok": True}

    fields.append("updated_at = NOW()")

    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            f"UPDATE user_mcp_registrations SET {', '.join(fields)} "
            f"WHERE id=%s AND user_id=%s RETURNING {_SELECT_COLS}",
            (*values, mcp_id, uid),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "MCP 不存在或无权访问")
        # 若改了 endpoint/transport/key/headers · 清 tools cache
        if any(k in ('endpoint', 'transport', 'api_key', 'headers') for k, v in body.dict().items() if v is not None):
            cur.execute("DELETE FROM user_mcp_tools_cache WHERE mcp_id=%s", (mcp_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "item": _row_to_dict(row)}


def _get_current_transport(uid: str, mcp_id: int) -> str:
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT transport FROM user_mcp_registrations WHERE id=%s AND user_id=%s",
                    (mcp_id, uid))
        r = cur.fetchone()
    finally:
        conn.close()
    return r[0] if r else "sse"


# ═════════════════════════════════════════════════════════════════
# DELETE /api/user_mcp/{id}
# ═════════════════════════════════════════════════════════════════

@router.delete("/{mcp_id}")
async def delete_mcp(mcp_id: int, request: Request):
    uid = _uid(request)
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("DELETE FROM user_mcp_registrations WHERE id=%s AND user_id=%s",
                    (mcp_id, uid))
        n = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    if not n:
        raise HTTPException(404, "MCP 不存在")
    return {"ok": True}


# ═════════════════════════════════════════════════════════════════
# POST /api/user_mcp/{id}/test · 探活 · list_tools
# ═════════════════════════════════════════════════════════════════

@router.post("/{mcp_id}/test")
async def test_mcp(mcp_id: int, request: Request):
    """尝试连接 MCP · 调 list_tools · 返回连通性 + tool list 预览。"""
    uid = _uid(request)
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(f"SELECT {_SELECT_COLS} FROM user_mcp_registrations "
                    f"WHERE id=%s AND user_id=%s", (mcp_id, uid))
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "MCP 不存在")
    d = _row_to_dict(row, include_encrypted=True)
    api_key = decrypt(d.get("_api_key_enc"))

    t0 = time.time()
    try:
        tools = await _fetch_tools(d["transport"], d["endpoint"], d["headers"], api_key,
                                    d["timeout_ms"] or 15000)
        dur = int((time.time() - t0) * 1000)
        # 缓存
        _save_tools_cache(mcp_id, tools)
        _mark_ok(mcp_id)
        return {"ok": True, "duration_ms": dur, "tool_count": len(tools),
                "tools": tools[:20]}   # 预览前 20
    except Exception as e:
        dur = int((time.time() - t0) * 1000)
        # key 可能在 URL 里 → httpx 的报错带完整 URL,写库/回显前必须抹掉
        msg = _redact(e, api_key)[:200]
        _mark_err(mcp_id, msg)
        return {"ok": False, "duration_ms": dur,
                "error": f"{type(e).__name__}: {msg}"}


# ═════════════════════════════════════════════════════════════════
# POST /api/user_mcp/{id}/refresh · 强制刷 tools cache
# ═════════════════════════════════════════════════════════════════

@router.post("/{mcp_id}/refresh")
async def refresh_mcp(mcp_id: int, request: Request):
    return await test_mcp(mcp_id, request)


# ═════════════════════════════════════════════════════════════════
# GET /api/user_mcp/{id}/stats · 近 7 天用量
# ═════════════════════════════════════════════════════════════════

@router.get("/{mcp_id}/stats")
async def stats_mcp(mcp_id: int, request: Request):
    uid = _uid(request)
    conn = get_conn(); cur = conn.cursor()
    try:
        # 验归属
        cur.execute("SELECT 1 FROM user_mcp_registrations WHERE id=%s AND user_id=%s",
                    (mcp_id, uid))
        if not cur.fetchone():
            raise HTTPException(404, "MCP 不存在")
        # 近 7 天按天聚合
        cur.execute("""
            SELECT DATE_TRUNC('day', ts) AS day,
                   count(*) AS calls,
                   sum(CASE WHEN status='err' THEN 1 ELSE 0 END) AS errors,
                   avg(duration_ms)::int AS avg_ms
              FROM user_mcp_call_log
             WHERE mcp_id=%s AND ts >= NOW() - INTERVAL '7 days'
             GROUP BY day
             ORDER BY day
        """, (mcp_id,))
        rows = [{"date": r[0].isoformat()[:10],
                 "calls": r[1], "errors": r[2], "avg_ms": r[3] or 0}
                for r in cur.fetchall()]
        # 最近 20 条 log
        cur.execute("""
            SELECT tool_name, status, duration_ms, error_code, ts
              FROM user_mcp_call_log
             WHERE mcp_id=%s
             ORDER BY ts DESC LIMIT 20
        """, (mcp_id,))
        recent = [{"tool": r[0], "status": r[1], "ms": r[2],
                   "err": r[3] or "", "ts": r[4].isoformat()}
                  for r in cur.fetchall()]
    finally:
        conn.close()

    return {"days": rows, "recent": recent}


# ═════════════════════════════════════════════════════════════════
# 内部工具 · 拉 tools list + 状态标记
# ═════════════════════════════════════════════════════════════════

# ═════════════════════════════════════════════════════════════════
# MCP Streamable HTTP 协议
#
# 第一版实现是"直接 POST 一个 tools/list",对任何合规 MCP server 都会失败。
# 2026-08-10 实测 CoinGecko 官方 MCP 的两道拒绝:
#   1) 只发 Content-Type            → 406 "Client must accept both
#                                       application/json and text/event-stream"
#   2) 补上 Accept 但不握手          → 400 "Mcp-Session-Id header is required"
#
# 规范要求的完整流程:
#   initialize            → 响应 header 带回 Mcp-Session-Id
#   notifications/initialized  (通知, 无响应体)
#   tools/list / tools/call    每次都带 Mcp-Session-Id
#
# 且响应可能是 SSE 帧格式(event: message\ndata: {...}),不能直接 r.json()。
# ═════════════════════════════════════════════════════════════════

MCP_PROTOCOL_VERSION = "2025-06-18"
# 规范要求同时接受两种类型 —— 少一个就是 406
_MCP_ACCEPT = "application/json, text/event-stream"

# ── key 放 URL 而不是 header 的那一派 ──────────────────────────────
# 2026-08-10 实测:并不是所有 MCP server 都收 Authorization: Bearer。
#   Alpha Vantage  https://mcp.alphavantage.co/mcp?apikey=XXX   ← Bearer 返 401
#   Bargo          https://www.bargo.ai/mcp?token=swmcp_XXX     ← 两种都收
# 让用户自己把 key 拼进 endpoint 也行,但那样 key 会明文躺在 endpoint 列里
# (endpoint 不加密、UI 全量回显)。所以约定占位符:endpoint 里写 {API_KEY},
# 真 key 仍然进加密的 api_key 字段,发请求前才渲染进 URL。
_KEY_PLACEHOLDER = "{API_KEY}"


def _render_endpoint(endpoint: str, api_key: str) -> tuple[str, bool]:
    """把 endpoint 里的 {API_KEY} 换成真 key。

    返回 (实际请求 URL, key 是否已在 URL 里)。后者为 True 时不再加 Bearer ——
    有些 server 见到无效 Bearer 会直接 401,哪怕 URL 里的 key 是对的。
    """
    if not endpoint or _KEY_PLACEHOLDER not in endpoint:
        return endpoint, False
    return endpoint.replace(_KEY_PLACEHOLDER, quote(api_key or "", safe="")), True


def _redact(text: str, api_key: str) -> str:
    """抹掉错误文本里的明文 key。

    httpx 的 HTTPStatusError 消息带完整 URL —— key 在 URL 里时,这条错误会被
    _mark_err 原样写进 last_err,再被 UI 回显给用户。必须先洗。
    """
    s = str(text or "")
    if api_key:
        for form in (api_key, quote(api_key, safe="")):
            if form:
                s = s.replace(form, "***")
    return s


def _auth_headers(headers: dict, api_key: str) -> dict:
    h = dict(headers or {})
    if api_key and "Authorization" not in h and "X-API-Key" not in h:
        # 常见约定：Bearer 或 X-API-Key
        h["Authorization"] = f"Bearer {api_key}"
    h.setdefault("Content-Type", "application/json")
    h["Accept"] = _MCP_ACCEPT
    return h


def _parse_rpc(resp: httpx.Response) -> dict:
    """解 MCP 响应 —— 可能是纯 JSON,也可能是 SSE 帧。

    SSE 形如:
        event: message
        data: {"jsonrpc":"2.0","id":1,"result":{...}}
    取第一条能解析出 jsonrpc 的 data 行。
    """
    text = resp.text or ""
    ctype = (resp.headers.get("content-type") or "").lower()
    if "text/event-stream" in ctype or text.lstrip().startswith(("event:", "data:")):
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if not body or body == "[DONE]":
                continue
            try:
                d = json.loads(body)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and ("result" in d or "error" in d):
                return d
        raise ValueError(f"SSE 响应里没有可解析的 data 帧: {text[:200]}")
    return resp.json()


def _rpc_result(payload: dict) -> dict:
    """取 result;server 返回 error 时抛出,让上层记进 last_err。"""
    if payload.get("error"):
        err = payload["error"]
        raise RuntimeError(f"MCP error {err.get('code')}: {err.get('message')}")
    return payload.get("result") or {}


async def _mcp_session(c: httpx.AsyncClient, endpoint: str, h: dict) -> dict:
    """握手 · 返回带 Mcp-Session-Id 的 header 副本。

    不是所有 server 都要求 session(有些无状态实现直接接受 tools/list),
    但握手对它们无害 —— 所以一律先握手,拿不到 id 也继续。
    """
    init = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "hunter", "version": "1.0"},
        },
    }
    r = await c.post(endpoint, json=init, headers=h)
    r.raise_for_status()
    _rpc_result(_parse_rpc(r))          # 校验握手本身没报错

    sid = r.headers.get("mcp-session-id") or r.headers.get("Mcp-Session-Id")
    h2 = dict(h)
    if sid:
        h2["Mcp-Session-Id"] = sid
        # 规范要求握手后发一条 initialized 通知;通知无响应体,失败不致命
        try:
            await c.post(endpoint, headers=h2,
                         json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception as e:
            logger.debug("[user_mcp] initialized 通知失败(忽略): {}", e)
    return h2


async def _fetch_tools(transport: str, endpoint: str, headers: dict,
                        api_key: str, timeout_ms: int) -> list[dict]:
    """握手后拉 tool 列表。抛异常 = 失败(上层会写进 last_err)。

    sse 与 http 走同一套 Streamable HTTP —— 规范已把两者统一,
    区别只在服务端是否用 SSE 帧回包,而 _parse_rpc 两种都认。
    """
    if transport not in _ALLOWED_TRANSPORT:
        raise ValueError(f"unsupported transport: {transport}")

    url, key_in_url = _render_endpoint(endpoint, api_key)
    h = _auth_headers(headers, "" if key_in_url else api_key)
    async with httpx.AsyncClient(timeout=timeout_ms / 1000, follow_redirects=True) as c:
        h = await _mcp_session(c, url, h)
        r = await c.post(url, headers=h,
                         json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        r.raise_for_status()
        return _rpc_result(_parse_rpc(r)).get("tools", []) or []


async def _forward_call(transport: str, endpoint: str, headers: dict,
                          api_key: str, tool: str, args: dict,
                          timeout_ms: int) -> str:
    """转发 tool call · 返回文本结果。

    每次调用都重新握手:MCP session 是有状态的,跨请求复用要管生命周期与失效,
    当前调用量下不值得。握手成本约一次往返。
    """
    url, key_in_url = _render_endpoint(endpoint, api_key)
    h = _auth_headers(headers, "" if key_in_url else api_key)
    async with httpx.AsyncClient(timeout=timeout_ms / 1000, follow_redirects=True) as c:
        h = await _mcp_session(c, url, h)
        r = await c.post(url, headers=h, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        })
        r.raise_for_status()
        result = _rpc_result(_parse_rpc(r))

    # MCP 规范：result.content 是 list[{"type":"text","text":...}]
    content = result.get("content")
    if isinstance(content, list):
        texts = [x.get("text", "") for x in content
                 if isinstance(x, dict) and x.get("type") == "text"]
        if texts:
            return "\n".join(texts)
    return json.dumps(result, ensure_ascii=False)


def _save_tools_cache(mcp_id: int, tools: list[dict]) -> None:
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO user_mcp_tools_cache (mcp_id, tools, fetched_at)
            VALUES (%s, %s::jsonb, NOW())
            ON CONFLICT (mcp_id) DO UPDATE SET
              tools = EXCLUDED.tools, fetched_at = NOW()
        """, (mcp_id, json.dumps(tools)))
        conn.commit()
    except Exception as e:
        logger.warning("[user_mcp] cache write failed: {}", e)
    finally:
        conn.close()


def _mark_ok(mcp_id: int) -> None:
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("UPDATE user_mcp_registrations SET last_ok_at=NOW(), last_err='' "
                    "WHERE id=%s", (mcp_id,))
        conn.commit()
    finally:
        conn.close()


def _mark_err(mcp_id: int, err: str) -> None:
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("UPDATE user_mcp_registrations SET last_err=%s, "
                    "error_count=error_count+1 WHERE id=%s", (err[:400], mcp_id))
        conn.commit()
    finally:
        conn.close()
