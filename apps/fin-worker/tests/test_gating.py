"""交易日历闸门：非交易日 / 日历缺数据都**不得**触发。"""

from __future__ import annotations

from app import gating


def test_trading_day_proceeds():
    action, why = gating.gate({"known": True, "trading": True})
    assert action == gating.PROCEED
    assert why is None


def test_non_trading_day_skips():
    action, why = gating.gate({"known": True, "trading": False, "note": "国庆假期"})
    assert action == gating.SKIP_NON_TRADING
    assert why == "国庆假期"


def test_unknown_calendar_skips_and_warns():
    action, why = gating.gate({"known": False, "trading": False})
    assert action == gating.SKIP_UNKNOWN
    assert "未知" in why


def test_unknown_wins_over_trading_flag():
    """缺数据时哪怕 trading 字段被误写成 True，也**不许**放行。"""
    action, _ = gating.gate({"known": False, "trading": True})
    assert action == gating.SKIP_UNKNOWN
