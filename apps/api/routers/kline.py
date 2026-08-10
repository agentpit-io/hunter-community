import akshare as ak
import pandas as pd
from fastapi import APIRouter, Query
from app.config import STOCK_MAP

router = APIRouter()

@router.get("/kline/{code}")
async def get_kline(code: str, period: str = Query("daily", enum=["daily","weekly","monthly"]), limit: int = 120):
    if code not in STOCK_MAP:
        return {"code": code, "data": []}
    stock = STOCK_MAP[code]
    try:
        if stock["market"] == "A":
            period_map = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
            df = ak.stock_zh_a_hist(symbol=code, period=period_map[period], adjust="qfq")
            df = df.tail(limit)
            data = [{"ts": str(r["日期"]), "open": r["开盘"], "high": r["最高"],
                     "low": r["最低"], "close": r["收盘"], "volume": r["成交量"]}
                    for _, r in df.iterrows()]
        else:
            df = ak.stock_hk_hist(symbol=code, period=period, adjust="qfq")
            df = df.tail(limit)
            data = [{"ts": str(r["日期"]), "open": r["开盘"], "high": r["最高"],
                     "low": r["最低"], "close": r["收盘"], "volume": r["成交量"]}
                    for _, r in df.iterrows()]
        return {"code": code, "period": period, "data": data}
    except Exception as e:
        return {"code": code, "period": period, "data": [], "error": str(e)}

@router.get("/timeshare/{code}")
async def get_timeshare(code: str):
    if code not in STOCK_MAP:
        return {"code": code, "data": []}
    stock = STOCK_MAP[code]
    try:
        if stock["market"] == "A":
            df = ak.stock_zh_a_hist_min_em(symbol=code, period="1", adjust="")
            df = df.tail(241)
            data = [{"ts": str(r["时间"]), "price": r["收盘"], "volume": r["成交量"],
                     "avg": r.get("均价", r["收盘"])}
                    for _, r in df.iterrows()]
        else:
            df = ak.stock_hk_hist_min_em(symbol=code, period="1", adjust="")
            df = df.tail(241)
            data = [{"ts": str(r["时间"]), "price": r["收盘"], "volume": r["成交量"]}
                    for _, r in df.iterrows()]
        return {"code": code, "data": data}
    except Exception as e:
        return {"code": code, "data": [], "error": str(e)}
