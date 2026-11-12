"""风控第 1 条 · 交易时段（纯函数，不连库）。"""

from datetime import datetime, timedelta, timezone

from app.risk.session import check_session

CST = timezone(timedelta(hours=8))
SESSIONS = [{"open": "09:30", "close": "11:30"}, {"open": "13:00", "close": "15:00"}]
CAL = {"is_trading": True, "sessions": SESSIONS}


def at(hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(2026, 10, 2, h, m, tzinfo=CST)


def test_no_calendar_rejects():
    r = check_session(None, at("10:00"))
    assert not r.ok and "交易日历" in r.reason


def test_non_trading_day_rejects():
    r = check_session({"is_trading": False, "sessions": []}, at("10:00"))
    assert not r.ok and "非交易日" in r.reason


def test_outside_sessions_rejects():
    r = check_session(CAL, at("12:00"))          # 午休
    assert not r.ok and "不在交易时段" in r.reason


def test_before_open_rejects():
    assert not check_session(CAL, at("09:29")).ok


def test_morning_window_ok():
    assert check_session(CAL, at("09:30")).ok
    assert check_session(CAL, at("10:00")).ok
    assert check_session(CAL, at("11:30")).ok     # 收盘时刻含在内


def test_just_after_morning_close_rejects():
    assert not check_session(CAL, at("11:31")).ok


def test_afternoon_window_ok():
    assert check_session(CAL, at("13:00")).ok
    assert check_session(CAL, at("14:59")).ok
    assert check_session(CAL, at("15:00")).ok


def test_timezone_is_shanghai():
    """UTC 02:00 = 上海 10:00（落在上午时段）；UTC 03:31 = 上海 11:31（已收市）。"""
    assert check_session(CAL, datetime(2026, 10, 2, 2, 0, tzinfo=timezone.utc)).ok
    assert not check_session(CAL, datetime(2026, 10, 2, 3, 31, tzinfo=timezone.utc)).ok


def test_naive_datetime_treated_as_shanghai():
    assert check_session(CAL, datetime(2026, 10, 2, 10, 0)).ok


def test_empty_sessions_rejects():
    assert not check_session({"is_trading": True, "sessions": []}, at("10:00")).ok
