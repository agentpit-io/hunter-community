"""`app.market_time` · 按市场时区归一（纯函数，不连库）。

盯住四件会静默出错的事：
  · 三个市场的时区各是哪个（拿 A 股时区顶替港美股 = 编数据）；
  · **夏令时**：美股冬夏偏移不同，写死 `-5` 或 `-4` 必错一个；
  · `market_of` 的代码形态判据（5 位数字 = 港股）；
  · 不认识的市场代号要**抛错**，不静默回落到 A 股。
"""

from datetime import date, datetime, timezone

import pytest

from app.market_time import (
    UnknownMarket,
    canonical_market,
    market_local_date,
    market_of,
    market_tz,
    to_market,
    tz_name,
)


def test_canonical_market_aliases():
    for raw in ("CN_A", "cn_a", "A", "a", "cn", "CN"):
        assert canonical_market(raw) == "CN_A"
    assert canonical_market("hk") == "HK"
    assert canonical_market("US") == "US"


def test_unknown_market_raises():
    with pytest.raises(UnknownMarket):
        canonical_market("LSE")
    with pytest.raises(UnknownMarket):
        market_tz("")


def test_tz_names():
    assert tz_name("CN_A") == "Asia/Shanghai"
    assert tz_name("HK") == "Asia/Hong_Kong"
    assert tz_name("US") == "America/New_York"


def test_to_market_shanghai():
    # UTC 02:00 → 上海 10:00
    dt = to_market(datetime(2026, 10, 2, 2, 0, tzinfo=timezone.utc), "CN_A")
    assert (dt.hour, dt.minute) == (10, 0)
    assert dt.utcoffset().total_seconds() == 8 * 3600


def test_hk_shares_offset_but_is_its_own_tz():
    dt = to_market(datetime(2026, 10, 2, 2, 0, tzinfo=timezone.utc), "HK")
    assert (dt.hour, dt.minute) == (10, 0)
    assert dt.utcoffset().total_seconds() == 8 * 3600


def test_us_dst_winter_and_summer():
    """2026-01-15 是美东冬令时（EST, -05:00）；2026-07-15 是夏令时（EDT, -04:00）。"""
    winter = to_market(datetime(2026, 1, 15, 14, 30, tzinfo=timezone.utc), "US")
    assert (winter.hour, winter.minute) == (9, 30)
    assert winter.utcoffset().total_seconds() == -5 * 3600

    summer = to_market(datetime(2026, 7, 15, 13, 30, tzinfo=timezone.utc), "US")
    assert (summer.hour, summer.minute) == (9, 30)
    assert summer.utcoffset().total_seconds() == -4 * 3600


def test_us_dst_spring_forward_day():
    """2026-03-08 是美股夏令时切换日：当天 09:30 ET 已是 EDT（-04:00）。"""
    dt = to_market(datetime(2026, 3, 8, 13, 30, tzinfo=timezone.utc), "US")
    assert dt.hour == 9 and dt.utcoffset().total_seconds() == -4 * 3600


def test_naive_interpreted_as_market_local():
    naive = datetime(2026, 10, 2, 10, 0)
    assert to_market(naive, "US").hour == 10     # 视为美东本地时间，不换算
    assert to_market(naive, "CN_A").hour == 10


def test_market_local_date_shifts_across_midnight():
    """美股 16:00 ET 收盘时，上海已是次日 —— 日历日期必须按美东切。"""
    us_close = datetime(2026, 10, 2, 16, 0, tzinfo=market_tz("US"))
    assert market_local_date(us_close, "US") == date(2026, 10, 2)
    assert market_local_date(us_close, "CN_A") == date(2026, 10, 3)


def test_market_of_code_shapes():
    assert market_of("600519") == "CN_A"
    assert market_of("000001") == "CN_A"
    assert market_of("00700") == "HK"          # 5 位数字 = 港股（与 fin_data 同口径）
    assert market_of("00700.HK") == "HK"
    assert market_of("AAPL") == "US"
    assert market_of("AAPL.US") == "US"
    assert market_of("") == "CN_A"
