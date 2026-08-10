"""持仓报告 API

GET  /api/portfolio/summary          — 当前用户持仓 + 实时盈亏汇总
PUT  /api/portfolio/{code}/position  — 更新单只股票持仓数据（不覆盖买入理由）
DELETE /api/portfolio/{code}/position — 清除持仓数据（保留买入理由）
"""
import json
import redis
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from app.services.database import (
    list_stocks_with_thesis_by_user,
    get_thesis_by_user,
    upsert_thesis_by_user,
    get_risk_profile,
    upsert_risk_profile,
)

router = APIRouter()
_redis = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)


class PositionIn(BaseModel):
    shares:     Optional[int]   = None
    cost_price: Optional[float] = None
    buy_date:   Optional[str]   = None


def _get_quote(code: str) -> dict:
    raw = _redis.get(f"quote:{code}")
    if raw:
        return json.loads(raw)
    return {}


@router.get("/portfolio/summary")
async def portfolio_summary(request: Request):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")

    stocks = list_stocks_with_thesis_by_user(user_id)

    positions = []
    no_cost_stocks = []

    for s in stocks:
        cost_price = s.get("cost_price")
        shares = s.get("shares")

        if not cost_price:
            no_cost_stocks.append({"code": s["code"], "name": s["name"]})
            continue

        q = _get_quote(s["code"])
        current_price = q.get("price")
        change_pct    = q.get("change_pct")

        market_value     = None
        profit_loss      = None
        profit_loss_pct  = None
        today_profit     = None

        if current_price is not None and shares:
            market_value    = round(float(current_price) * shares, 2)
            profit_loss     = round((float(current_price) - cost_price) * shares, 2)
            profit_loss_pct = round((float(current_price) - cost_price) / cost_price * 100, 2)
        prev_close_raw = q.get("prev_close")
        if change_pct is not None and shares and current_price is not None:
            if prev_close_raw is not None:
                prev_close = float(prev_close_raw)
            else:
                prev_close = float(current_price) / (1 + float(change_pct) / 100)
            today_profit = round((float(current_price) - prev_close) * shares, 2)

        positions.append({
            "code":            s["code"],
            "name":            s["name"],
            "shares":          shares,
            "cost_price":      cost_price,
            "current_price":   current_price,
            "change_pct":      change_pct,
            "market_value":    market_value,
            "profit_loss":     profit_loss,
            "profit_loss_pct": profit_loss_pct,
            "today_profit":    today_profit,
            "buy_date":        s.get("buy_date"),
            "has_price":       current_price is not None,
        })

    # 汇总
    total_cost         = sum(s["cost_price"] * s["shares"] for s in positions if s["cost_price"] and s["shares"])
    total_market_value = sum(s["market_value"] for s in positions if s["market_value"] is not None)
    total_profit_loss  = sum(s["profit_loss"] for s in positions if s["profit_loss"] is not None)
    total_today_profit = sum(s["today_profit"] for s in positions if s["today_profit"] is not None)
    total_profit_pct   = round(total_profit_loss / total_cost * 100, 2) if total_cost else 0

    return {
        "summary": {
            "total_market_value": round(total_market_value, 2),
            "total_cost":         round(total_cost, 2),
            "total_profit_loss":  round(total_profit_loss, 2),
            "total_profit_pct":   total_profit_pct,
            "total_today_profit": round(total_today_profit, 2),
        },
        "positions":      positions,
        "no_cost_stocks": no_cost_stocks,
    }


@router.put("/portfolio/{code}/position")
async def update_position(code: str, payload: PositionIn, request: Request):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")

    # 保留原有买入理由，只更新持仓字段
    existing    = get_thesis_by_user(code, user_id)
    thesis_text = existing.get("thesis_text", "") if existing else ""

    ok = upsert_thesis_by_user(
        code, user_id, thesis_text,
        shares=payload.shares,
        cost_price=payload.cost_price,
        buy_date=payload.buy_date,
    )
    if not ok:
        raise HTTPException(400, "更新失败，请先将股票加入自选股")
    return {"ok": True}


@router.delete("/portfolio/{code}/position")
async def clear_position(code: str, request: Request):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")

    existing    = get_thesis_by_user(code, user_id)
    thesis_text = existing.get("thesis_text", "") if existing else ""

    upsert_thesis_by_user(
        code, user_id, thesis_text,
        shares=None, cost_price=None, buy_date=None,
    )
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────
# 用户风险画像（2026-08 · 持仓建议 Sprint 1）
# GET / PUT /api/portfolio/profile · 供 /chat SKILL + /portfolio 抽屉共用
# ─────────────────────────────────────────────────────────────────────

class RiskProfileIn(BaseModel):
    cash_balance:   Optional[float] = None
    risk_tolerance: Optional[str]   = None   # low / medium / high
    max_position:   Optional[float] = None
    max_hk_ratio:   Optional[float] = None
    max_sector:     Optional[float] = None
    extra_patch:    Optional[dict]  = None


@router.get("/portfolio/profile")
async def read_risk_profile(request: Request):
    """读取风险画像 · 无记录时返回默认值（is_default=true）。"""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")
    return get_risk_profile(user_id)


@router.put("/portfolio/profile")
async def update_risk_profile_api(payload: RiskProfileIn, request: Request):
    """更新风险画像 · None 字段保留旧值 · 返回完整更新后 profile。"""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")
    try:
        return upsert_risk_profile(
            user_id,
            cash_balance=payload.cash_balance,
            risk_tolerance=payload.risk_tolerance,
            max_position=payload.max_position,
            max_hk_ratio=payload.max_hk_ratio,
            max_sector=payload.max_sector,
            extra_patch=payload.extra_patch,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
