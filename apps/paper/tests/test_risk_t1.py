"""风控第 2 条 · T+1（纯函数，不连库）。"""

from app.risk.t1 import check_t1


def test_buy_never_blocked_by_t1():
    assert check_t1("buy", 1000, 0).ok


def test_sell_within_sellable_ok():
    assert check_t1("sell", 100, 100).ok
    assert check_t1("sell", 100, 500).ok


def test_sell_over_sellable_rejects():
    r = check_t1("sell", 200, 100)
    assert not r.ok
    assert "可卖数量不足" in r.reason and "T+1" in r.reason


def test_sell_all_when_nothing_sellable_rejects():
    assert not check_t1("sell", 1, 0).ok


def test_unknown_side_rejects():
    assert not check_t1("hold", 1, 1).ok
