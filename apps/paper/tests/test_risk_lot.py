"""风控第 3 条 · 整手（纯函数，不连库）。"""

from app.risk.lot import check_lot


def test_buy_multiple_of_100_ok():
    assert check_lot("buy", 100).ok
    assert check_lot("buy", 1000).ok


def test_buy_150_rejected():
    r = check_lot("buy", 150)
    assert not r.ok
    assert "100 股的整数倍" in r.reason and "150" in r.reason


def test_buy_one_share_rejected():
    assert not check_lot("buy", 1).ok


def test_buy_zero_or_negative_rejected():
    assert not check_lot("buy", 0).ok
    assert not check_lot("buy", -100).ok


def test_sell_odd_lot_allowed():
    """清仓场景允许零股：卖出不要求整手。"""
    assert check_lot("sell", 150).ok
    assert check_lot("sell", 37).ok


def test_sell_zero_rejected():
    assert not check_lot("sell", 0).ok


def test_custom_lot_size():
    assert check_lot("buy", 200, lot_size=200).ok
    assert not check_lot("buy", 100, lot_size=200).ok


def test_bad_lot_size_rejects_buy():
    assert not check_lot("buy", 100, lot_size=0).ok
