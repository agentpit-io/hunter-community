"""AgentPit token 鉴权中间件。

从请求 Header 的 Authorization: Bearer <token> 取出 token，
调 AgentPit verify-token 接口验证并解析 user_id，注入到 request.state.user_id。
"""
import os
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from loguru import logger

# 不需要鉴权的路径（市场数据公开，用户数据需要鉴权）
_PUBLIC_PATHS = {"/api/health", "/api/hermes/feishu/status", "/api/auth/login",
                 "/api/auth/status", "/api/auth/agentpit-sso",
                 "/api/push/templates", "/api/feishu/event",
                 "/api/ax/register",              # AdventureX 老版注册接口
                 "/api/ax/register-and-analyze",  # V4：新用户此时尚无 token
                 "/api/agent/health"}             # Agent Chat V2 探活

# 不需要鉴权的路径前缀（行情/K线/新闻/资金流向是公共数据）
_PUBLIC_PREFIXES = ("/api/wx/",  # 微信 OAuth 绑定流程，无需鉴权
                    "/api/kpred/",  # K线预测为市场公开数据，agentpit服务端直调无JWT
                    "/api/quote/", "/api/kline/", "/api/news/", "/api/fundflow/",
                    "/api/orderbook/", "/api/financial/", "/api/timeshare/",
                    "/api/push/logs/public/",
                    "/api/sso/",  # 推送详情公开，供微信用户无登录查看
                    "/api/v1/signal/report/",  # 信号报告 HTML，微信消息链接无需登录
                    "/api/online-analysis/stream/",   # SSE 流：EventSource 不支持自定义 header，task_id 随机不可猜
                    "/api/online-analysis/search-stock",  # 公共股票名搜索，无需鉴权
                    "/api/online-analysis/check-stock",   # AdventureX intake 检查按钮，此时新用户无 token
                    "/api/gm/quote/", "/api/gm/quotes",   # 美港股行情为公开市场数据
                    "/api/gm/kline/", "/api/gm/discover/",  # 美港股K线/发现榜单同上
                    "/api/gm/kpred/", "/api/gm/news/",    # 择时信号/新闻同为公开市场数据
                    "/api/gm/research/",                  # 深度研究(结果缓存, 无用户数据)
                    "/api/gm/scout/",                     # 官方公告为公开数据
                    "/api/gm/analysts/",                  # 分析师评级为公开数据
                    "/api/gm/fundamentals/",              # 港股财务摘要为公开数据
                    "/api/geo/",                          # 地缘风险总览为公开市场数据
                    "/api/backtest/accuracy", "/api/backtest/consistency",
                    "/api/backtest/reversals", "/api/backtest/evolution/",  # 回测统计为模型公开指标
                    "/api/public/artifacts/",             # Artifact 匿名访问 · Claude 风公开链接
                    "/api/public/chat_debate/stream/",    # 多专家辩论 SSE · task_id 随机不可枚举
                    "/api/public/chat_kpred/stream/",     # Kronos 预测 SSE · task_id 随机不可枚举
                    "/api/chat/skills/detail/",           # 内置能力详情（品牌/来源/描述）· /skills/[key] 页公开可分享
                    "/api/internal/")                     # 持仓建议 Phase 0 · MCP 桥接内部 API · 共享 secret 鉴权


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
                "[auth] 401 UNAUTHORIZED path={} method={} client={} — "
                "白名单未覆盖或请求未带 Authorization header",
                path, request.method, client_host,
            )
            return JSONResponse(
                {"error": "UNAUTHORIZED", "needLogin": True}, status_code=401
            )

        user_id = _verify_jwt(token)
        if not user_id:
            logger.warning(
                "[auth] 401 INVALID_TOKEN path={} method={} client={} token_prefix={}",
                path, request.method, client_host, token[:12],
            )
            return JSONResponse(
                {"error": "INVALID_TOKEN", "needLogin": True}, status_code=401
            )

        request.state.user_id = user_id
        return await call_next(request)


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _verify_jwt(token: str) -> str | None:
    """验证本地 JWT，返回 user_id 或 None。"""
    try:
        from app.routers.auth import verify_jwt
        payload = verify_jwt(token)
        return payload.get("sub") if payload else None
    except Exception as e:
        logger.warning("verify_jwt error: {}", e)
    return None
