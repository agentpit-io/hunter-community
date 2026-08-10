"""美港股量化择时(规则模型) —— GET /api/gm/kpred/{market}/{code}"""
from fastapi import APIRouter, HTTPException
from app.services.gm import findata_db, yahoo_hk, kpred_rules
from app.services.gm.market_calendar import market_state, state_label

router = APIRouter()


@router.get("/kpred/{market}/{code}")
async def gm_kpred(market: str, code: str):
    market = market.upper()
    code = code.upper() if market == "US" else code.zfill(5)
    if market == "US":
        bars = findata_db.us_kline(code, "1d", 250)
    elif market == "HK":
        bars = yahoo_hk.hk_kline(code, "1d", 250)
    else:
        raise HTTPException(400, "market 必须是 US / HK")
    closes = [b["close"] for b in bars]
    result = kpred_rules.score(closes)
    if result is None:
        return {"error": "insufficient_data", "market": market, "code": code,
                "bars": len(closes), "hint": "历史日线不足65根, 无法计算指标"}
    result.update({
        "market": market, "code": code,
        "as_of": bars[-1]["ts"] if bars else None,
        "market_state_label": state_label(market, market_state(market)),
    })
    return result
