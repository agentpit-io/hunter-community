"""风控第 4 条 · 涨跌停（纯函数，不连库）。"""

from decimal import Decimal

from app.risk.price_limit import check_price_limit, limit_prices

MAIN = {"limit_up_pct": Decimal("0.10"), "limit_down_pct": Decimal("0.10")}
STAR = {"limit_up_pct": Decimal("0.20"), "limit_down_pct": Decimal("0.20")}


def test_limit_prices_main_board():
    up, down = limit_prices("10.00", MAIN["limit_up_pct"], MAIN["limit_down_pct"])
    assert up == Decimal("11.00") and down == Decimal("9.00")


def test_limit_prices_round_half_up():
    """10.125 × 1.10 = 11.1375 → 11.14（四舍五入，不是银行家舍入）。"""
    up, _ = limit_prices("10.125", MAIN["limit_up_pct"], MAIN["limit_down_pct"])
    assert up == Decimal("11.14")


def test_missing_instrument_rejects():
    r = check_price_limit(None, "buy", "10.00", "10.00")
    assert not r.ok
    assert "元数据" in r.reason and "绝不猜涨跌幅" in r.reason


def test_missing_prev_close_rejects():
    r = check_price_limit(MAIN, "buy", "10.00", None)
    assert not r.ok and "前收盘价" in r.reason


def test_buy_at_limit_up_ok():
    assert check_price_limit(MAIN, "buy", "11.00", "10.00").ok


def test_buy_above_limit_up_rejects():
    r = check_price_limit(MAIN, "buy", "11.01", "10.00")
    assert not r.ok and "涨停价" in r.reason


def test_sell_at_limit_down_ok():
    assert check_price_limit(MAIN, "sell", "9.00", "10.00").ok


def test_sell_below_limit_down_rejects():
    r = check_price_limit(MAIN, "sell", "8.99", "10.00")
    assert not r.ok and "跌停价" in r.reason


def test_star_board_wider_limit():
    """科创板 20%：主板会拒的 11.50，科创板放行。"""
    assert not check_price_limit(MAIN, "buy", "11.50", "10.00").ok
    assert check_price_limit(STAR, "buy", "11.50", "10.00").ok
