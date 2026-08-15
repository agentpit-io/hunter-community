"""美港股行情（gm 端）。美股: financedata 库 us_kline 最新bar；港股: Yahoo延迟源。"""
from fastapi import APIRouter, HTTPException, Query, Response
from app.services.gm import findata_db, yahoo_hk
from app.services.gm.market_calendar import market_state, state_label, state_iso

router = APIRouter()

_NO_STORE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


def _one(market: str, code: str) -> dict | None:
    market = market.upper()
    if market == "US":
        # 先试 financedata 库(数据更全:主表 13,019 只 + 分钟线)。
        # 库拿不到时回落到 Yahoo chart —— 开源版没有 FINDATA_DB_URL,
        # 走到这里之前 100% 是 404,现在免 key 也能出行情。
        q = findata_db.us_quote(code) or yahoo_hk.us_quote(code)
    elif market == "HK":
        q = yahoo_hk.hk_quote(code)
        if q is not None:
            m = findata_db.hk_master(code)
            if m:
                q["name"] = m["name"] or q["name"]
                q["lot_size"] = m.get("lot_size")
            q["dual_us"] = findata_db.DUAL_LISTED_HK.get(code.zfill(5))
    else:
        return None
    if q is None:
        return None
    st = market_state(market)
    q["market_state"] = st
    q["market_state_label"] = state_label(market, st)
    q["market_state_iso"] = state_iso(st)
    return q


@router.get("/quote/{market}/{code}")
async def gm_quote(market: str, code: str, response: Response):
    response.headers.update(_NO_STORE)
    if market.upper() not in ("US", "HK"):
        raise HTTPException(400, "market 必须是 US / HK")
    q = _one(market, code)
    if q is None:
        raise HTTPException(404, f"{market.upper()}/{code} not_found")
    return q


@router.get("/fundamentals/hk/{code}")
async def gm_hk_fundamentals(code: str):
    """港股财务摘要(年度, 读库): 营收/净利/YoY/EPS/BPS"""
    items = findata_db.hk_fin_db(code, 4)
    return {"items": items, "count": len(items),
            "note": "金额单位: 亿(报告币种) · 来源: 东财港股财报数据"}


@router.get("/quotes")
async def gm_quotes(response: Response, codes: str = Query("", description="逗号分隔 US:AAPL,HK:00700")):
    """批量行情。codes=US:AAPL,HK:00700"""
    response.headers.update(_NO_STORE)
    out = []
    for item in codes.split(","):
        item = item.strip()
        if ":" not in item:
            continue
        market, code = item.split(":", 1)
        q = _one(market, code)
        if q:
            out.append(q)
    return {"items": out, "count": len(out)}
