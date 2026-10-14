"""首页/个股行情缓存的新鲜度判据（**不连库、不联网**）。

只管一件事：一条缓存什么时候该被判"过时"从而去重取。

判据原来只看"是不是今天"，于是**盘中某一刻**写下的快照过了收盘仍被当成最新 ——
2026-10-14 事故:缓存里冻着上午 10:37 的 22.51(松霖科技 603992 那一刻的真实价),
而它当天涨停收 24.61,收盘后用户拿到的还是那个盘中价,一直错到第二天。
这里把那四类边界钉住。
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from app.routers import quote as router

CST = timezone(timedelta(hours=8))


@pytest.fixture
def freeze(monkeypatch):
    """把 router 里的"现在"冻到指定时刻。

    `quote.py` 是 `from datetime import datetime`,所以 router.datetime 是类本身,
    换成一个 now() 被固定的替身即可(和修数据脚本里用的是同一个手法)。
    """
    def _freeze(y, mo, d, h, mi):
        class _FrozenDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(y, mo, d, h, mi, tzinfo=tz or CST)
        monkeypatch.setattr(router, "datetime", _FrozenDT)
    return _freeze


TODAY = "2026-10-14"


def test_yesterday_is_stale(freeze):
    freeze(2026, 10, 14, 10, 40)
    assert router._is_stale({"ts": "2026-10-13 15:00:00", "market": "A"}, TODAY)


def test_intraday_snapshot_before_close_is_fresh(freeze):
    """还在盘中:盘中快照本来就是最新的,不该白打上游。"""
    freeze(2026, 10, 14, 10, 40)
    assert not router._is_stale({"ts": "2026-10-14 10:37:00", "market": "A"}, TODAY)


def test_intraday_snapshot_after_close_is_stale(freeze):
    """事故场景:10:37 写下的快照,收盘后(15:30)再看就是过时的。"""
    freeze(2026, 10, 14, 15, 30)
    assert router._is_stale({"ts": "2026-10-14 10:37:00", "market": "A"}, TODAY)


def test_close_time_snapshot_is_fresh(freeze):
    """收盘后写下的那条(24.61)不该被判过时 —— 否则每来一个请求就打一次上游。"""
    freeze(2026, 10, 14, 22, 30)
    assert not router._is_stale({"ts": "2026-10-14 15:24:00", "market": "A"}, TODAY)


def test_hk_uses_hk_close(freeze):
    """港股 16:00 才收盘:15:30 时港股那条还是新鲜的,A 股那条已经过时。"""
    freeze(2026, 10, 14, 15, 30)
    q = {"ts": "2026-10-14 13:00:00", "market": "HK"}
    assert not router._is_stale(q, TODAY)
    freeze(2026, 10, 14, 16, 30)
    assert router._is_stale(q, TODAY)


def test_unknown_market_keeps_day_granularity(freeze):
    """未收录的市场保持原判据(当天即新鲜),不误伤。"""
    freeze(2026, 10, 14, 22, 30)
    assert not router._is_stale({"ts": "2026-10-14 10:37:00", "market": "US"}, TODAY)


def test_unparsable_ts_is_not_stale(freeze):
    """时间戳只有日期(解析不出时分秒)时当它不过期 —— 宁可漏判,也不要把好缓存反复扔掉打上游。"""
    freeze(2026, 10, 14, 22, 30)
    assert not router._is_stale({"ts": "2026-10-14", "market": "A"}, TODAY)


def test_missing_ts_is_stale(freeze):
    """没有时间戳就当它过期 —— 这是**原有行为**,别改:没时间戳的缓存不该信。"""
    freeze(2026, 10, 14, 22, 30)
    assert router._is_stale({}, TODAY)
