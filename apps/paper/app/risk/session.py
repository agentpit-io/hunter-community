"""风控第 1 条 · 交易时段。

判据来自 `fin_market_calendar`（`is_trading` + `sessions`），不猜周末、不猜节假日——
A 股有调休，日历表是唯一权威（`09 §4.3`）。

时区：**上海时间**。`at` 是 `TIMESTAMPTZ` 读出来带时区的 `datetime`；naive 的按
上海时间解释（调用方明确知道自己在传本机上海时间时用）。中国没有夏令时，
用固定 `+08:00` 而不是 `ZoneInfo("Asia/Shanghai")`——后者要求镜像里有 tzdata，
python:slim 基础镜像不一定带（`apps/api/main.py` 也是这么处理的）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.risk.result import RiskResult

CST = timezone(timedelta(hours=8))
NAME = "session"


def to_cst(at: datetime) -> datetime:
    if at.tzinfo is None:
        return at.replace(tzinfo=CST)
    return at.astimezone(CST)


def _minutes(hhmm: str) -> int:
    hh, mm = hhmm.strip().split(":")
    return int(hh) * 60 + int(mm)


def in_any_session(sessions, at: datetime) -> bool:
    """`sessions` = `[{"open": "09:30", "close": "11:30"}, ...]`，两端都含。"""
    if not sessions:
        return False
    now = to_cst(at)
    cur = now.hour * 60 + now.minute
    for window in sessions:
        try:
            if _minutes(window["open"]) <= cur <= _minutes(window["close"]):
                return True
        except (KeyError, TypeError, ValueError):
            # 日历里有一格写坏了：**不放行**——宁可拒绝也不猜一个时段。
            continue
    return False


def check_session(calendar: dict | None, at: datetime) -> RiskResult:
    if calendar is None:
        return RiskResult.reject(NAME, "没有该日的交易日历，无法判定交易时段（拒绝委托）")
    if not calendar.get("is_trading"):
        return RiskResult.reject(NAME, f"{to_cst(at).date()} 是非交易日（休市）")
    sessions = calendar.get("sessions") or []
    if not in_any_session(sessions, at):
        windows = "、".join(f"{s.get('open')}-{s.get('close')}" for s in sessions) or "（日历未写时段）"
        return RiskResult.reject(
            NAME,
            f"不在交易时段内（{to_cst(at).strftime('%H:%M')} 上海时间，当日时段 {windows}）",
        )
    return RiskResult.pass_(NAME, at=to_cst(at).isoformat())
