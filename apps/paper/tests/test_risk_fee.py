"""风控第 5 条 · 费用（纯函数，不连库）。"""

from decimal import Decimal

from app.risk.fee import check_fee_model, compute_fee

MODEL = {
    "version": "fee-cn-a-v1",
    "commission_pct": Decimal("0.00025"),
    "commission_min": Decimal("5.00"),
    "stamp_tax_pct": Decimal("0.0005"),
    "transfer_fee_pct": Decimal("0.00001"),
}


def test_buy_fee_commission_hits_minimum():
    # 金额 1000 → 佣金理论 0.25，低于最低 5 元 → 取 5
    f = compute_fee("buy", Decimal("1000"), MODEL)
    assert f.commission == Decimal("5.0000")
    assert f.stamp_tax == Decimal("0.0000")          # 买入不收印花税
    assert f.transfer_fee == Decimal("0.0100")       # 1000 × 万分之 0.1
    assert f.total == Decimal("5.0100")


def test_sell_fee_includes_stamp_tax():
    f = compute_fee("sell", Decimal("100000"), MODEL)
    assert f.commission == Decimal("25.0000")        # 100000 × 万分之 2.5
    assert f.stamp_tax == Decimal("50.0000")         # 100000 × 千分之 0.5
    assert f.transfer_fee == Decimal("1.0000")       # 100000 × 万分之 0.1
    assert f.total == Decimal("76.0000")


def test_commission_above_minimum_is_proportional():
    f = compute_fee("buy", Decimal("40000"), MODEL)
    assert f.commission == Decimal("10.0000")        # 40000 × 0.00025 = 10 > 5


def test_amounts_are_decimal_not_float():
    f = compute_fee("sell", Decimal("12345.67"), MODEL)
    assert isinstance(f.commission, Decimal)
    assert isinstance(f.total, Decimal)
    # 精度到 4 位（NUMERIC(18,4)），没有浮点尾数
    assert f.total == f.total.quantize(Decimal("0.0001"))


def test_fee_model_missing_rejects():
    r = check_fee_model(None)
    assert not r.ok and "费用模型" in r.reason


def test_fee_model_incomplete_rejects():
    assert not check_fee_model({"version": "x"}).ok


def test_fee_model_ok_reports_version():
    r = check_fee_model(MODEL)
    assert r.ok and r.detail["version"] == "fee-cn-a-v1"
