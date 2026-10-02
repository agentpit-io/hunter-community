"""业务契约：StrategyDecision 的校验与翻译。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.bridge.contracts import CONTRACT_VERSION, ContractError, StrategyDecision

BASE = {
    "contract_version": CONTRACT_VERSION,
    "decision_id": "sample-fixed-2026-10-09-0930-601398",
    "strategy_key": "sample-fixed",
    "strategy_version": "1.0",
    "account_version": 3,
    "data_snapshot": {"kind": "project_params"},
    "intent": {"code": "601398", "side": "buy", "qty": 100, "price_type": "market"},
    "valid_until": "2026-10-09T10:00:00+08:00",
}


def _with(**over):
    d = dict(BASE)
    d.update(over)
    return d


def test_roundtrip_and_paper_command():
    d = StrategyDecision.from_dict(BASE)
    cmd = d.to_paper_command(project_id="prj_x", idempotency_key="k1")
    assert cmd["project_id"] == "prj_x"
    assert cmd["code"] == "601398"
    assert cmd["qty"] == 100
    assert cmd["price_type"] == "market"
    assert cmd["source"] == "ai"
    assert cmd["expected_version"] == 3
    assert cmd["idempotency_key"] == "k1"
    assert cmd["decision_ref"] == BASE["decision_id"]
    # 策略版本 / 数据快照 / 契约版本都要留痕（复盘要能追到版本）
    assert cmd["intent_ref"]["strategy"] == "sample-fixed@1.0"
    assert cmd["intent_ref"]["contract_version"] == CONTRACT_VERSION
    assert cmd["intent_ref"]["data_snapshot"] == {"kind": "project_params"}
    # 市价单不带 limit_price
    assert "limit_price" not in cmd


def test_limit_intent_carries_price():
    d = _with(intent={"code": "601398", "side": "sell", "qty": 200,
                      "price_type": "limit", "limit_price": "6.5"})
    cmd = StrategyDecision.from_dict(d).to_paper_command(project_id="p", idempotency_key="k")
    assert cmd["limit_price"] == "6.5"
    assert Decimal(cmd["limit_price"]) == Decimal("6.5")


@pytest.mark.parametrize("missing", ["contract_version", "decision_id", "strategy_key",
                                     "strategy_version", "account_version", "valid_until",
                                     "intent"])
def test_missing_required_fields_rejected(missing):
    d = dict(BASE)
    d.pop(missing)
    with pytest.raises(ContractError):
        StrategyDecision.from_dict(d)


def test_major_version_mismatch_rejected():
    with pytest.raises(ContractError):
        StrategyDecision.from_dict(_with(contract_version="2.0"))


def test_same_major_minor_version_accepted():
    d = StrategyDecision.from_dict(_with(contract_version="1.7"))
    assert d.contract_version == "1.7"


@pytest.mark.parametrize("intent", [
    {"code": "601398", "side": "hold", "qty": 100, "price_type": "market"},
    {"code": "", "side": "buy", "qty": 100, "price_type": "market"},
    {"code": "601398", "side": "buy", "qty": 0, "price_type": "market"},
    {"code": "601398", "side": "buy", "qty": -5, "price_type": "market"},
    {"code": "601398", "side": "buy", "qty": 100, "price_type": "limit"},          # 限价无价
    {"code": "601398", "side": "buy", "qty": 100, "price_type": "market",
     "limit_price": "6.5"},                                                         # 市价带价
    {"code": "601398", "side": "buy", "qty": 100, "price_type": "stop"},           # 未知价型
])
def test_bad_intents_rejected(intent):
    with pytest.raises(ContractError):
        StrategyDecision.from_dict(_with(intent=intent))


def test_negative_account_version_rejected():
    with pytest.raises(ContractError):
        StrategyDecision.from_dict(_with(account_version=-1))


def test_to_dict_is_json_serializable():
    import json
    d = StrategyDecision.from_dict(BASE)
    json.dumps(d.to_dict())  # 不抛即通过
