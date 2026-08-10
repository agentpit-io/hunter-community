import asyncio
import json
import redis
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from app.services.database import get_stocks, get_stocks_by_user
from app.services.finance_data_client import register_stocks, get_reliable_close

router = APIRouter()
_redis = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)

CST = timezone(timedelta(hours=8))

# 防微信内嵌浏览器 / 部分手机 App webview 激进缓存行情数据（曾出现用户看到很老的 quote）
_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma":        "no-cache",
    "Expires":       "0",
}


def _today() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d")


def _get_user_stocks(request: Request) -> list:
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return get_stocks_by_user(user_id)
    return get_stocks()


def _kline_refresh(code: str, today: str) -> dict | None:
    """用 kline 刷新过期 quote 并写回 Redis。"""
    kq = get_reliable_close(code, today)
    if kq:
        _redis.set(f"quote:{code}", json.dumps(kq))
        logger.info("✅ kline 刷新首页行情 {} → {}", code, kq.get("price"))
    else:
        logger.warning("⚠️ kline 兜底失败，维持旧数据 {}", code)
    return kq


@router.get("/stocks")
async def list_stocks(request: Request):
    return {"stocks": _get_user_stocks(request)}


@router.get("/quote/{code}")
async def get_quote(code: str):
    today = _today()
    cached = _redis.get(f"quote:{code}")
    if cached:
        q = json.loads(cached)
        if q.get("ts", "")[:10] != today:
            kq = await asyncio.to_thread(_kline_refresh, code, today)
            if kq:
                return JSONResponse(content=kq, headers=_NO_STORE_HEADERS)
        return JSONResponse(content=q, headers=_NO_STORE_HEADERS)
    stocks = get_stocks()
    stock_map = {s["code"]: s for s in stocks}
    if code not in stock_map:
        raise HTTPException(404, f"股票 {code} 不存在")
    return JSONResponse(
        content={"code": code, "name": stock_map[code]["name"], "price": None,
                 "msg": "非交易时间或数据未就绪"},
        headers=_NO_STORE_HEADERS,
    )


@router.get("/quotes")
async def get_all_quotes(request: Request):
    today = _today()
    stocks = _get_user_stocks(request)
    register_stocks(stocks)

    # 找出需要 kline 兜底的股票（ts 不是今天）
    cached_map: dict[str, dict] = {}
    stale_codes: list[str] = []
    for s in stocks:
        raw = _redis.get(f"quote:{s['code']}")
        if raw:
            q = json.loads(raw)
            cached_map[s["code"]] = q
            if q.get("ts", "")[:10] != today:
                stale_codes.append(s["code"])
        else:
            cached_map[s["code"]] = {"code": s["code"], "name": s["name"], "price": None}

    # 并发 kline 刷新所有过期股票
    if stale_codes:
        logger.info("首页行情 {} 只股票数据过期，尝试 kline 兜底…", len(stale_codes))
        refreshed = await asyncio.gather(
            *[asyncio.to_thread(_kline_refresh, code, today) for code in stale_codes]
        )
        for code, kq in zip(stale_codes, refreshed):
            if kq:
                cached_map[code] = kq

    return JSONResponse(
        content={"quotes": [cached_map[s["code"]] for s in stocks]},
        headers=_NO_STORE_HEADERS,
    )
