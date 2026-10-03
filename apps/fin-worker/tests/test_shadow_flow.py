"""R7 · 影子验证链路（**不连网、不连库**）：两臂决策构造 + `shadow_step` 编排。

用假客户端（monkeypatch `activities.HunterApiClient` / `activities.PaperClient`）跑一次
完整的 `shadow_step`，断言：

  · 两臂都调了**同一个**策略（不同配置 / 不同现金），并各自交出一笔委托；
  · `shadow_simulate` 收到**两臂**且 `initial_capital` 来自 prepare；
  · 落库的 records **带上了 `validation_id`**（= proposal_id）；
  · 每臂的现金来自**它自己**的 state（不是项目真账本的现金）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import activities  # noqa: E402
from app.strategy import shadow as sh  # noqa: E402


# ── 纯函数 ────────────────────────────────────────────────────────────────

def test_param_of_takes_only_real_values():
    assert sh.param_of({"max_position_pct": 0.2, "stop_loss_pct": 0.05}) == {
        "max_position_pct": 0.2, "max_positions": None}
    assert sh.param_of(None) == {"max_position_pct": None, "max_positions": None}


def test_arm_order_projects_intent():
    d = {"intent": {"side": "buy", "qty": 100, "price_type": "market", "limit_price": None}}
    assert sh.arm_order(d) == {"side": "buy", "qty": 100, "price_type": "market",
                               "limit_price": None}
    assert sh.arm_order(None) is None


def test_build_for_arm_returns_none_when_no_budget():
    """只接限价单的市场 + 买不起一手 → `None`（如实记「本时点无决策」）。"""
    from datetime import datetime, timezone
    order = sh.build_for_arm(
        project={"project_id": "p", "tier": "play", "version": 0}, config={},
        trade_date="2026-10-08", point="HK-0930",
        now=datetime(2026, 10, 8, 9, 30, tzinfo=timezone.utc), code="00700",
        strategy_key="sample-fixed", strategy_version="1.0", ttl_seconds=1800,
        market="HK", market_order_supported=False, reference_price="421",
        lot_size=100, available_cash="1000")     # 买不起 100 股 × 421
    assert order is None


def test_build_for_arm_market_order_uses_tier_lot():
    from datetime import datetime, timezone
    order = sh.build_for_arm(
        project={"project_id": "p", "tier": "manage", "version": 3}, config={},
        trade_date="2026-10-08", point="CN_A-0930",
        now=datetime(2026, 10, 8, 9, 30, tzinfo=timezone.utc), code="601398",
        strategy_key="sample-fixed", strategy_version="1.0", ttl_seconds=1800,
        market="CN_A", market_order_supported=True)
    assert order == {"side": "buy", "qty": 1000, "price_type": "market", "limit_price": None}


# ── shadow_step 编排（假客户端）───────────────────────────────────────────

class _FakeApi:
    last_record = None
    last_prepare = None

    def __init__(self, *a, **k):
        pass

    def evolution_shadow_prepare(self, proposal_id, market=None):
        _FakeApi.last_prepare = (proposal_id, market)
        return {"validation_id": proposal_id, "project_id": "prj_1", "market": market,
                "initial_capital": 100000,
                "base_config": {"stop_loss_pct": 0.08},
                "candidate_config": {"stop_loss_pct": 0.05},
                "states": {"incumbent": {"cash_available": "100000", "cash_frozen": "0",
                                         "positions": []},
                           "candidate": {"cash_available": "50000", "cash_frozen": "0",
                                         "positions": []}}}

    def evolution_shadow_validate(self, proposal_id, market, trade_date):
        return {"started": True}

    def evolution_shadow_record(self, records):
        _FakeApi.last_record = records
        return {"written": len(records), "skipped": 0}


class _FakePaper:
    last_sim = None

    def __init__(self, *a, **k):
        pass

    def get_project(self, project_id, market=None):
        return {"project": {"project_id": project_id, "tier": "play", "version": 0},
                "param": {"strategies": [{"key": "sample-fixed", "version": "1.0",
                                          "active": True}]}}

    def market_rules(self):
        return [{"market": "CN_A", "market_order_supported": True}]

    def shadow_simulate(self, body):
        _FakePaper.last_sim = body
        results = []
        for arm in body["arms"]:
            results.append({
                "arm": arm["arm"],
                "trade_date": body["trade_date"], "point": body["point"],
                "symbol": body["symbol"], "quote_as_of": "2026-10-08T10:00:00+08:00",
                "signal": ({"side": "buy", "qty": arm.get("qty")} if arm["decided"] else None),
                "filled": bool(arm["decided"]), "reject_reason": None,
                "fee": "5.0", "slippage": "0",
                "position": {"cash_available": "1", "cash_frozen": "0", "positions": []},
                "valuation": {"total_assets": "100000"},
            })
        return {"quote_as_of": "2026-10-08T10:00:00+08:00", "snapshot_id": "SNAP-1",
                "snapshot_tradable": True, "gap": False, "results": results}


def test_shadow_step_runs_both_arms_and_records(monkeypatch):
    monkeypatch.setattr(activities, "HunterApiClient", _FakeApi)
    monkeypatch.setattr(activities, "PaperClient", _FakePaper)
    out = activities.shadow_step({
        "project_id": "prj_1", "proposal_id": "evp_x", "market": "CN_A",
        "trade_date": "2026-10-08", "point": "CN_A-0930",
        "now": "2026-10-08T09:30:00+08:00",
    })
    # 两臂都出了决策、都成交
    assert {a["arm"] for a in out["arms"]} == {"incumbent", "candidate"}
    assert all(a["decided"] and a["filled"] for a in out["arms"])
    # shadow_simulate 收到两臂 + prepare 给的初始资金
    sim = _FakePaper.last_sim
    assert [a["arm"] for a in sim["arms"]] == ["incumbent", "candidate"]
    assert sim["initial_capital"] == "100000"
    # 每臂的现金来自**它自己**的 state
    cash = {a["arm"]: a["state"]["cash_available"] for a in sim["arms"]}
    assert cash == {"incumbent": "100000", "candidate": "50000"}
    # 落库 records 带上了 validation_id
    assert [r["validation_id"] for r in _FakeApi.last_record] == ["evp_x", "evp_x"]
    assert out["written"] == 2


def test_market_points_uses_market_rule(monkeypatch):
    monkeypatch.setattr(activities, "PaperClient", _FakePaper)
    pts = activities.market_points({"market": "CN_A"})
    assert pts and all("point" in p and "at" in p for p in pts)
