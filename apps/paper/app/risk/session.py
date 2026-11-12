"""风控第 1 条 · 交易时段（按市场取时区与时段）。

判据来自 `fin_market_calendar`（`is_trading` + `sessions`），不猜周末、不猜节假日——
A 股有调休，日历表是唯一权威（`09 §4.3`）。**日历缺失 = 不可交易**：
读不到该市场该日的日历行 → 判为不可交易并写明原因，**绝不退化成「周一至周五即交易日」**
（`apps/fin-worker/app/workflows.py` 已写下这条原则，N2 把它从 ETL 推广到交易本身）。

时区与时段**按标的所属市场取**（二期追加规则 §三）：

| 市场 | 时区 | 时段（当地） | 来源 |
|---|---|---|---|
| `CN_A` | `Asia/Shanghai` | 09:30–11:30 / 13:00–15:00 | `fin_market_rule`（与一期逐条等价） |
| `HK` | `Asia/Hong_Kong` | 09:30–12:00 / 13:00–16:00 | `fin_market_rule` |
| `US` | `America/New_York` | 09:30–16:00（含夏令时） | `fin_market_rule` |

**不再硬编码 `CST = +8`**（原 `:18` 的注释说明那只是因为 slim 镜像没装 tzdata）。
本模块改走 `app.market_time` 的 IANA 时区名；镜像装 `tzdata` 是前置条件。

时段取哪一份：**日历行自带的 `sessions` 优先**（允许逐日覆写，例如港股半日市），
为空时回落到 `fin_market_rule.sessions`（市场常设时段）。半日市没标时按常设时段
「放宽处理」并在回执留痕 —— 与拍板 §3.1 第 4 条一致（不得因此拒绝交易）。
"""

from __future__ import annotations

from typing import Optional

from app.market_time import DEFAULT_MARKET, canonical_market, to_market
from app.risk.result import RiskResult

NAME = "session"

# 市场 → 中文时区标签（仅用于给人看的报错文案；A 股沿用一期原话「上海时间」）。
_TZ_LABEL = {"CN_A": "上海时间", "HK": "香港时间", "US": "美东时间"}


def _label(market: str) -> str:
    return _TZ_LABEL.get(market, market)


def to_market_tz(at, market: str):
    """把 `at` 归一成该市场的当地时间（供测试与调用方复用）。"""
    return to_market(at, market)


def _minutes(hhmm: str) -> int:
    hh, mm = hhmm.strip().split(":")
    return int(hh) * 60 + int(mm)


def in_any_session(sessions, at, market: str = DEFAULT_MARKET) -> bool:
    """`sessions` = `[{"open": "09:30", "close": "11:30"}, ...]`，两端都含。"""
    if not sessions:
        return False
    now = to_market(at, market)
    cur = now.hour * 60 + now.minute
    for window in sessions:
        try:
            if _minutes(window["open"]) <= cur <= _minutes(window["close"]):
                return True
        except (KeyError, TypeError, ValueError):
            # 日历里有一格写坏了：**不放行**——宁可拒绝也不猜一个时段。
            continue
    return False


def resolve_sessions(calendar: Optional[dict], rule_sessions=None) -> list:
    """时段来源：日历行自带优先（可逐日覆写），为空回落市场规则的常设时段。"""
    cal_sessions = (calendar or {}).get("sessions") or []
    if cal_sessions:
        return cal_sessions
    return list(rule_sessions or [])


def check_session(
    calendar: Optional[dict],
    at,
    *,
    market: str = DEFAULT_MARKET,
    timezone_name: Optional[str] = None,   # 兼容形参；真正时区一律由 market 推导
    sessions=None,
) -> RiskResult:
    """`calendar` = `fin_market_calendar` 按 `(market, 当地日期)` 取到的那一行（或 None）。

    `timezone_name` 保留为兼容形参（调用方若显式传了市场的 IANA 名，这里只作为旁证，
    真正时区一律经 `app.market_time`，避免两处各说一套）。
    """
    mkt = canonical_market(market)
    if calendar is None:
        reason = ("没有该日的交易日历，无法判定交易时段（拒绝委托）" if mkt == "CN_A"
                  else f"没有 {mkt} 该日的交易日历，无法判定交易时段（拒绝委托）")
        return RiskResult.reject(NAME, reason, market=mkt, calendar_known=False)

    local = to_market(at, mkt)
    if not calendar.get("is_trading"):
        return RiskResult.reject(
            NAME, f"{local.date()} 是非交易日（休市）",
            market=mkt, calendar_source=calendar.get("calendar_source"),
        )

    windows = resolve_sessions(calendar, sessions)
    if not in_any_session(windows, at, mkt):
        text = "、".join(f"{s.get('open')}-{s.get('close')}" for s in windows) or "（日历未写时段）"
        return RiskResult.reject(
            NAME,
            f"不在交易时段内（{local.strftime('%H:%M')} {_label(mkt)}，当日时段 {text}）",
            market=mkt, calendar_source=calendar.get("calendar_source"),
        )
    return RiskResult.pass_(
        NAME, at=local.isoformat(), market=mkt,
        calendar_source=calendar.get("calendar_source"),
    )
