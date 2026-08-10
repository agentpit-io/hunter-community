"""gm组合: 仓位录入 + 双币组合汇总。
仓位复用A股 position_thesis 表(code,user_id 维度, 币种随市场)。"""
import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from app.services.database import get_conn, upsert_thesis_by_user
from app.services.gm import findata_db, yahoo_hk
from app.services.gm.ah_south import _fetch_chart_raw
from app.services.gm.yahoo_hk import _cache_get, _cache_set

log = logging.getLogger(__name__)
router = APIRouter()


class PositionIn(BaseModel):
    shares: float | None = None
    cost_price: float | None = None


def _uid(request: Request) -> str:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "需要登录")
    return user_id


def _fx(pair: str) -> float | None:
    key = f"gm:fx:{pair}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        rate = (_fetch_chart_raw(f"{pair}=X").get("meta") or {}).get("regularMarketPrice")
        if rate:
            _cache_set(key, float(rate), 3600)
            return float(rate)
    except Exception as e:
        log.warning("fx %s failed: %s", pair, e)
    return None


@router.put("/watchlist/{code}/position")
async def gm_set_position(code: str, body: PositionIn, request: Request):
    user_id = _uid(request)
    ok = upsert_thesis_by_user(code, user_id, "",
                               int(body.shares) if body.shares else None,
                               body.cost_price, None)
    return {"ok": ok}


@router.get("/portfolio/summary")
async def gm_portfolio_summary(request: Request):
    user_id = _uid(request)
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""SELECT s.code, s.name, s.market, t.shares, t.cost_price
                   FROM stocks s LEFT JOIN position_thesis t
                     ON t.code = s.code AND t.user_id = s.user_id
                   WHERE s.enabled = TRUE AND s.user_id = %s AND s.market IN ('US','HK')""",
                (user_id,))
    rows = cur.fetchall(); conn.close()
    usdcny, hkdcny = _fx("USDCNY"), _fx("HKDCNY")
    total_cny = pnl_today_cny = cost_cny = 0.0
    positions = []
    fx_missing = False
    for code, name, market, shares, cost in rows:
        if not shares:
            continue  # 仅关注无仓位的不计
        q = findata_db.us_quote(code) if market == "US" else yahoo_hk.hk_quote(code)
        if not q or not q.get("price"):
            continue
        price = float(q["price"])
        chg = float(q.get("change_pct") or 0)
        fxr = (usdcny if market == "US" else hkdcny) or 0
        if not fxr:
            fx_missing = True   # 汇率获取失败: mv_local仍准确, CNY折算不可信
        mv_local = price * float(shares)
        mv_cny = mv_local * fxr
        prev_price = price / (1 + chg / 100) if chg != -100 else price
        pnl_today_cny += (price - prev_price) * float(shares) * fxr
        total_cny += mv_cny
        if cost:
            cost_cny += float(cost) * float(shares) * fxr
        positions.append({"code": code, "name": name, "market": market,
                          "shares": float(shares), "cost_price": float(cost) if cost else None,
                          "price": price, "mv_local": round(mv_local, 2),
                          "mv_cny": round(mv_cny, 2), "change_pct": chg,
                          "currency": "USD" if market == "US" else "HKD"})
    return {
        "total_mv_cny": round(total_cny, 2),
        "pnl_today_cny": round(pnl_today_cny, 2),
        "pnl_total_cny": round(total_cny - cost_cny, 2) if cost_cny else None,
        "fx": {"USDCNY": usdcny, "HKDCNY": hkdcny},
        "fx_missing": fx_missing,
        "positions": positions, "count": len(positions),
    }
