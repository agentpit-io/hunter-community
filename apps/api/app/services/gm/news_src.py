"""美港股新闻源(gm端)。

US: Alpaca News API(免费档自带, Benzinga源) https://data.alpaca.markets/v1beta1/news
HK: Yahoo finance search 接口附带的 news 数组
Redis缓存15分钟。统一返回 [{title, source, url, ts, lang}]
"""
import os
import json
import logging
from datetime import datetime, timezone
import requests

from app.services.gm.yahoo_hk import _cache_get, _cache_set, _yahoo_symbol

log = logging.getLogger(__name__)

_ALPACA_HEADERS = {
    "APCA-API-KEY-ID": os.getenv("ALPACA_KEY_ID", ""),
    "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET", ""),
}


def us_news(symbol: str, limit: int = 12) -> list[dict]:
    symbol = symbol.upper()
    key = f"gm:usnews:{symbol}"
    cached = _cache_get(key)
    if cached is not None:
        return cached[:limit]
    if not _ALPACA_HEADERS["APCA-API-KEY-ID"]:
        return []
    try:
        r = requests.get("https://data.alpaca.markets/v1beta1/news",
                         params={"symbols": symbol, "limit": 20},
                         headers=_ALPACA_HEADERS, timeout=12)
        r.raise_for_status()
        out = []
        for n in r.json().get("news", []):
            out.append({
                "title": n.get("headline", ""),
                "source": n.get("source", "Benzinga"),
                "url": n.get("url", ""),
                "ts": n.get("created_at", ""),
                "lang": "en",
            })
        _cache_set(key, out, 900)
        return out[:limit]
    except Exception as e:
        log.warning("alpaca news %s failed: %s", symbol, e)
        return []


def hk_news(code: str, limit: int = 12) -> list[dict]:
    key = f"gm:hknews:{code}"
    cached = _cache_get(key)
    if cached is not None:
        return cached[:limit]
    try:
        r = requests.get("https://query1.finance.yahoo.com/v1/finance/search",
                         params={"q": _yahoo_symbol(code), "newsCount": 15, "quotesCount": 0},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        r.raise_for_status()
        out = []
        for n in r.json().get("news", []):
            ts = n.get("providerPublishTime")
            out.append({
                "title": n.get("title", ""),
                "source": n.get("publisher", "Yahoo"),
                "url": n.get("link", ""),
                "ts": datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else "",
                "lang": "en",
            })
        _cache_set(key, out, 900)
        return out[:limit]
    except Exception as e:
        log.warning("yahoo hk news %s failed: %s", code, e)
        return []
