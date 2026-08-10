"""美港股交易时段判断（gm 端专用）。

现有 collector._is_trading_time 只有 A股/港股的北京时间硬编码，无美股。
本模块用 zoneinfo 按交易所本地时间判断，自动处理夏令时。

MVP 简化：不含节假日表——周内假期会误判为交易时段，但下游拉不到新数据
无副作用；节假日表后续接 pandas_market_calendars 补。
"""
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

_NY = ZoneInfo("America/New_York")
_HK = ZoneInfo("Asia/Hong_Kong")


def us_market_state(now_utc: datetime | None = None) -> str:
    """美股状态: pre_market / open / after_hours / closed（纽约时间，自动夏令时）"""
    now = (now_utc or datetime.now(timezone.utc)).astimezone(_NY)
    if now.weekday() >= 5:
        return "closed"
    t = now.time()
    if time(4, 0) <= t < time(9, 30):
        return "pre_market"
    if time(9, 30) <= t < time(16, 0):
        return "open"
    if time(16, 0) <= t < time(20, 0):
        return "after_hours"
    return "closed"


def hk_market_state(now_utc: datetime | None = None) -> str:
    """港股状态: open / lunch / closed"""
    now = (now_utc or datetime.now(timezone.utc)).astimezone(_HK)
    if now.weekday() >= 5:
        return "closed"
    t = now.time()
    if time(9, 30) <= t < time(12, 0) or time(13, 0) <= t < time(16, 0):
        return "open"
    if time(12, 0) <= t < time(13, 0):
        return "lunch"
    return "closed"


def market_state(market: str) -> str:
    if market == "US":
        return us_market_state()
    if market == "HK":
        return hk_market_state()
    return "unknown"


def state_label(market: str, state: str) -> str:
    """给前端显示的中文标签"""
    labels = {
        "pre_market": "盘前", "open": "交易中", "after_hours": "盘后",
        "lunch": "午间休市", "closed": "休市",
    }
    return labels.get(state, state)


_ISO = {"pre_market": "PRE", "open": "REGULAR", "after_hours": "POST",
        "lunch": "LUNCH", "closed": "CLOSED"}


def state_iso(state: str) -> str:
    """业界通用大写枚举(REGULAR/PRE/POST/CLOSED/LUNCH), 供API消费者使用"""
    return _ISO.get(state, state.upper())
