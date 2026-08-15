"""美港股K线（gm 端）。period: 1m/5m/1d。美股读 financedata 库，港股 Yahoo+缓存。"""
from fastapi import APIRouter, HTTPException, Query
from app.services.gm import findata_db, yahoo_hk

router = APIRouter()


@router.get("/kline/{market}/{code}")
async def gm_kline(market: str, code: str,
                   period: str = Query("1d", pattern="^(1m|5m|1d)$"),
                   limit: int = Query(250, ge=1, le=2000)):
    market = market.upper()
    if market == "US":
        # 库优先(覆盖最全),库空回落 Yahoo —— 港股早就是这个写法,美股一直没接上,
        # 结果开源版拿到的是 200 OK + bars=[](装作成功,用户看不出发生了什么)
        bars = findata_db.us_kline(code, period, limit) or yahoo_hk.us_kline(code, period, limit)
        delayed_note = "延迟约15分钟" if period in ("1m", "5m") else ""
    elif market == "HK":
        # 读库优先(每日采集入库), 库缺(池外冷门股/1分钟粒度)回退Yahoo实时拉
        bars = findata_db.hk_kline_db(code, period, limit) if period in ("1d", "5m") else []
        if not bars:
            bars = yahoo_hk.hk_kline(code, period, limit)
        delayed_note = "延迟约15分钟"
    else:
        raise HTTPException(400, "market 必须是 US / HK")
    return {"market": market, "code": code, "period": period,
            "bars": bars, "count": len(bars), "delayed_note": delayed_note}
