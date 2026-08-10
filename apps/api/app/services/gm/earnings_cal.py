"""美股财报日历(gm端) —— Nasdaq 官方 calendar API(免key)。

https://api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD
每天返回全部披露公司; 取未来7个交易日, 每天按市值排序取前30。
Redis缓存6小时。若Nasdaq被墙/失败, 返回空由前端显示"暂无数据"。
"""
import logging
from datetime import date, timedelta
import requests

from app.services.gm.yahoo_hk import _cache_get, _cache_set

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}


def _mcap_to_num(s: str) -> float:
    """'$3,456,789,012' -> 3456789012.0"""
    try:
        return float(s.replace("$", "").replace(",", ""))
    except (ValueError, AttributeError):
        return 0.0


def _fetch_day(d: date) -> list[dict]:
    try:
        r = requests.get("https://api.nasdaq.com/api/calendar/earnings",
                         params={"date": d.isoformat()}, headers=_HEADERS, timeout=12)
        r.raise_for_status()
        rows = ((r.json().get("data") or {}).get("rows")) or []
    except Exception as e:
        log.warning("nasdaq earnings %s failed: %s", d, e)
        return None   # None=拉取失败(供partial统计); []=当天真无披露
    out = []
    for row in rows:
        out.append({
            "symbol": row.get("symbol", ""),
            "name": (row.get("companyName") or row.get("name") or "")[:40],
            "when": {"time-pre-market": "盘前", "time-after-hours": "盘后"}.get(
                row.get("time", ""), ""),
            "eps_forecast": row.get("epsForecast", ""),
            "mcap": _mcap_to_num(row.get("marketCap", "")),
        })
    out.sort(key=lambda x: -x["mcap"])
    return out[:30]


def week_earnings_meta() -> dict:
    """未来7个自然日(跳过周末)财报日历 + 数据完整性标记。
    返回 {"days": [{date, weekday, items:[...]}], "partial": bool}
    partial=True 表示部分日期拉取失败(如Nasdaq限流), 前端应提示数据不完整。"""
    key = "gm:earnings:week2"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    out, failed = [], 0
    d = date.today()
    wd_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    for i in range(8):
        cur = d + timedelta(days=i)
        if cur.weekday() >= 5:
            continue
        items = _fetch_day(cur)
        if items is None:
            failed += 1
            continue
        if items:
            out.append({"date": cur.isoformat(), "weekday": wd_cn[cur.weekday()],
                        "items": items})
    meta = {"days": out, "partial": failed > 0}
    # 失败时缩短缓存, 尽快重试补齐
    _cache_set(key, meta, 1800 if failed else 21600)
    return meta


def week_earnings() -> list[dict]:
    return week_earnings_meta()["days"]
