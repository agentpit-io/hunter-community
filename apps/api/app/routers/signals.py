"""市场信号 API — GET /api/signals/current, /api/signals/history"""
import asyncio
import os
from fastapi import APIRouter, Request, HTTPException
from loguru import logger
from pydantic import BaseModel
from app.services.database import (
    get_latest_signals, list_market_signals, get_stocks_by_user,
    save_event_analysis, list_event_analyses, get_event_analysis_html,
)

router = APIRouter()

SIGNAL_META = {
    "cpi":        {"label": "美国 CPI",   "icon": "📊", "desc": "通胀数据 · BLS 每月发布"},
    "oil":        {"label": "布伦特油价", "icon": "🛢️", "desc": "日内异动监控 · Yahoo Finance"},
    "fomc":       {"label": "FOMC 决议",  "icon": "🏦", "desc": "美联储利率决议 + 鹰鸽分析"},
    "spacex":     {"label": "SpaceX IPO", "icon": "🚀", "desc": "上市表现 + A股航天板块联动"},
    "northbound": {"label": "北向资金",   "icon": "🧲", "desc": "沪深港通净流入/流出"},
}

SCENARIO_COLOR = {
    "HAWKISH_SHOCK":   "red",
    "DOVISH_RELIEF":   "green",
    "IN_LINE":         "yellow",
    "OIL_SPIKE":       "red",
    "OIL_DROP":        "green",
    "STRONG_OPEN":     "green",
    "BROKEN_IPO":      "red",
    "OUTFLOW":         "red",
    "INFLOW":          "green",
    "IN_LINE":         "yellow",  # FOMC 按兵不动
}


@router.get("/signals/current")
async def get_current_signals():
    latest = {s["signal_type"]: s for s in get_latest_signals()}
    result = []
    for stype, meta in SIGNAL_META.items():
        sig = latest.get(stype)
        result.append({
            "signal_type":   stype,
            "label":         meta["label"],
            "icon":          meta["icon"],
            "desc":          meta["desc"],
            "scenario":      sig["scenario"]      if sig else None,
            "triggered_at":  sig["triggered_at"]  if sig else None,
            "data":          sig["data"]           if sig else None,
            "portfolio_impact": sig["portfolio_impact"] if sig else "",
            "color":         SCENARIO_COLOR.get(sig["scenario"], "gray") if sig else "gray",
            "active":        sig is not None,
        })
    return result


_ADMIN_HERMES_UID = os.getenv("ADMIN_HERMES_USER_ID", "cmnhijcdl0000607z6vxzczez")


class FireSignalBody(BaseModel):
    signal_type: str  # cpi / oil / fomc / spacex / northbound
    scenario: str     # e.g. OIL_SPIKE / OIL_DROP / HAWKISH_SHOCK
    data: dict = {}
    test_only: bool = True  # 默认只推给 admin，生产触发传 False


@router.post("/signals/fire")
async def fire_signal(body: FireSignalBody, request: Request):
    """管理员手动触发信号推送（测试用）。test_only=True 时只推给 ADMIN_HERMES_USER_ID。"""
    uid = getattr(request.state, "user_id", "") or ""
    if not uid:
        raise HTTPException(status_code=401, detail="未登录")
    from app.services.signal_monitor import _dispatch_signal
    from app.services.database import save_market_signal
    import asyncio
    sig_id = await asyncio.to_thread(save_market_signal, body.signal_type, body.scenario, body.data)
    target_users = [_ADMIN_HERMES_UID] if body.test_only else None
    asyncio.create_task(_dispatch_signal(body.signal_type, body.scenario, body.data, sig_id, target_users=target_users))
    mode = f"仅推给 admin({_ADMIN_HERMES_UID[:8]}…)" if body.test_only else "全量推送"
    return {"ok": True, "sig_id": sig_id, "message": f"信号 {body.signal_type}/{body.scenario} 已触发（{mode}）"}


@router.get("/signals/history")
async def get_signal_history(limit: int = 30):
    rows = list_market_signals(limit=limit)
    for r in rows:
        meta = SIGNAL_META.get(r["signal_type"], {})
        r["label"] = meta.get("label", r["signal_type"])
        r["icon"]  = meta.get("icon", "📡")
        r["color"] = SCENARIO_COLOR.get(r["scenario"], "gray")
    return rows


@router.get("/signals/reports")
async def get_signal_reports(limit: int = 20):
    """返回最近信号触发记录及每次触发产生的报告列表（供推送管理页展示）。"""
    from app.services.database import list_signal_fires_with_reports
    rows = list_signal_fires_with_reports(limit=limit)
    for r in rows:
        meta = SIGNAL_META.get(r["signal_type"], {})
        r["label"] = meta.get("label", r["signal_type"])
        r["icon"]  = meta.get("icon", "📡")
        r["color"] = SCENARIO_COLOR.get(r["scenario"], "gray")
    return rows


class EventAnalysisBody(BaseModel):
    event: str
    # 可选：聚焦分析某一只股票（从"投研助手/深度研究"跳转过来时传入）
    # 若有值：只针对该股做事件影响分析（覆盖全自选股行为）
    # 若为空：保持原有的全自选股组合分析
    focus_symbol: str | None = None
    focus_name:   str | None = None


def _infer_exchange(code: str) -> str:
    """从 6 位 A 股代码推断交易所后缀"""
    if code.startswith(("60", "68", "11", "51", "52")):
        return "SH"
    if code.startswith(("43", "83", "87", "88")):
        return "BJ"
    return "SZ"


@router.post("/event-analysis")
async def event_analysis(body: EventAnalysisBody, request: Request):
    """用户输入自定义大事件，用 skill 分析持仓影响，返回 HTML 报告字符串，并存入历史。

    V2：支持 focus_symbol 聚焦分析。
      - 传 focus_symbol：只对该股做事件影响分析（可以不在自选股列表）
      - 未传：保持原有的"全自选股组合"分析
    """
    user_id = getattr(request.state, "user_id", "") or ""
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")
    if not body.event.strip():
        raise HTTPException(status_code=400, detail="请输入事件描述")

    # ── 决定分析范围（stocks 列表）──
    if body.focus_symbol:
        # 聚焦模式：只分析这只股。优先复用用户自选股元数据（若有）
        code = body.focus_symbol.strip()
        user_stocks = await asyncio.to_thread(get_stocks_by_user, user_id)
        matched = next((s for s in user_stocks if s.get("code") == code), None)
        if matched:
            stocks = [matched]
        else:
            name = (body.focus_name or code).strip()
            stocks = [{
                "code": code, "name": name,
                "market": "A", "exchange": _infer_exchange(code),
                "asset_type": "stock",
            }]
    else:
        # 全自选股模式（原有行为）
        stocks = await asyncio.to_thread(get_stocks_by_user, user_id)
        if not stocks:
            raise HTTPException(status_code=400, detail="请先添加自选股再使用事件分析")

    from app.services.signal_monitor import generate_event_analysis_html
    html = await asyncio.to_thread(generate_event_analysis_html, body.event.strip(), stocks)
    if not html:
        raise HTTPException(status_code=500, detail="分析生成失败，请稍后重试")

    # 存入历史记录
    record_id = await asyncio.to_thread(save_event_analysis, user_id, body.event.strip(), html)

    # 后台写记忆：把涉及的自选股全部记录到用户行为记忆
    async def _save_event_memory():
        try:
            from app.services.database import save_stock_memory, update_user_portrait
            event_desc = body.event.strip()[:200]
            for s in stocks:
                await asyncio.to_thread(
                    save_stock_memory,
                    user_id, s["code"], s["name"], "event_analysis",
                    None, None, event_desc,
                )
            await asyncio.to_thread(update_user_portrait, user_id)
        except Exception as me:
            logger.warning("event memory save failed: {}", me)
    asyncio.create_task(_save_event_memory())

    return {"html": html, "id": record_id}


@router.get("/event-analysis/history")
async def event_analysis_history(request: Request, limit: int = 30):
    """返回当前用户的事件分析历史列表（不含 HTML，只含标题和时间）。"""
    user_id = getattr(request.state, "user_id", "") or ""
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")
    rows = await asyncio.to_thread(list_event_analyses, user_id, limit)
    return rows


@router.get("/event-analysis/history/{record_id}")
async def event_analysis_history_detail(record_id: int, request: Request):
    """返回指定历史记录的完整 HTML（只能查看自己的）。"""
    user_id = getattr(request.state, "user_id", "") or ""
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")
    html = await asyncio.to_thread(get_event_analysis_html, record_id, user_id)
    if html is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"html": html}
