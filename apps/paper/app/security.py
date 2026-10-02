"""两道**只做拒绝、不做放行**的守卫。

1. **服务间鉴权**：`X-Hunter-Internal-Key` 必须等于 `HUNTER_INTERNAL_KEY`，否则 401。
   这是 `paper` 唯一的门 —— 它绑 `127.0.0.1`、只由 `api` / `fin-worker` 在 docker
   网络内调用（`08 §3.2`）。

2. **实盘字段守卫**：请求（body 或 query）里出现 `live` / `real` / `broker` /
   `account_no` 之类字段就 **报错**，不是忽略（`08 §八-1`）。忽略等于「用户以为切到
   实盘了、系统当作没看见」，比报错危险得多。

`/healthz` 是唯一豁免鉴权的路径：容器健康检查跑在容器内、拿不到密钥，而它只回
「进程活着 + 模式是不是 PAPER」，不含任何账本数据。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request

from app import config

# 健康检查路径：唯一不需要内部口令的端点（见模块文档）。
PUBLIC_PATHS = frozenset({"/healthz"})


def require_internal_key(request: Request) -> None:
    """FastAPI 依赖：挂在 app 上，对所有路由生效（`/healthz` 除外）。"""
    if request.url.path in PUBLIC_PATHS:
        return
    expected = config.internal_key()
    got = request.headers.get(config.INTERNAL_KEY_HEADER, "")
    # 口令没配时**一律拒绝**：没配 = 没人能通过，而不是「谁都能过」。
    if not expected or got != expected:
        raise HTTPException(status_code=401, detail="internal auth failed")


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace("-", "_")


def _scan_keys(keys) -> list[str]:
    return sorted({k for k in keys if _normalize_key(str(k)) in config.LIVE_FIELD_DENYLIST})


def _scan_json(value: Any, found: list[str]) -> None:
    """递归扫描 JSON：实盘字段可能藏在 `{"order": {"broker": ...}}` 里。"""
    if isinstance(value, dict):
        found.extend(_scan_keys(value.keys()))
        for v in value.values():
            _scan_json(v, found)
    elif isinstance(value, list):
        for v in value:
            _scan_json(v, found)


class LiveFieldGuard:
    """纯 ASGI 中间件：查 query 与 JSON body 里的实盘字段，命中即 400。

    写成纯 ASGI 而不是 `BaseHTTPMiddleware`，是因为后者消费 body 后下游拿到的是
    空流（Starlette 的已知行为）。这里把 body 读出来、再包一个 `receive` 原样还回去，
    下游看到的字节与原始请求完全一致。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # ① query string
        from urllib.parse import parse_qsl

        raw_qs = (scope.get("query_string") or b"").decode("utf-8", "replace")
        q_keys = [k for k, _ in parse_qsl(raw_qs, keep_blank_values=True)]
        bad = _scan_keys(q_keys)
        if bad:
            return await self._reject(send, bad)

        method = scope.get("method", "GET").upper()
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            body = b""
            more = True
            while more:
                msg = await receive()
                if msg["type"] != "http.request":
                    break
                body += msg.get("body", b"")
                more = msg.get("more_body", False)

            ctype = ""
            for name, value in scope.get("headers", []):
                if name == b"content-type":
                    ctype = value.decode("latin-1")
            if "application/json" in ctype and body.strip():
                try:
                    parsed = json.loads(body)
                except (ValueError, UnicodeDecodeError):
                    parsed = None  # 不是合法 JSON：交给下游按它自己的规则报 422
                if parsed is not None:
                    found: list[str] = []
                    _scan_json(parsed, found)
                    if found:
                        return await self._reject(send, sorted(set(found)))

            replayed = False

            async def replay():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            return await self.app(scope, replay, send)

        return await self.app(scope, receive, send)

    async def _reject(self, send, fields: list[str]) -> None:
        payload = json.dumps(
            {
                "detail": (
                    f"请求携带了实盘相关字段 {fields}，已拒绝。"
                    "本服务固定 PAPER 模式，不接实盘、不配置任何交易凭证（01方案 §7.4）。"
                ),
                "live_fields": fields,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 400,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(payload)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})
