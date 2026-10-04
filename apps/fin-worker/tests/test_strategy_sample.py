"""固定示例策略。"""

from __future__ import annotations

import pytest

from datetime import datetime, timedelta, timezone

from app.bridge.contracts import StrategyDecision
from app.strategy.sample import SampleNoBudget
from app.strategy.sample import SAMPLE_LOTS, build_decision

SH = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 9, 9, 30, tzinfo=SH)


def _build(tier, version=0):
    return build_decision(
        project={"project_id": "prj_1", "tier": tier, "version": version},
        param={"max_position_pct": "0.2", "max_positions": 3},
        trade_date="2026-10-09", point="0930", now=NOW,
        code="601398", strategy_key="sample-fixed", strategy_version="1.0",
        ttl_seconds=1800,
    )


def test_lot_per_tier():
    for tier, lot in SAMPLE_LOTS.items():
        d = StrategyDecision.from_dict(_build(tier))
        assert d.intent.qty == lot
        assert d.intent.side == "buy"
        assert d.intent.code == "601398"


def test_unknown_tier_falls_back_to_smallest_lot():
    d = StrategyDecision.from_dict(_build("mystery"))
    assert d.intent.qty == 100


def test_decision_id_is_deterministic_across_runs():
    a = StrategyDecision.from_dict(_build("play"))
    b = StrategyDecision.from_dict(_build("play"))
    assert a.decision_id == b.decision_id
    assert a.decision_id == "sample-fixed-2026-10-09-0930-601398"


def test_valid_until_is_ttl_after_now():
    d = StrategyDecision.from_dict(_build("play"))
    assert d.valid_until == (NOW + timedelta(seconds=1800)).isoformat()


def test_account_version_carried_through():
    d = StrategyDecision.from_dict(_build("operate", version=7))
    assert d.account_version == 7


def test_snapshot_declares_it_did_not_read_market_data():
    d = StrategyDecision.from_dict(_build("play"))
    assert d.data_snapshot["kind"] == "project_params"
    assert "不读行情" in d.data_snapshot["note"]


# ── P2 · 按市场参数化：只接限价单的市场出限价单、按可用资金定量 ──────────────

def _build_market(market, *, mos, price=None, lot=None, cash=None, tier="manage",
                  pct="0.25"):
    return build_decision(
        project={"project_id": "prj_hk", "tier": tier, "version": 0},
        param={"max_position_pct": pct, "max_positions": 4},
        trade_date="2026-10-09", point="HK-0930", now=NOW,
        code="00700", strategy_key="sample-fixed", strategy_version="1.0",
        ttl_seconds=1800,
        market=market, market_order_supported=mos,
        reference_price=price, lot_size=lot, available_cash=cash,
    )


def test_hk_emits_limit_order_priced_from_reference():
    """只接限价单的市场 → 限价单，限价 = **真实参考价**（不是编的）。"""
    d = StrategyDecision.from_dict(_build_market("HK", mos=False, price="421.2",
                                                 lot=100, cash="1000000"))
    assert d.intent.price_type == "limit"
    assert str(d.intent.limit_price) == "421.2"
    assert d.data_snapshot["market"] == "HK"
    assert d.data_snapshot["reference_price"] == "421.2"


# ── R12 · 策略真读 max_position_pct 定量（只接限价单的市场）───────────────────

def test_hk_qty_uses_max_position_pct():
    """数量 = ⌊可用资金 × max_position_pct × 0.995 ÷（参考价 × 每手股数）⌋ × 每手股数。

    1000000 × 0.25 × 0.995 = 248750；421.2 × 100 = 42120/手 → 5 手 = 500 股。
    """
    d = StrategyDecision.from_dict(_build_market("HK", mos=False, price="421.2",
                                                 lot=100, cash="1000000", pct="0.25"))
    assert d.intent.qty == 500


def test_hk_qty_scales_with_pct():
    """同一笔现金 / 参考价，占比不同 → 数量不同（两臂分叉的根据）。"""
    big = StrategyDecision.from_dict(_build_market("HK", mos=False, price="421.2",
                                                   lot=100, cash="1000000", pct="0.50"))
    small = StrategyDecision.from_dict(_build_market("HK", mos=False, price="421.2",
                                                     lot=100, cash="1000000", pct="0.25"))
    assert big.intent.qty == 1000        # 1000000×0.5×0.995=497500 → 11 手=1100 → 封顶 manage 1000
    assert small.intent.qty == 500
    assert big.intent.qty > small.intent.qty


def test_hk_qty_capped_by_tier_lot():
    """可用资金再多也不超过档位固定手数（manage = 1000）。"""
    d = StrategyDecision.from_dict(_build_market("HK", mos=False, price="10.00",
                                                 lot=100, cash="100000000"))
    assert d.intent.qty == 1000


def test_hk_cannot_afford_one_lot_raises():
    """占比 × 可用资金买不起一手 → `SampleNoBudget`（本时点不出委托，不编数量硬下单）。"""
    with pytest.raises(SampleNoBudget):
        _build_market("HK", mos=False, price="421.2", lot=100, cash="100000", pct="0.25")


def test_position_qty_falls_back_to_tier_lot_when_price_inputs_missing():
    """量价三输入（可用资金 / 参考价 / 每手股数）任一缺失 → 回落档位固定手数（不猜）。"""
    from app.strategy.sample import _position_qty
    # 缺可用资金
    assert _position_qty(available=None, price="421.2", lot=100, cap=1000, pct="0.25") == 1000
    # 缺参考价
    assert _position_qty(available="100000", price=None, lot=100, cap=1000, pct="0.25") == 1000
    # 缺每手股数
    assert _position_qty(available="100000", price="421.2", lot=None, cap=1000, pct="0.25") == 1000
    # 价 / 每手无效（<=0）同样回落
    assert _position_qty(available="100000", price="0", lot=100, cap=1000, pct="0.25") == 1000
    assert _position_qty(available="100000", price="421.2", lot=0, cap=1000, pct="0.25") == 1000


def test_position_qty_missing_pct_uses_old_affordable_口径():
    """占比未配置 → 沿用旧口径（不设占比上限，按可用资金定量）—— 量价输入都在，仍算得出。"""
    from app.strategy.sample import _position_qty
    # 100000×0.995=99500；42120/手 → 2 手 = 200 股（与 R12 之前逐位一致）
    assert _position_qty(available="100000", price="421.2", lot=100, cap=1000, pct=None) == 200
    # 占比为 0 → 买不起（本时点不出委托）
    assert _position_qty(available="100000", price="421.2", lot=100, cap=1000, pct="0") == 0


def test_market_order_market_keeps_one_shot_semantics():
    """支持市价单的市场：价型仍是 market、数量仍是档位固定手数、快照不读行情。"""
    d = StrategyDecision.from_dict(_build_market("CN_A", mos=True, price=None,
                                                 lot=None, cash=None))
    assert d.intent.price_type == "market"
    assert d.intent.qty == 1000
    assert "不读行情" in d.data_snapshot["note"]


def test_market_order_path_ignores_max_position_pct():
    """A 股市价单路径**不读**占比参数：占比从 0.9 掉到 0.1，数量仍是档位固定手数。"""
    for pct in ("0.9", "0.1"):
        d = StrategyDecision.from_dict(_build_market("CN_A", mos=True, pct=pct))
        assert d.intent.qty == 1000
        assert d.intent.price_type == "market"


def test_snapshot_records_params_actually_read():
    """`data_snapshot.params_used` 记录策略真正读的白名单字段（与 shadow.param_of 同清单）。"""
    from app.strategy.sample import STRATEGY_READ_FIELDS
    d = StrategyDecision.from_dict(_build_market("HK", mos=False, price="421.2",
                                                 lot=100, cash="1000000", pct="0.25"))
    assert set(d.data_snapshot["params_used"]) == set(STRATEGY_READ_FIELDS) == {"max_position_pct"}
    assert d.data_snapshot["params_used"]["max_position_pct"] == "0.25"


def test_valid_until_keeps_now_timezone():
    """有效期跟随 `now` 自带的时区（美股不再被折成上海表示）。"""
    from datetime import timezone
    ny = datetime(2026, 10, 9, 9, 30, tzinfo=timezone(timedelta(hours=-4)))
    d = build_decision(
        project={"project_id": "prj_us", "tier": "play", "version": 0}, param=None,
        trade_date="2026-10-09", point="US-0930", now=ny,
        code="AAPL", strategy_key="sample-fixed", strategy_version="1.0",
        ttl_seconds=1800, market="US", market_order_supported=False,
        reference_price="333.69", lot_size=1, available_cash="10000",
    )
    assert d["valid_until"] == (ny + timedelta(seconds=1800)).isoformat()
