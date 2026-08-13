"""持仓建议 · Phase 0 · MCP 桥接 HTTP 端点

暴露给 /opt/opencode-mcp/{watchlist,portfolio}_mcp.py 反调 · 内部走 subagents 现有函数。
不走 JWT 中间件（middleware 白名单已放行 /api/internal） · 用共享 secret + X-Hunter-User-Id
认证 · 只接受 localhost 请求（docker bridge 172.17.0.1）。

对应 doc/codex/持仓建议/06-架构断层诊断-opencode-vs-orchestrator.md §5 方案 A。
"""
from __future__ import annotations
import os
from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from app.services.subagents.watchlist_agent import (
    _quickview, _news, _digest,
)
from app.services.subagents.portfolio_agent import (
    _rebalance, _stress,
    _fmt_profile_for_card,
)
from app.services.database import get_risk_profile, upsert_risk_profile, get_conn

router = APIRouter(prefix="/internal", tags=["mcp-bridge"])


_INTERNAL_KEY = os.getenv("HUNTER_INTERNAL_KEY", "")


def _auth(request: Request) -> str:
    """验证 MCP 侧共享 secret · 返回 X-Hunter-User-Id · 失败 401。"""
    key = request.headers.get("X-Hunter-Internal-Key", "")
    if key != _INTERNAL_KEY:
        raise HTTPException(401, "internal auth failed")
    user_id = request.headers.get("X-Hunter-User-Id", "").strip()
    logger.info("[internal] path={} user_id={} · key_ok",
                request.url.path, user_id or "(missing!)")
    return user_id  # 允许空 · 交由 tool 内部决定是否必需


# ─────────────────────────────────────────────────────────────────────
# Watchlist · 3 tool
# ─────────────────────────────────────────────────────────────────────

class QuickviewIn(BaseModel):
    code: str


@router.post("/watchlist/stock_quickview")
async def _api_quickview(body: QuickviewIn, request: Request):
    user_id = _auth(request)
    return await _quickview(body.code.strip(), user_id or None)


class NewsIn(BaseModel):
    code: str
    limit: int = 5


@router.post("/watchlist/stock_news")
async def _api_news(body: NewsIn, request: Request):
    _auth(request)   # 不需要 user_id · 只做鉴权
    limit = max(1, min(10, int(body.limit)))
    return await _news(body.code.strip(), limit)


class DigestIn(BaseModel):
    top_n: int = 3


@router.post("/watchlist/watchlist_digest")
async def _api_digest(body: DigestIn, request: Request):
    user_id = _auth(request)
    if not user_id:
        return {"type": "watchlist_digest", "error": "需要登录后才能拉自选股日报"}
    top_n = max(1, min(10, int(body.top_n)))
    return await _digest(user_id, top_n)


class AddIn(BaseModel):
    query: str


@router.post("/watchlist/watchlist_add")
async def _api_add(body: AddIn, request: Request):
    """把用户口述的股票加自选。query 可以是名称、代码,中英文皆可。

    复用现有 search_stocks(东方财富 suggest) 拿完整字段,取第一个命中直接落库。
    命中 0 条则返回 not_found;命中已存在 watchlist 也算成功(自愈,幂等)。
    """
    user_id = _auth(request)
    if not user_id:
        return {"type": "watchlist_add", "success": False,
                "error": "unauthorized",
                "message": "请先在网页登录后再让我帮你加自选。"}

    q = (body.query or "").strip()
    if not q:
        return {"type": "watchlist_add", "success": False,
                "error": "empty_query",
                "message": "请告诉我股票名字或代码,比如 '紫金矿业' 或 '601899'。"}

    # 复用 search 端点的实现 · 不新起 HTTP 调用
    from app.routers.watchlist import search_stocks
    from app.services.database import add_stock_by_user
    from app.services.finance_data_client import subscribe as fd_subscribe

    result = await search_stocks(q=q, limit=5)
    items = result.get("items") if isinstance(result, dict) else []
    if not items:
        return {"type": "watchlist_add", "success": False,
                "error": "not_found",
                "query": q,
                "message": f"没找到匹配 '{q}' 的股票/ETF/基金,试试完整名字或代码?"}

    top = items[0]
    try:
        added = add_stock_by_user(
            top["code"], top["name"], top["market"],
            top["exchange"], top["asset_type"], user_id,
        )
        # finance-data 订阅失败不影响主流程,静默忽略
        try:
            fd_subscribe(top["code"], top["name"], top["market"],
                         top["exchange"], top["asset_type"])
        except Exception:
            pass
    except Exception as e:
        logger.exception("[internal.watchlist_add] db insert failed: {}", e)
        return {"type": "watchlist_add", "success": False,
                "error": "db_error",
                "message": f"落库失败: {type(e).__name__}"}

    return {
        "type": "watchlist_add",
        "success": True,
        "already_added": not added,
        "item": {
            "code": top["code"], "name": top["name"],
            "market": top["market"], "exchange": top["exchange"],
            "asset_type": top["asset_type"],
        },
        # 前 5 条一起回,让 LLM 在有多个匹配时(比如"东方"这种模糊词)可以追问
        "candidates": items[:5],
        "message": (
            f"已把 {top['name']} ({top['code']}) 加入你的自选。"
            if added else
            f"{top['name']} ({top['code']}) 已经在你的自选里了,无需重复添加。"
        ),
    }


# ─────────────────────────────────────────────────────────────────────
# Portfolio · 3 tool
# ─────────────────────────────────────────────────────────────────────

class RebalanceIn(BaseModel):
    cash_available: float = 0


@router.post("/portfolio/portfolio_rebalance")
async def _api_rebalance(body: RebalanceIn, request: Request):
    user_id = _auth(request)
    if not user_id:
        return {"type": "portfolio_rebalance", "error": "需要登录后才能给组合建议"}
    return await _rebalance(user_id, float(body.cash_available or 0))


class StressIn(BaseModel):
    shock_code: str
    shock_pct: float
    sector_pass_through: bool = True


@router.post("/portfolio/portfolio_stress")
async def _api_stress(body: StressIn, request: Request):
    user_id = _auth(request)
    if not user_id:
        return {"type": "portfolio_stress", "error": "需要登录后才能做情景模拟"}
    return await _stress(user_id, body.shock_code.strip(),
                          float(body.shock_pct), bool(body.sector_pass_through))


class ProfileIn(BaseModel):
    cash_balance:   float | None = None
    risk_tolerance: str   | None = None
    max_position:   float | None = None
    max_hk_ratio:   float | None = None
    max_sector:     float | None = None
    read_only:      bool = False


@router.post("/portfolio/update_risk_profile")
async def _api_profile(body: ProfileIn, request: Request):
    user_id = _auth(request)
    if not user_id:
        return {"type": "update_risk_profile", "error": "需要登录后才能设置风险画像"}

    write_fields = (body.cash_balance, body.risk_tolerance,
                    body.max_position, body.max_hk_ratio, body.max_sector)
    has_write = any(v is not None for v in write_fields)

    try:
        if body.read_only or not has_write:
            profile = get_risk_profile(user_id)
            return _fmt_profile_for_card(profile, before=None)
        before = get_risk_profile(user_id)
        profile = upsert_risk_profile(
            user_id,
            cash_balance=body.cash_balance,
            risk_tolerance=body.risk_tolerance,
            max_position=body.max_position,
            max_hk_ratio=body.max_hk_ratio,
            max_sector=body.max_sector,
        )
        return _fmt_profile_for_card(profile, before=before)
    except ValueError as e:
        return {"type": "update_risk_profile", "error": str(e)}


@router.get("/ping")
async def _ping():
    """无鉴权健康检查 · MCP 启动时用来确认 hermes-api 可达。"""
    return {"ok": True, "service": "hermes-internal-mcp-bridge"}


# ─────────────────────────────────────────────────────────────────────
# Session ↔ User 反查（多租户方案 A · 2026-08）
# 对应 doc/codex/自选股整合/02-多租户身份映射方案.md
# 修补 opencode chat.message hook 拿不到 message.metadata.hermes_token 的问题：
# opencode 内部消化了 metadata · hunter-auth 存不到 sessionUsers · 全靠 fallback ·
# 本端点让 hunter-mcp-context plugin 直接用 sessionID 反查 chat_session_owner 表。
# ─────────────────────────────────────────────────────────────────────

@router.get("/session/{session_id}/user")
async def _api_session_user(session_id: str, request: Request):
    """MCP 桥接反查：sessionID → user_id · 走 chat_session_owner 表。"""
    key = request.headers.get("X-Hunter-Internal-Key", "")
    if key != _INTERNAL_KEY:
        raise HTTPException(401, "internal auth failed")

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT user_id FROM chat_session_owner "
            "WHERE session_id = %s AND NOT archived",
            (session_id,),
        )
        row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        logger.warning("[internal] session-lookup miss · session_id={}", session_id[:12])
        raise HTTPException(404, f"session {session_id[:12]}... 无归属记录")

    user_id = row[0]
    logger.info("[internal] session-lookup OK · session={} · user={}",
                session_id[:12], (user_id or "")[:8])
    return {"session_id": session_id, "user_id": user_id}
