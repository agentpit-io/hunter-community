import asyncio
import json
import redis
import akshare as ak
from datetime import datetime, time
from loguru import logger
from app.config import STOCKS, ak_code

_redis = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
_task = None

def _is_trading_time() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    morning = time(9, 25) <= t <= time(11, 35)
    afternoon = time(12, 55) <= t <= time(15, 5)
    return morning or afternoon

def _fetch_a_quote(stock: dict) -> dict | None:
    try:
        code = stock["code"]
        df = ak.stock_bid_ask_em(symbol=code)
        info = {row["item"]: row["value"] for _, row in df.iterrows()}
        return {
            "code": code,
            "name": stock["name"],
            "price": float(info.get("最新", 0)),
            "change_pct": float(info.get("涨跌幅", "0").replace("%", "")),
            "change_amt": float(info.get("涨跌额", 0)),
            "volume": int(float(info.get("总手", 0)) * 100),
            "amount": float(info.get("金额", 0)),
            "high": float(info.get("最高", 0)),
            "low": float(info.get("最低", 0)),
            "open": float(info.get("今开", 0)),
            "prev_close": float(info.get("昨收", 0)),
            "bid1": float(info.get("买一价", 0)),
            "bid1_vol": int(float(info.get("买一量", 0))),
            "ask1": float(info.get("卖一价", 0)),
            "ask1_vol": int(float(info.get("卖一量", 0))),
            "ts": datetime.now().isoformat(),
            "market": "A",
        }
    except Exception as e:
        logger.warning("A股行情获取失败 {}: {}", stock["code"], e)
        return None

def _fetch_hk_quote(stock: dict) -> dict | None:
    try:
        code = stock["code"]
        df = ak.stock_hk_spot_em()
        row = df[df["代码"] == code]
        if row.empty:
            return None
        r = row.iloc[0]
        return {
            "code": code,
            "name": stock["name"],
            "price": float(r.get("最新价", 0)),
            "change_pct": float(str(r.get("涨跌幅", "0")).replace("%", "")),
            "change_amt": float(r.get("涨跌额", 0)),
            "volume": int(float(r.get("成交量", 0))),
            "amount": float(r.get("成交额", 0)),
            "high": float(r.get("最高", 0)),
            "low": float(r.get("最低", 0)),
            "open": float(r.get("今开", 0)),
            "prev_close": float(r.get("昨收", 0)),
            "ts": datetime.now().isoformat(),
            "market": "HK",
        }
    except Exception as e:
        logger.warning("港股行情获取失败 {}: {}", stock["code"], e)
        return None

async def _collect_loop():
    logger.info("Collector started")
    while True:
        try:
            if _is_trading_time():
                for stock in STOCKS:
                    if stock["market"] == "A":
                        data = _fetch_a_quote(stock)
                    else:
                        data = _fetch_hk_quote(stock)
                    if data:
                        _redis.setex(f"quote:{stock['code']}", 30, json.dumps(data))
                        logger.debug("Updated quote: {} {}", stock["code"], data["price"])
                await asyncio.sleep(5)
            else:
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Collector error: {}", e)
            await asyncio.sleep(10)

async def start_collector():
    global _task
    _task = asyncio.create_task(_collect_loop())

async def stop_collector():
    if _task:
        _task.cancel()
