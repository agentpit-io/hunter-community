"""新浪未覆盖的美股（含 OTC）真实日线备用源，不合成缺失K线。"""
from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from loguru import logger

_LOCK = threading.Lock()
_NEXT_REQUEST = 0.0
_CACHE: OrderedDict = OrderedDict()
_TTL = 1200


def valid_bar(row: dict) -> bool:
    try:
        o, h, l, c, v = [float(row[k]) for k in ("open", "high", "low", "close", "volume")]
        return (all(math.isfinite(x) for x in (o, h, l, c, v))
                and min(o, h, l, c) > 0 and v >= 0
                and h >= max(o, c, l) and l <= min(o, c, h))
    except (KeyError, TypeError, ValueError):
        return False


def parse_chart(chart: dict) -> list[dict]:
    """只返回完整真实OHLCV；Yahoo空洞行不能变成零价格。"""
    timestamps = chart.get("timestamp") or []
    quotes = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    rows = {}
    for i, ts in enumerate(timestamps):
        try:
            values = [float(quotes[k][i]) for k in ("open", "high", "low", "close", "volume")]
            o, h, l, c, v = values
            day = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
            row = {"ts": day, "open": o, "high": h, "low": l, "close": c, "volume": v}
            if valid_bar(row):
                row["volume"] = int(v)
                rows[day] = row
        except (KeyError, IndexError, TypeError, ValueError, OverflowError, OSError):
            continue
    return [rows[d] for d in sorted(rows)]


def daily(code: str, limit: int = 800) -> list[dict]:
    symbol = str(code or "").strip().upper()
    if symbol.endswith(".US"):
        symbol = symbol[:-3]
    # Yahoo用 BRK-B 表示股份类别；不能split('.')把B丢掉。
    symbol = symbol.replace("/", "-").replace(".", "-")
    if not symbol:
        return []
    with _LOCK:
        cached = _CACHE.get(symbol)
        if cached and time.monotonic() - cached[0] < _TTL:
            _CACHE.move_to_end(symbol)
            return cached[1][-limit:] if limit else cached[1][:]
    global _NEXT_REQUEST
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        # 批量验收与悬停共享限速；出现429停止本次，不立即换域名继续打。
        with _LOCK:
            delay = max(0, _NEXT_REQUEST - time.monotonic())
            _NEXT_REQUEST = time.monotonic() + delay + 1
        if delay:
            time.sleep(delay)
        try:
            response = requests.get(
                f"https://{host}/v8/finance/chart/{quote(symbol, safe='-')}",
                params={"interval": "1d", "range": "5y"},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=15,
            )
            if response.status_code == 429:
                logger.warning("[us_daily_fallback] Yahoo限流 {}", symbol)
                return []
            response.raise_for_status()
            result = (response.json().get("chart") or {}).get("result") or []
            rows = parse_chart(result[0]) if result else []
            if rows:
                with _LOCK:
                    _CACHE[symbol] = (time.monotonic(), rows)
                    _CACHE.move_to_end(symbol)
                    while len(_CACHE) > 256:
                        _CACHE.popitem(last=False)
                return rows[-limit:] if limit else rows
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            logger.warning("[us_daily_fallback] {} {}: {}", host, symbol, type(exc).__name__)
    return []
