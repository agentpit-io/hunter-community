"""风控引擎：六条串联 + decline_reason 汇总（纯函数，不连库）。"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.risk.engine import RiskInputs, evaluate

CST = timezone(timedelta(hours=8))
CAL = {"is_trading": True, "sessions": [{"open": "09:30", "close": "11:30"},
                                        {"open": "13:00", "close": "15:00"}]}
INST = {"limit_up_pct": Decimal("0.10"), "limit_down_pct": Decimal("0.10"), "lot_size": 100}
MODEL = {
    "version": "fee-cn-a-v1",
    "commission_pct": Decimal("0.00025"),
    "commission_min": Decimal("5.00"),
    "stamp_tax_pct": Decimal("0.0005"),
    "transfer_fee_pct": Decimal("0.00001"),
}
# CN_A 市场规则（与 db/migrations/0030 的 CN_A 行逐项一致）—— 六条规则从它取参。
RULE_CN_A = {
    "market": "CN_A", "currency": "CNY", "timezone": "Asia/Shanghai",
    "sessions": [{"open": "09:30", "close": "11:30"}, {"open": "13:00", "close": "15:00"}],
    "sellable_rule": "t_plus_n", "sellable_days": 1,
    "lot_rule": "fixed", "lot_fixed": 100, "price_limit_mode": "pct",
}


def base(**kw):
    params = dict(
        at=datetime(2026, 10, 2, 10, 0, tzinfo=CST),
        side="buy",
        qty=100,
        price=Decimal("10.00"),
        calendar=CAL,
        instrument=INST,
        fee_model=MODEL,
        prev_close=Decimal("10.00"),
        available=Decimal("10000"),
        position_qty=0,
        sellable_qty=0,
        market="CN_A",
        market_rule=RULE_CN_A,
    )
    params.update(kw)
    return RiskInputs(**params)


def test_all_pass_ok_and_fee_computed():
    out = evaluate(base())
    assert out.passed
    assert out.decline_reason is None
    assert out.amount == Decimal("1000.0000")
    assert out.fee.total == Decimal("5.0100")


def test_all_six_rules_run():
    names = [r.name for r in evaluate(base()).results]
    assert names == ["session", "lot", "t1", "price_limit", "fee", "funds"]


def test_multiple_failures_collected_in_reason():
    """数量不整手 + 资金不足：两条都报，而不是只报第一条。"""
    out = evaluate(base(qty=150, available=Decimal("100")))
    assert not out.passed
    assert "整数倍" in out.decline_reason
    assert "可用资金不足" in out.decline_reason
    assert set(out.failed_names) >= {"lot", "funds"}


def test_no_source_branch():
    """`source` 不参与判断：给它任何值，结果都一样（09 §六-10）。"""
    keys = set(RiskInputs.__dataclass_fields__)
    assert "source" not in keys


def test_engine_never_reads_env_or_now():
    import inspect

    from app.risk import engine

    src = inspect.getsource(engine)
    assert "os.environ" not in src and "now()" not in src
