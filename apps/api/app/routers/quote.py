import asyncio
import json
import os
import redis
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from app.services.database import get_stocks, get_stocks_by_user
from app.services.finance_data_client import register_stocks, get_reliable_close, get_quote as fd_get_quote

router = APIRouter()
_redis = redis.Redis.from_url(
    os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    decode_responses=True,
)

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


def _cache_poisoned(code: str, raw) -> bool:
    """缓存里的 market 和代码形态对不上 → 这条缓存是错的。

    只做这一个判断,不做"全面校验" —— 判据越多越容易把好缓存误删,
    而误删的代价是多打一次上游,判漏的代价是用户一直看到错数据。
    """
    try:
        from app.services.market_source import market_of
        q = json.loads(raw)
    except Exception:                                          # noqa: BLE001
        return True          # 解析不了的缓存本来就该扔
    want = market_of(code).upper()
    got = (q.get("market") or "").upper()
    return bool(got) and got != want


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
    if cached and _cache_poisoned(code, cached):
        # 修复前写进去的脏缓存:港股 00700 曾被判成 A 股(以 "00" 开头),
        # 于是缓存里 market=A、name="00700"。ts 是今天,所以下面那段
        # 刷新逻辑**不会触发** —— 脏数据会一直返回下去。
        #
        # 已经装了的用户升级后不该还要手动清 Redis,所以这里自愈:
        # 缓存里的 market 和代码本身对不上就当没缓存。
        _redis.delete(f"quote:{code}")
        cached = None
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
    # Cache-miss path · try live fetch (finance_data_client hits SaaS or
    # falls back to providers.data_source · either yfinance/akshare).
    fresh = await asyncio.to_thread(fd_get_quote, code)
    if fresh:
        _redis.set(f"quote:{code}", json.dumps(fresh))
        return JSONResponse(content=fresh, headers=_NO_STORE_HEADERS)
    # Second attempt · kline-based reliable close (also has provider bridge)
    fresh = await asyncio.to_thread(_kline_refresh, code, today)
    if fresh:
        return JSONResponse(content=fresh, headers=_NO_STORE_HEADERS)
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
