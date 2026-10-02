"""按市场时区归一（二期追加规则 §六-12）。

⚠️ 本文件在 `apps/paper/app/market_time.py` 与 `apps/fin-worker/app/market_time.py`
**逐字节相同**：两个服务各有各的构建上下文（`apps/paper/` / `apps/fin-worker/`），
不能互相 import，只能各留一份同实现。`apps/paper/tests/test_market_time_parity.py`
逐字节比对这两份文件，谁漂了谁红。

**禁止任何地方再用 `timedelta(hours=±N)` 手工算时区** —— 那是 M7「美股 `event_time`
时差 12 小时」的根因。要某市场的当地时间，一律经本模块。

为什么用 `zoneinfo`（而不是固定偏移）：美股有夏令时，同一时刻冬夏偏移不同；
`Asia/Shanghai` / `Asia/Hong_Kong` 无夏令时，但**语义上是市场时区**，
统一走 IANA 时区名，不写死 `+8`。运行镜像必须装 `tzdata`（slim 基础镜像不带，
见 `apps/*/Dockerfile`），启动自检会真取一次 `America/New_York` 把它验出来。
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

# 本仓统一的三个市场代号 → IANA 时区名（与 `fin_market_rule.timezone` 同口径）。
MARKET_TZ: dict[str, str] = {
    "CN_A": "Asia/Shanghai",
    "HK": "Asia/Hong_Kong",
    "US": "America/New_York",
}

DEFAULT_MARKET = "CN_A"

# 边界归一：代码形态 / 调度用的 `a` / `cn` / `hk` / `us` → 本仓统一的三值。
# A 股在一期里有 `A`（`store._market_of` 的旧值）与 `cn`（同步通道用语）两种写法，
# 一并收进来，避免每处调用点各写一套映射。
_ALIASES: dict[str, str] = {
    "A": "CN_A", "CN": "CN_A", "CN_A": "CN_A", "CN-A": "CN_A",
    "HK": "HK", "US": "US",
}

_TZ_CACHE: dict[str, ZoneInfo] = {}


class UnknownMarket(ValueError):
    """不认识的市场代号。**不猜**（猜错 = 拿别的市场的时区/规则顶替）。"""


def canonical_market(market) -> str:
    """把各种写法归一成 `CN_A` / `HK` / `US`。不认识的直接抛错。"""
    key = str(market or "").strip().upper()
    try:
        return _ALIASES[key]
    except KeyError:
        raise UnknownMarket(f"未知市场 {market!r}（只认 CN_A / HK / US）") from None


def tz_name(market) -> str:
    return MARKET_TZ[canonical_market(market)]


def market_tz(market) -> ZoneInfo:
    """该市场的 IANA 时区对象。进程内缓存（`ZoneInfo` 构造有系统调用）。"""
    name = tz_name(market)
    tz = _TZ_CACHE.get(name)
    if tz is None:
        try:
            tz = ZoneInfo(name)
        except Exception as exc:  # noqa: BLE001 —— tzdata 缺失就是这里爆
            raise RuntimeError(
                f"取不到时区 {name!r}：镜像缺少 tzdata（slim 基础镜像不带）。"
                "见 apps/paper/Dockerfile · apps/fin-worker/Dockerfile 的 tzdata 安装。"
            ) from exc
        _TZ_CACHE[name] = tz
    return tz


def to_market(at: datetime, market) -> datetime:
    """把任意时刻归一成该市场的当地时间。

    **naive 的 `datetime` 视为「该市场本地时间」**（调用方明确知道自己在传某个市场的
    本地时刻时用它）——这与原来 `session.to_cst` 对 naive 的处理一致，只是市场可变。
    """
    tz = market_tz(market)
    if at.tzinfo is None:
        return at.replace(tzinfo=tz)
    return at.astimezone(tz)


def market_local_date(at: datetime, market) -> date:
    """`at` 在 `market` 当地是哪一天（日历按 `(market, 当地日期)` 读）。"""
    return to_market(at, market).date()


def market_of(code: str) -> str:
    """由代码形态判市场（与 `apps/api` 的 `fin_data.market_of` **同口径**）。

    纯数字：5 位 = 港股，其余（含 6 位 A 股与其指数、历史遗留码）= A 股；
    含字母：`.HK` = 港股、`.US` 或裸字母 = 美股。空串按 A 股（沿用 `fin_data` 口径）。
    """
    s = str(code or "").strip().upper()
    if not s:
        return DEFAULT_MARKET
    if s.endswith(".HK"):
        return "HK"
    if s.endswith(".US"):
        return "US"
    bare = s.split(".")[0]
    if bare.isdigit():
        return "HK" if len(bare) == 5 else "CN_A"
    return "US"
