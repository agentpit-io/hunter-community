# -*- coding: utf-8 -*-
"""L13 · **自动生效链路**（验证通过 → 自动应用）。真库用例（`TEST_DATABASE_URL` 未设时整体 skip）。

    TEST_DATABASE_URL=postgresql://hunter:hunter@127.0.0.1:5598/l13_api \
      cd apps/api && PYTHONPATH=. .venv/bin/python -m pytest tests/test_fin_evolution_autoapply.py -q

盯住 L13 的六条纪律（`plan/L13.md` §三.D / §四）：

  · **只对收紧**：`direction='tighten'` + `target='strategy'` → 自动生效；`loosen` / `mixed` → 被拒；
  · **风控不走此路**：`target='risk'` 一律被拒（红线 8 / 9）；
  · **开关是前提**：项目自动生效关着 → 什么都不做（不写事件、不改配置）；
  · **闸门没被绕过**：没有 `passed` / 样本不足 → 自动也上不去；
  · **留痕一眼看出是机器做的**：`fin_evolution_event.actor='system:auto-apply'`、
    生效事件 `payload.auto is True`、`fin_param_change_log.actor='system:auto-apply'`；
  · **人走的入口一字未改**：`/internal/fin/evolution/apply` 仍要显式 `confirm=true`。

模型照抄 `test_fin_evolution_apply.py`（同一套 `env` fixture 与 `_propose` 工具）。
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

_API_ROOT = Path(__file__).resolve().parents[1]
for _p in ("", "/", str(_API_ROOT)):
    while _p in sys.path:
        sys.path.remove(_p)
sys.path.insert(0, str(_API_ROOT))
_bad_app = sys.modules.get("app")
if _bad_app is not None and Path(getattr(_bad_app, "__file__", "") or "").parent == _API_ROOT:
    del sys.modules["app"]

os.environ.setdefault("FIN_EVOLUTION_MODE", "observe")

psycopg2 = pytest.importorskip("psycopg2")
import psycopg2.extras  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routers import fin_evolution  # noqa: E402
from app.services.fin import control as C  # noqa: E402
from app.services.fin import evolution as E  # noqa: E402
from app.services.fin import memory as memory_svc  # noqa: E402
from app.services.fin import switches as switches_svc  # noqa: E402

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
INTERNAL_KEY = "test-internal-key"
AUTO_ACTOR = "system:auto-apply"

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="需要 TEST_DATABASE_URL 指向一个已跑过 0055 迁移的 postgres")


def _ready() -> bool:
    if not TEST_DATABASE_URL:
        return False
    try:
        conn = psycopg2.connect(TEST_DATABASE_URL)
    except Exception:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('fin_memory_switch')")
            return cur.fetchone()[0] is not None
    finally:
        conn.close()


if not _ready():
    pytestmark = pytest.mark.skip(reason="库里没有 fin_memory_switch —— 先跑 0055 迁移")
else:
    E.DATABASE_URL = TEST_DATABASE_URL
    memory_svc.DATABASE_URL = TEST_DATABASE_URL
    switches_svc.DATABASE_URL = TEST_DATABASE_URL     # 按项目覆盖层读的是测试库
    C._schema_done = True                             # 测试库已有列，别再跑 DDL
    fin_evolution._INTERNAL_KEY = INTERNAL_KEY

app = FastAPI()


@app.middleware("http")
async def fake_auth(request: Request, call_next):
    request.state.user_id = request.headers.get("x-test-user") or None
    return await call_next(request)


app.include_router(fin_evolution.router, prefix="/api")
client = TestClient(app, raise_server_exceptions=False)
IK = {"X-Hunter-Internal-Key": INTERNAL_KEY}

BASE_STRATEGY = {"key": "vb", "name": "量价突破", "version": "v1",
                 "params": {"vol_mult": 2.0, "confirm_days": 1}}
BASE_PARAM = dict(stop_loss_pct=-0.08, take_profit_pct=0.20, hold_days_max=5,
                  max_position_pct=0.20, daily_loss_halt_pct=-0.03,
                  account_drawdown_halt_pct=-0.08)


def _conn():
    return psycopg2.connect(TEST_DATABASE_URL)


# ── 清理 ────────────────────────────────────────────────────────────────

def _purge(conn, project_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE fin_evolution_plan DISABLE TRIGGER fin_evolution_plan_immutable")
        cur.execute("ALTER TABLE fin_evolution_event DISABLE TRIGGER fin_evolution_event_immutable")
        cur.execute(
            "DELETE FROM fin_evolution_plan WHERE proposal_id IN "
            "(SELECT proposal_id FROM fin_evolution_proposal WHERE project_id = %s)",
            (project_id,))
        cur.execute(
            "DELETE FROM fin_evolution_event WHERE proposal_id IN "
            "(SELECT proposal_id FROM fin_evolution_proposal WHERE project_id = %s)"
            " OR payload->>'project_id' = %s", (project_id, project_id))
        cur.execute("DELETE FROM fin_evolution_shadow_event WHERE validation_id IN "
                    "(SELECT proposal_id FROM fin_evolution_proposal WHERE project_id = %s)",
                    (project_id,))
        cur.execute("DELETE FROM fin_evolution_reinject_task WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM fin_evolution_proposal WHERE project_id = %s", (project_id,))
        cur.execute("ALTER TABLE fin_evolution_plan ENABLE TRIGGER fin_evolution_plan_immutable")
        cur.execute("ALTER TABLE fin_evolution_event ENABLE TRIGGER fin_evolution_event_immutable")
        cur.execute("DELETE FROM fin_experience_evidence WHERE experience_id IN "
                    "(SELECT experience_id FROM fin_experience WHERE project_id = %s)", (project_id,))
        cur.execute("DELETE FROM fin_memory_snapshot WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM fin_experience WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM fin_param_change_log WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM fin_param WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM fin_trade WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM fin_order WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM fin_valuation WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM fin_snapshot WHERE code = %s", (project_id,))
        cur.execute("DELETE FROM fin_memory_switch_log WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM fin_memory_switch WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM fin_project WHERE project_id = %s", (project_id,))
    conn.commit()


@pytest.fixture()
def env():
    """一个项目 + 参数 + 证据 + 复盘冻结晶（与 `test_fin_evolution_apply.py` 同构）。"""
    uid = "u-" + uuid.uuid4().hex
    tag = uuid.uuid4().hex[:12]
    pid = f"prj_l13_{tag}"
    snap = f"SNAP-l13-{tag}"
    order = f"ord_{tag}"
    trade = f"trd_{tag}"
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO fin_project (project_id, user_id, tier, initial_capital, "
                        "  market_scope) VALUES (%s, %s, 'play', 100000, 'CN_A')", (pid, uid))
            cur.execute(
                "INSERT INTO fin_param (project_id, stop_loss_pct, take_profit_pct, hold_days_max, "
                "  max_positions, max_position_pct, min_order_amount, daily_max_new, daily_max_orders, "
                "  daily_loss_halt_pct, account_drawdown_halt_pct, strategies) "
                "VALUES (%s, %s, %s, %s, 5, %s, 1000, 5, 10, %s, %s, %s)",
                (pid, BASE_PARAM["stop_loss_pct"], BASE_PARAM["take_profit_pct"],
                 BASE_PARAM["hold_days_max"], BASE_PARAM["max_position_pct"],
                 BASE_PARAM["daily_loss_halt_pct"], BASE_PARAM["account_drawdown_halt_pct"],
                 psycopg2.extras.Json([dict(BASE_STRATEGY)])))
            cur.execute("INSERT INTO fin_snapshot (snapshot_id, code, snapshot_time, source, last_price) "
                        "VALUES (%s, %s, now(), 'l13test', 5.0)", (snap, pid))
            cur.execute("INSERT INTO fin_order (order_id, project_id, code, side, qty, price_type, "
                        "  status, filled_qty, source, actor, market) "
                        "VALUES (%s, %s, '601398', 'buy', 100, 'limit', 'filled', 100, 'ai', 'system', 'CN_A')",
                        (order, pid))
            cur.execute("INSERT INTO fin_trade (trade_id, order_id, project_id, code, side, qty, price, "
                        "  amount, snapshot_id, source, fee_model_version, traded_at, market, currency) "
                        "VALUES (%s, %s, %s, '601398', 'buy', 100, 5.0, 500, %s, 'ai', "
                        "  'fee-model-v1', now(), 'CN_A', 'CNY')", (trade, order, pid, snap))
        conn.commit()

        exp = memory_svc.append_evidence(
            caller="internal", project_id=pid, kind="verified",
            statement="甲股缩量整理后追高的回撤概率上升", method="回测", sample_size=30,
            polarity="support", regime_tags=["bull"], symbols=["CN_A:601398"],
            as_of="2026-09-01T15:00:00+08:00",
            evidence=[{"evidence_kind": "snapshot", "ref_id": snap}])
        review = memory_svc.query(project_id=pid, caller="internal", freeze=True, purpose="review")
    finally:
        conn.close()
    ctx = {"uid": uid, "pid": pid, "snap": snap, "trade": trade,
           "exp": exp["experience_id"], "review_snap": review["memory_snapshot_id"]}
    yield ctx
    conn = _conn()
    try:
        _purge(conn, pid)
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _auto_apply_ceiling_on(monkeypatch):
    """天花板默认开（`FIN_AUTO_APPLY=1`）—— 用例自己控制「项目关掉」那一档。"""
    monkeypatch.setenv("FIN_AUTO_APPLY", "1")
    switches_svc.invalidate_switch_cache()
    yield
    switches_svc.invalidate_switch_cache()


# ── 工具 ────────────────────────────────────────────────────────────────

def _base_config(pid):
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            return E.read_config(cur, pid)
    finally:
        conn.close()


def _propose(env, *, mutate=None, target="strategy", plan_overrides=None, rationale="止损收紧"):
    """走唯一入口 `E.propose` 建一条提案。`mutate(base) -> dict` 给出候选完整配置。"""
    base = _base_config(env["pid"])
    cand = mutate(base) if mutate else {**base, "stop_loss_pct": -0.05}
    return E.propose(
        project_id=env["pid"], evidence_refs=[env["exp"]],
        evidence_snapshot_id=env["review_snap"], candidate_config=cand,
        param_diff=E.config_diff(base, cand), target=target, regime_tags=["bull"],
        rationale=rationale, created_by="test", plan_overrides=plan_overrides)


def _pass_it(proposal_id, note="通过"):
    return E.append_event(proposal_id=proposal_id, kind="passed", payload={"note": note})


def _seed_shadow(proposal_id, n=1):
    recs = []
    for i in range(n):
        d = f"2026-10-{10 + (i % 18):02d}"
        for arm in ("incumbent", "candidate"):
            recs.append({
                "validation_id": proposal_id, "arm": arm, "trade_date": d,
                "point": "CN_A-0930", "symbol": f"60139{i % 10}", "quote_as_of": "2026-10-09T08:00:00+00:00",
                "signal": {"side": "buy", "qty": 100, "note": "t"}, "filled": False,
                "reject_reason": "风控拒绝（测试）", "fee": None, "slippage": None,
                "position": None, "valuation": None})
    return E.record_shadow_events(records=recs)


def _param(pid):
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM fin_param WHERE project_id = %s", (pid,))
            return dict(cur.fetchone())
    finally:
        conn.close()


def _active_key(pid):
    return (C.active_strategy({"strategies": _param(pid)["strategies"]}) or {}).get("key")


def _logs(pid, field=None):
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if field:
                cur.execute("SELECT * FROM fin_param_change_log WHERE project_id=%s AND field=%s "
                            "ORDER BY changed_at, id", (pid, field))
            else:
                cur.execute("SELECT * FROM fin_param_change_log WHERE project_id=%s "
                            "ORDER BY changed_at, id", (pid,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _events(pid, kind=None):
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT e.* FROM fin_evolution_event e WHERE (e.proposal_id IN "
                "(SELECT proposal_id FROM fin_evolution_proposal WHERE project_id=%s) "
                " OR e.payload->>'project_id'=%s) "
                + ("AND e.kind=%s " if kind else "")
                + "ORDER BY e.created_at, event_id",
                ((pid, pid, kind) if kind else (pid, pid)))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _status_of(pid):
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT status FROM fin_evolution_proposal WHERE proposal_id=%s", (pid,))
            row = cur.fetchone()
            return row["status"] if row else None
    finally:
        conn.close()


def _auto(proposal_id, **over):
    return client.post(f"/api/internal/fin/evolution/{proposal_id}/auto-apply",
                       json=over, headers=IK)


def _human_apply(proposal_id, base_hash, **over):
    body = {"proposal_id": proposal_id, "expected_base_config_hash": base_hash,
            "actor": "tester", "confirm": True}
    body.update(over)
    return client.post("/api/internal/fin/evolution/apply", json=body, headers=IK)


def _auto_skip_events(pid):
    return [e for e in _events(pid, "rejected_by_gate")
            if (e.get("payload") or {}).get("stage") == "auto-apply"]


# ════════════════════════════════════════════════════════════════════════
# ① 收紧 + 验证通过 + 开关开 → 自动生效成功
# ════════════════════════════════════════════════════════════════════════

def test_auto_apply_tighten_passed_applies_and_traces_system(env):
    prop = _propose(env, plan_overrides={"min_comparable_sample": 1})
    pid = prop["proposal_id"]
    assert prop["direction"] == "tighten" and prop["target"] == "strategy"
    _pass_it(pid)
    _seed_shadow(pid, 1)
    sl_before = float(_param(env["pid"])["stop_loss_pct"])

    r = _auto(pid)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["auto_applied"] is True and out["skipped"] is False
    assert out["from_key"] == "vb" and out["to_key"].startswith("vb#")

    # ① 参数真的变了（止损 -0.08 → -0.05）
    assert float(_param(env["pid"])["stop_loss_pct"]) == -0.05 and sl_before == -0.08
    assert _active_key(env["pid"]) == out["to_key"]

    # ② 生效事件：actor 写清是机器做的 + payload.auto=True + applied_by=system:auto-apply
    applied = [e for e in _events(env["pid"]) if e["kind"] == "applied"]
    assert len(applied) == 1
    ev = applied[0]
    assert ev["actor"] == AUTO_ACTOR
    payload = ev["payload"]
    assert payload["auto"] is True and payload["applied_by"] == AUTO_ACTOR
    assert payload["from_key"] == "vb"

    # ③ 参数改动日志的 actor 同样写清来源
    act = _logs(env["pid"], "active_strategy")
    assert len(act) == 1 and act[0]["actor"] == AUTO_ACTOR
    sl = _logs(env["pid"], "stop_loss_pct")
    assert sl and sl[-1]["actor"] == AUTO_ACTOR


# ════════════════════════════════════════════════════════════════════════
# ② 放宽 / 混合 → 被拒 + 留痕（状态仍是等人确认）
# ════════════════════════════════════════════════════════════════════════

def test_auto_apply_loosen_rejected_with_trace_and_status_kept(env):
    # stop_loss_pct 越接近 0 越紧 → -0.12 是**放宽**
    prop = _propose(env, mutate=lambda b: {**b, "stop_loss_pct": -0.12},
                    rationale="放宽止损", plan_overrides={"min_comparable_sample": 1})
    pid = prop["proposal_id"]
    assert prop["direction"] == "loosen"
    _pass_it(pid)
    _seed_shadow(pid, 1)

    r = _auto(pid)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["auto_applied"] is False and out["skipped"] is True
    assert "tighten" in out["reason"] and "loosen" in out["reason"]

    # 配置一动不动
    assert float(_param(env["pid"])["stop_loss_pct"]) == -0.08
    assert _active_key(env["pid"]) == "vb"
    assert not [e for e in _events(env["pid"]) if e["kind"] == "applied"]

    # 留痕：rejected_by_gate / stage=auto-apply / actor=system:auto-apply
    skips = _auto_skip_events(env["pid"])
    assert len(skips) == 1
    assert skips[0]["actor"] == AUTO_ACTOR
    assert skips[0]["payload"]["direction"] == "loosen"
    assert skips[0]["payload"]["target"] == "strategy"

    # 状态仍是「等人确认」：被自动拒绝 ≠ 提案被驳回
    assert _status_of(pid) == "passed"


def test_auto_apply_mixed_rejected(env):
    # 止损收紧（-0.08→-0.05）+ 持有天数放宽（5→10）= 混合
    prop = _propose(env, mutate=lambda b: {**b, "stop_loss_pct": -0.05, "hold_days_max": 10},
                    rationale="收紧止损但拉长持有", plan_overrides={"min_comparable_sample": 1})
    pid = prop["proposal_id"]
    assert prop["direction"] == "mixed"
    _pass_it(pid)
    _seed_shadow(pid, 1)

    r = _auto(pid)
    assert r.status_code == 200 and r.json()["skipped"] is True
    assert "mixed" in r.json()["reason"]
    assert not [e for e in _events(env["pid"]) if e["kind"] == "applied"]
    assert len(_auto_skip_events(env["pid"])) == 1
    assert _status_of(pid) == "passed"


# ════════════════════════════════════════════════════════════════════════
# ③ 闸门没被绕过：验证没通过 → 自动也上不去
# ════════════════════════════════════════════════════════════════════════

def test_auto_apply_without_passed_event_rejected(env):
    prop = _propose(env, plan_overrides={"min_comparable_sample": 1})
    pid = prop["proposal_id"]
    _seed_shadow(pid, 1)                                     # 没有 passed 事件

    r = _auto(pid)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["auto_applied"] is False and out["skipped"] is True
    assert "passed" in out["reason"]
    assert float(_param(env["pid"])["stop_loss_pct"]) == -0.08       # 没生效
    # 闸门拒绝由 apply_proposal 自己留痕（rejected_by_gate）
    assert [e for e in _events(env["pid"], "rejected_by_gate")]


def test_auto_apply_insufficient_sample_rejected(env):
    prop = _propose(env, plan_overrides={"min_comparable_sample": 5})
    pid = prop["proposal_id"]
    _pass_it(pid)
    _seed_shadow(pid, 1)                                     # 只有 1 个可比样本

    r = _auto(pid)
    assert r.status_code == 200 and r.json()["skipped"] is True
    assert "可比样本" in r.json()["reason"]
    assert float(_param(env["pid"])["stop_loss_pct"]) == -0.08


# ════════════════════════════════════════════════════════════════════════
# ④ 风控类参数一律不走此路（红线 8 / 9）
# ════════════════════════════════════════════════════════════════════════

def test_auto_apply_risk_target_rejected(env):
    # 单票上限 0.20 → 0.15 = 收紧（risk.compare 认「越小越紧」）
    prop = _propose(env, target="risk", rationale="收紧单票上限",
                    mutate=lambda b: {**b, "max_position_pct": 0.15},
                    plan_overrides={"min_comparable_sample": 1})
    pid = prop["proposal_id"]
    assert prop["target"] == "risk"
    _pass_it(pid)
    _seed_shadow(pid, 1)

    r = _auto(pid)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["auto_applied"] is False and out["skipped"] is True
    assert "风控" in out["reason"] and "strategy" in out["reason"]
    assert float(_param(env["pid"])["max_position_pct"]) == 0.20      # 风控参数一动不动
    skips = _auto_skip_events(env["pid"])
    assert len(skips) == 1 and skips[0]["payload"]["target"] == "risk"


# ════════════════════════════════════════════════════════════════════════
# ⑤ 项目关了自动生效 → 什么都不做
# ════════════════════════════════════════════════════════════════════════

def test_auto_apply_switch_off_does_nothing(env):
    prop = _propose(env, plan_overrides={"min_comparable_sample": 1})
    pid = prop["proposal_id"]
    _pass_it(pid)
    _seed_shadow(pid, 1)

    # 按项目关掉自动生效（走 L12 的唯一写入口；天花板仍是开）
    conn = _conn()
    try:
        switches_svc.set_project_switch(conn, env["pid"], switch_key=switches_svc.SWITCH_AUTO_APPLY,
                                        value=False, actor="tester", reason="L13 出口验收")
    finally:
        conn.close()
    switches_svc.invalidate_switch_cache(env["pid"])
    assert switches_svc.auto_apply_effective(env["pid"]) is False

    events_before = len(_events(env["pid"]))
    r = _auto(pid)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["auto_applied"] is False and out["skipped"] is True
    assert "未开启自动生效" in out["reason"]

    # **什么都不做**：没有新事件、没改配置、没有参数改动日志
    assert len(_events(env["pid"])) == events_before
    assert not _auto_skip_events(env["pid"])
    assert float(_param(env["pid"])["stop_loss_pct"]) == -0.08
    assert _active_key(env["pid"]) == "vb"
    assert not _logs(env["pid"])
    assert _status_of(pid) == "passed"


# ════════════════════════════════════════════════════════════════════════
# ⑥ 人走的入口一字未改：仍要显式 confirm=true
# ════════════════════════════════════════════════════════════════════════

def test_human_apply_still_requires_explicit_confirm(env):
    prop = _propose(env, plan_overrides={"min_comparable_sample": 1})
    pid = prop["proposal_id"]
    _pass_it(pid)
    _seed_shadow(pid, 1)
    r = _human_apply(pid, prop["base_config_hash"], confirm=False)
    assert r.status_code == 400 and "确认" in r.text
    assert float(_param(env["pid"])["stop_loss_pct"]) == -0.08


def test_auto_apply_requires_internal_key(env):
    prop = _propose(env, plan_overrides={"min_comparable_sample": 1})
    r = client.post(f"/api/internal/fin/evolution/{prop['proposal_id']}/auto-apply", json={})
    assert r.status_code == 401


def test_auto_apply_missing_proposal_is_404():
    r = _auto("evp_does_not_exist")
    assert r.status_code == 404
