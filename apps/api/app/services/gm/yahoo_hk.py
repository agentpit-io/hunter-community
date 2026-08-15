"""港股行情/K线：Yahoo chart 公开接口按需拉取 + Redis 缓存。

- 免费源，港股延迟约15分钟（行业免费档惯例，前端标注）
- 不引入 yfinance 依赖，直接打 query1.finance.yahoo.com/v8/finance/chart
- Redis 缓存：quote 60s、分钟K线 5min、日K线 30min；Redis 挂了则直拉不缓存
- 后续切 iTick/富途实时源时只改本文件
"""
import os
import json
import logging
import requests

log = logging.getLogger(__name__)

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_redis = None


def _r():
    global _redis
    if _redis is None:
        try:
            import redis as _redis_mod
            _redis = _redis_mod.Redis.from_url(_REDIS_URL, socket_timeout=2)
            _redis.ping()
        except Exception:
            _redis = False  # 不可用，降级为不缓存
    return _redis or None


def _cache_get(key: str):
    r = _r()
    if not r:
        return None
    try:
        v = r.get(key)
        return json.loads(v) if v else None
    except Exception:
        return None


def _cache_set(key: str, value, ttl: int):
    r = _r()
    if not r:
        return
    try:
        r.setex(key, ttl, json.dumps(value))
    except Exception:
        pass


def _yahoo_symbol(code: str) -> str:
    """00700 -> 0700.HK (Yahoo用4位, 去一个前导零)"""
    c = code.zfill(5)
    return f"{c[1:]}.HK" if c.startswith("0") else f"{c}.HK"


_PERIOD_MAP = {
    "1m": ("1m", "1d"),
    "5m": ("5m", "5d"),
    "1d": ("1d", "1y"),
}


def _fetch_chart(code: str, interval: str, range_: str,
                 source_key: str = "hk.quote") -> dict | None:
    """打 Yahoo chart 接口。

    `source_key` 只用于被动健康观测(services/source_health.py)—— 缓存命中时
    根本不会走到这里,那也正确:没发生上游调用就没什么可观测的。
    """
    import time as _time
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{_yahoo_symbol(code)}"
    t0 = _time.perf_counter()
    try:
        resp = requests.get(url, params={"interval": interval, "range": range_,
                                         "includePrePost": "false"},
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        resp.raise_for_status()
        result = (resp.json().get("chart") or {}).get("result") or []
        _health(source_key, bool(result), t0,
                "" if result else "chart.result 为空(代码不存在或已退市)")
        return result[0] if result else None
    except Exception as e:
        log.warning("yahoo hk chart %s %s failed: %s", code, interval, e)
        _health(source_key, False, t0, f"{type(e).__name__}: {e}")
        return None


def _health(key: str, ok: bool, t0: float, err: str = "") -> None:
    """观测绝不能影响取数 —— 这里出任何问题都当没发生。"""
    try:
        import time as _time
        from app.services import source_health
        source_health.record(key, ok, (_time.perf_counter() - t0) * 1000, err)
    except Exception:
        pass


def hk_kline(code: str, period: str = "1d", limit: int = 250) -> list[dict]:
    """港股K线，时间升序 [{ts,open,high,low,close,volume}]"""
    if period not in _PERIOD_MAP:
        return []
    code = code.zfill(5)   # 归一化后作缓存key, 700/00700 共享同一条目
    key = f"gm:hkkline:{code}:{period}"
    cached = _cache_get(key)
    if cached is not None:
        return cached[-limit:]
    interval, range_ = _PERIOD_MAP[period]
    chart = _fetch_chart(code, interval, range_, "hk.kline")
    if not chart:
        return []
    ts_list = chart.get("timestamp") or []
    q = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    out = []
    from datetime import datetime, timezone
    for i, ts in enumerate(ts_list):
        o, h, l, c = (q.get("open") or [None])[i], (q.get("high") or [None])[i], \
                     (q.get("low") or [None])[i], (q.get("close") or [None])[i]
        if c is None or o is None:
            continue
        dt = datetime.fromtimestamp(ts, timezone.utc)
        out.append({"ts": dt.isoformat(),
                    "open": round(float(o), 3), "high": round(float(h), 3),
                    "low": round(float(l), 3), "close": round(float(c), 3),
                    "volume": int((q.get("volume") or [0])[i] or 0)})
    _cache_set(key, out, 300 if period in ("1m", "5m") else 1800)
    return out[-limit:]


def hk_quote(code: str) -> dict | None:
    """港股快照（延迟15分钟）：用日线chart的meta字段"""
    code = code.zfill(5)
    key = f"gm:hkquote:{code}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    chart = _fetch_chart(code, "1d", "5d")
    if not chart:
        return None
    meta = chart.get("meta") or {}
    price = meta.get("regularMarketPrice")
    # 昨收: 用日线倒数第二根收盘(盘中时最后一根是当日未完成bar, 收盘后同理成立)
    # 注意 chartPreviousClose 是"5天窗口前"的收盘, 不能当昨收用
    q = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    closes = [c for c in (q.get("close") or []) if c is not None]
    prev = closes[-2] if len(closes) >= 2 else meta.get("previousClose")
    if price is None:
        return None
    mkt_ts = meta.get("regularMarketTime")
    if mkt_ts:
        from datetime import datetime, timezone
        mkt_ts = datetime.fromtimestamp(mkt_ts, timezone.utc).isoformat()
    out = {
        "code": code, "market": "HK", "currency": "HKD",
        "name": meta.get("shortName") or "",
        "price": round(float(price), 3),
        "prev_close": round(float(prev), 3) if prev else None,
        "change_pct": round((price - prev) / prev * 100, 2) if prev else None,
        "ts": mkt_ts, "delayed": True,
    }
    _cache_set(key, out, 60)
    return out
