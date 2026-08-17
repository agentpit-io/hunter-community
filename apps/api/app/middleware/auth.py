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
            # ⚠️ 白名单在这里就 return 了 —— 下面那段设 user_id 的代码走不到。
            #
            # 「免登录可访问」不等于「不认识用户」。这两件事被混为一谈,
            # 造成了同一个 bug 的两个实例:
            #   · `/api/internal/*` —— 聊天问股价时 source_resolver 看到
            #     user_id=None,永远走官方源
            #   · `/api/catalog/*`  —— 能力库页拿不到"你自己的"那组,
            #     用户明明加了数据源,列表里却还是"你还没有接自己的数据源"
            # 两处都不报错、不告警。
            #
            # 所以这里做**可选身份识别**:能认出来就认,认不出照常放行。
            _bind_optional_identity(request, path)
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
        # 同一个 user_id 也挂到 contextvar 上(`_21` §6.2)。
        # 为什么两处都要:`request.state` 只有拿得到 request 对象的地方能读,
        # 而取数发生在 `finance_data_client` 那十几个**模块级同步函数**里,
        # 它们没有 request。contextvar 是一处设置、全链路可见 ——
        # 挨个加 user_id 参数的话,漏掉一处的表现是"这个功能不认用户的
        # 数据源"且不报错,又是一次静默失败。
        try:
            from app.services import request_ctx
            request_ctx.set_user(payload["sub"])
            request_ctx.begin_provenance()
        except Exception:      # noqa: BLE001 — 取数出处是增强,不能让它挡住请求
            pass
        return await call_next(request)


def _bind_optional_identity(request: Request, path: str) -> None:
    """免登录路径上的**可选身份识别** —— 认得出就认,认不出照常放行。

    两个来源,对应两类调用方:

      · `/api/internal/*` —— MCP 桥注入的 `X-Hunter-User-Id`。
        **不做鉴权**:鉴权仍由各 internal 路由自己的共享 secret 负责。
        伪造这个 header 没有新增暴露面 —— 那些路由本来就用它取数据,
        伪造的前提是已经拿到了共享 secret。

      · 其余公开路径 —— 浏览器带的 Bearer token。校验**照常做**(走同一个
        `_verify`),只是校验失败不拒绝请求,而是当匿名处理。
        `/api/catalog/*` 就是这一类:它是公开的("这套部署能拿到什么数据"
        谁都能看),但登录了就该多看到"你自己接的那些"。

    识别不到时记 debug 日志。**这条日志很重要**:识别不到的表现是
    "我明明加了数据源,列表里却说我没加",而这个原因从现象上完全看不出来。
    """
    uid = None
    try:
        if path.startswith("/api/internal/"):
            uid = request.headers.get("X-Hunter-User-Id", "").strip() or None
        else:
            token = _extract_token(request)
            if token:
                payload = _verify(token)
                if payload and payload.get("type") in ("access", None):
                    uid = payload.get("sub")

        # request.state 与 contextvar 两处都设:前者给能拿到 request 的
        # handler(如 /catalog/sources),后者给取数层那些模块级同步函数
        request.state.user_id = uid
        from app.services import request_ctx
        request_ctx.set_user(uid)
        request_ctx.begin_provenance()
        if not uid:
            logger.debug("[auth] 公开路径未识别到用户 path={} "
                         "· 用户自定义数据源不会出现在结果里", path)
    except Exception as e:      # noqa: BLE001 — 绝不能让它挡住公开请求
        logger.warning("[auth] 可选身份识别失败(已忽略): {}", e)


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
