"""L03 · 决策上下文（技术方案 §10.3）—— `StrategyDecision` 里的「出身证」字段。

覆盖：`mode`（显式）/ `portfolio_version`（§10.3 名，= `account_version`）/ `decision_as_of`
（本次决策允许使用信息的截止时间）/ `data_snapshot_id`（绑定不可变数据快照，L04 起用）。

不变的红线：**拿不到真值就 `NULL`** —— 这里断言「没给就是空 / None」，不是「给个默认填满」。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.bridge.contracts import CONTRACT_VERSION, StrategyDecision
from app.strategy.sample import build_decision

SH = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 9, 9, 30, tzinfo=SH)

BASE = {
    "contract_version": CONTRACT_VERSION,
    "decision_id": "d1",
    "strategy_key": "sample-fixed",
    "strategy_version": "1.0",
    "account_version": 3,
    "data_snapshot": {"kind": "project_params"},
    "intent": {"code": "601398", "side": "buy", "qty": 100, "price_type": "market"},
    "valid_until": "2026-10-09T10:00:00+08:00",
}


def _cmd(**over):
    d = dict(BASE)
    d.update(over)
    return StrategyDecision.from_dict(d).to_paper_command(project_id="p", idempotency_key="k")


# ── ① mode：显式声明，不靠「没配就是 PAPER」────────────────────────────────

def test_mode_lands_in_intent_ref_explicitly():
    ref = _cmd()["intent_ref"]
    assert ref["mode"] == "PAPER"


def test_mode_defaults_to_paper_for_legacy_contract():
    # 老契约（没这个字段）也要能进来；但**出口一定带出去**（决策对象始终声明模式）。
    ref = _cmd(mode=None)["intent_ref"]
    assert ref["mode"] == "PAPER"


# ── ② portfolio_version：§10.3 的名字，= account_version（只加别名）────────

def test_portfolio_version_equals_account_version():
    ref = _cmd(account_version=7)["intent_ref"]
    assert ref["portfolio_version"] == 7


def test_portfolio_version_explicit_value_preserved():
    d = StrategyDecision.from_dict({**BASE, "portfolio_version": 42})
    assert d.portfolio_version == 42
    assert d.to_paper_command(project_id="p", idempotency_key="k")["intent_ref"][
        "portfolio_version"] == 42


# ── ③ decision_as_of：能推出就写口径；推不出留空 → NULL ───────────────────

def test_decision_as_of_carried_through():
    ref = _cmd(decision_as_of="2026-10-09T09:30:00+08:00")["intent_ref"]
    assert ref["decision_as_of"] == "2026-10-09T09:30:00+08:00"


def test_decision_as_of_absent_is_null_not_forged():
    # 没给 → 空串 → 落库 None（**不是**拿 now() 填一个假的）。
    ref = _cmd()["intent_ref"]
    assert ref["decision_as_of"] is None


# ── ④ data_snapshot_id：示例策略不绑 → NULL，不编假快照键 ──────────────────

def test_data_snapshot_id_absent_is_null():
    assert _cmd()["intent_ref"]["data_snapshot_id"] is None


def test_data_snapshot_id_carried_when_present():
    ref = _cmd(data_snapshot_id="DSNAP-abc")["intent_ref"]
    assert ref["data_snapshot_id"] == "DSNAP-abc"


# ── ⑤ 示例策略**显式**写出这三样（不是靠下游补默认）────────────────────────

def _build(version=0):
    return build_decision(
        project={"project_id": "prj_1", "tier": "play", "version": version},
        param={"max_position_pct": "0.2", "max_positions": 3},
        trade_date="2026-10-09", point="0930", now=NOW,
        code="601398", strategy_key="sample-fixed", strategy_version="1.0",
        ttl_seconds=1800,
    )


def test_sample_decision_writes_mode_and_portfolio_version_explicitly():
    d = _build(version=5)
    assert d["mode"] == "PAPER"
    assert d["portfolio_version"] == 5
    assert d["account_version"] == 5


def test_sample_decision_decision_as_of_is_decision_moment():
    d = _build()
    assert d["decision_as_of"] == NOW.isoformat()


def test_sample_decision_does_not_bind_a_data_snapshot():
    # 示例策略只读项目参数，不绑定数据集快照 → 不写 data_snapshot_id（落库 NULL）。
    d = _build()
    assert "data_snapshot_id" not in d


def test_sample_decision_roundtrips_through_contract():
    d = _build(version=4)
    decision = StrategyDecision.from_dict(d)
    assert decision.mode == "PAPER"
    assert decision.portfolio_version == 4
    assert decision.decision_as_of == NOW.isoformat()
    assert decision.data_snapshot_id == ""
