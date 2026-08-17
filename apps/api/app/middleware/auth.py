"""Local JWT auth middleware · Hunter Community P3.

Verifies `Authorization: Bearer <JWT>` locally via app.routers.auth.verify_jwt.
No external calls. Public paths and prefixes below are the only routes that
skip auth · everything else under /api/ requires a valid access token.
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from loguru import logger


# Exact-match public paths
_PUBLIC_PATHS = {
    "/api/health",
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/refresh",
    "/api/auth/logout",
    "/api/auth/status",
    "/api/auth/local-session",   # single-user mode · see routers/auth.py
    "/api/agent/health",
}

# Prefix-match public paths · public market data + public-share endpoints only
_PUBLIC_PREFIXES = (
    "/api/kpred/",
    "/api/quote/", "/api/kline/", "/api/news/", "/api/fundflow/",
    "/api/orderbook/", "/api/financial/", "/api/timeshare/",
    "/api/signals/",   # macro signal dashboard is public data
    "/api/v1/signal/report/",
    "/api/online-analysis/stream/",
    "/api/online-analysis/search-stock",
    "/api/online-analysis/check-stock",
    # Global markets (US/HK) public data
    "/api/gm/quote/", "/api/gm/quotes",
    "/api/gm/kline/", "/api/gm/discover/",
    "/api/gm/kpred/", "/api/gm/news/",
    "/api/gm/research/",
    "/api/gm/scout/",
    "/api/gm/analysts/",
    "/api/gm/fundamentals/",
    "/api/geo/",
    "/api/backtest/accuracy", "/api/backtest/consistency",
    "/api/backtest/reversals", "/api/backtest/evolution/",
    # Anonymous artifact share links
    "/api/public/artifacts/",
    "/api/public/chat_debate/stream/",
    "/api/public/chat_kpred/stream/",
    "/api/chat/skills/detail/",
    # Capability catalog · 只描述"这套部署能拿到什么数据",不含任何凭证
    # (只回 configured=true/false,不回 key 本身;endpoint 路径本来就在开源代码里)
    "/api/catalog/",
    # 量化策略 · 因子/官方策略公开可看 · 我的策略 handler 内部再校 uid
    # (endpoint 本身在公开代码里 · 无凭证)
    "/api/quant/",
    # 数据源「来源模板」· 只是一张"我们支持接哪些来源"的静态清单,不含任何凭证。
    # 免登录是有意的:用户在决定要不要用这个开源版时,
    # 「它能接我手上的 Tushare 吗」是个先决问题,不该先逼他注册。
    # ⚠️ 只放这一条路径 —— `/api/user_sources` 其余端点(CRUD/test)
    # 都带用户凭证,必须登录,所以**不能**把 "/api/user_sources/" 整个放进来
    "/api/user_sources/templates",
    # Internal MCP bridge · shared-secret authenticated separately
    "/api/internal/",
)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in _PUBLIC_PATHS or not path.startswith("/api/"):
            return await call_next(request)
        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        token = _extract_token(request)
        client_host = request.client.host if request.client else "?"
        if not token:
            logger.warning(
                "[auth] 401 UNAUTHORIZED path={} method={} client={}",
                path, request.method, client_host,
            )
            return JSONResponse(
                {"error": "UNAUTHORIZED", "needLogin": True}, status_code=401
            )

        payload = _verify(token)
        if not payload or payload.get("type") not in ("access", None):
            # `type` was not set by the old signer · accept legacy tokens too until they naturally expire
            logger.warning(
                "[auth] 401 INVALID_TOKEN path={} method={} client={} token_prefix={}",
                path, request.method, client_host, token[:12],
            )
            return JSONResponse(
                {"error": "INVALID_TOKEN", "needLogin": True}, status_code=401
            )

        request.state.user_id = payload["sub"]
        request.state.user_role = payload.get("role", "user")
        return await call_next(request)


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _verify(token: str) -> dict | None:
    try:
        from app.routers.auth import verify_jwt
        return verify_jwt(token)
    except Exception as e:
        logger.warning("verify_jwt error: {}", e)
    return None
