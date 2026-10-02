"""风控第 6 条 · 资金与持仓校验（纯函数，不连库）。"""

from decimal import Decimal

from app.risk.funds import check_funds


def test_buy_enough_cash_ok():
    assert check_funds("buy", Decimal("1000"), Decimal("5.01"), Decimal("10000"), 0, 100).ok


def test_buy_exactly_enough_ok():
    assert check_funds("buy", Decimal("1000"), Decimal("5.01"), Decimal("1005.01"), 0, 100).ok


def test_buy_insufficient_cash_rejects():
    r = check_funds("buy", Decimal("1000"), Decimal("5.01"), Decimal("1005.00"), 0, 100)
    assert not r.ok and "可用资金不足" in r.reason


def test_buy_fee_counts_toward_requirement():
    """资金刚好够成交价、但不够费用 → 拒绝。"""
    r = check_funds("buy", Decimal("1000"), Decimal("5.01"), Decimal("1000.00"), 0, 100)
    assert not r.ok


def test_sell_with_position_ok():
    assert check_funds("sell", Decimal("1000"), Decimal("0"), Decimal("0"), 100, 100).ok


def test_sell_more_than_position_rejects():
    r = check_funds("sell", Decimal("1000"), Decimal("0"), Decimal("0"), 100, 200)
    assert not r.ok and "持仓不足" in r.reason


def test_unknown_side_rejects():
    assert not check_funds("hold", Decimal("1"), Decimal("0"), Decimal("999"), 0, 0).ok
