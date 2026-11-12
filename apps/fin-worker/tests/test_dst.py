"""夏令时专项（N4 · `plan/N4.md` §三.4）。

**时区一律用 IANA 时区名，`ScheduleSpec(time_zone_name=...)` 由 Temporal 自动跟随夏令时**
—— 所以「夏令时切换日附近的时刻换算正确」这件事，落在两处：

1. 各市场时区名正确（`points`/`schedules` 用 `Asia/Shanghai` / `Asia/Hong_Kong` /
   `America/New_York`，**不写死 ±N 偏移**）；
2. `market_time` 对固定 UTC 时刻，在**切换日两侧**算出不同的本地偏移（美股 EST/EDT）。

用**固定日期**断言（不依赖「现在」）：2026 年美股夏令时 3 月 8 日开始、11 月 1 日结束。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.market_time import MARKET_TZ, to_market

UTC = timezone.utc


def _offset_hours(dt: datetime) -> float:
    return dt.utcoffset().total_seconds() / 3600.0


def test_market_timezones_are_iana_names_not_fixed_offsets():
    assert MARKET_TZ["CN_A"] == "Asia/Shanghai"
    assert MARKET_TZ["HK"] == "Asia/Hong_Kong"
    assert MARKET_TZ["US"] == "America/New_York"


def test_us_spring_forward_boundary():
    """2026-03-08 美股进入夏令时：同日历时的本地偏移从 -05:00 变 -04:00。

    同一「美东 09:30 开盘」对应的 UTC 时刻不同（14:30 冬 vs 13:30 夏）——
    这正是「不能写死 ±N 偏移」的原因。
    """
    before = to_market(datetime(2026, 3, 4, 14, 30, tzinfo=UTC), "US")
    after = to_market(datetime(2026, 3, 11, 13, 30, tzinfo=UTC), "US")
    assert (before.hour, before.minute) == (9, 30)
    assert (after.hour, after.minute) == (9, 30)
    assert _offset_hours(before) == -5.0    # EST
    assert _offset_hours(after) == -4.0     # EDT
    # 切换当天（03-08）前后差一小时
    assert _offset_hours(to_market(datetime(2026, 3, 7, 12, tzinfo=UTC), "US")) == -5.0
    assert _offset_hours(to_market(datetime(2026, 3, 8, 12, tzinfo=UTC), "US")) == -4.0


def test_us_fall_back_boundary():
    """2026-11-01 美股退出夏令时：偏移从 -04:00 变回 -05:00。"""
    assert _offset_hours(to_market(datetime(2026, 10, 31, 12, tzinfo=UTC), "US")) == -4.0
    assert _offset_hours(to_market(datetime(2026, 11, 1, 12, tzinfo=UTC), "US")) == -5.0
    assert _offset_hours(to_market(datetime(2026, 11, 5, 12, tzinfo=UTC), "US")) == -5.0


def test_hk_and_cn_have_no_dst():
    """港股 / A 股无夏令时，全年 +08:00 —— 切换日两侧同一 UTC 时刻的本地时间不变。"""
    for market, tzname in (("HK", "Asia/Hong_Kong"), ("CN_A", "Asia/Shanghai")):
        for day in ((2026, 1, 15), (2026, 7, 15), (2026, 12, 15)):
            dt = to_market(datetime(*day, 4, 0, tzinfo=UTC), market)
            assert _offset_hours(dt) == 8.0, (market, day)
            assert dt.tzinfo is not None
            assert str(dt.tzinfo) == tzname


def test_schedule_timezone_names_match_market_time():
    """Schedule 用的时区名与 `market_time` 同口径（否则调度与账本会差一小时）。"""
    from app import schedules

    by_tz = {s.timezone for s in schedules.point_specs()}
    assert by_tz == {"Asia/Shanghai", "Asia/Hong_Kong", "America/New_York"}
    assert by_tz == set(MARKET_TZ.values())
