"""固定示例策略。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.bridge.contracts import StrategyDecision
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
