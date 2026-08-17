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
            # ⚠️ `/api/internal/*` 在白名单里,**在这里就 return 了** ——
            # 下面那段设 user_id 的代码根本走不到。
            #
            # 这正是聊天里问股价拿不到用户数据源的原因:MCP 工具走的就是
            # 这条路。身份其实**送到了**(hunter-mcp-context plugin 注入的
            # X-Hunter-User-Id),只是没人把它转给 contextvar,于是
            # source_resolver 看到 user_id=None,永远直接走官方源 ——
            # 不报错、不告警,又一次静默失败。
            #
            # 放在中间件里而不是四个 internal 路由各加一行:那四份 `_auth`
            # 本来就是同一件事抄了四遍,再抄第五遍只会让下一个新增的
            # internal 路由继续漏掉。这里一处覆盖全部,包括以后新加的。
            _bind_internal_identity(request, path)
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


def _bind_internal_identity(request: Request, path: str) -> None:
    """把 MCP 桥带进来的 `X-Hunter-User-Id` 转成 contextvar。

    只对 `/api/internal/*` 生效。**不做鉴权** —— 鉴权仍由各 internal 路由
    自己的共享 secret 校验负责,这里只是把已经送到的身份挂上去。
    即使 header 是伪造的也没有新增暴露面:那些路由本来就用这个 header
    取数据,伪造它的前提是已经拿到了共享 secret。

    header 缺失时**显式记一条日志**。缺失的表现是"用户配了数据源但
    聊天里用不上",而这个原因从现象上完全看不出来 —— 必须在日志里留痕。
    """
    if not path.startswith("/api/internal/"):
        return
    try:
        from app.services import request_ctx
        uid = request.headers.get("X-Hunter-User-Id", "").strip()
        request_ctx.set_user(uid or None)
        request_ctx.begin_provenance()
        if not uid:
            logger.debug("[auth] internal 请求无 X-Hunter-User-Id path={} "
                         "· 用户自定义数据源将不参与本次取数", path)
    except Exception as e:      # noqa: BLE001 — 绝不能让它挡住内部调用
        logger.warning("[auth] 绑定 internal 身份失败(已忽略): {}", e)


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
