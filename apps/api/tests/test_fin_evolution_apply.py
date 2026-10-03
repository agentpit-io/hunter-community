# -*- coding: utf-8 -*-
"""R8 · **生效 / 观察 / 回滚 / 回灌**（真库用例；`TEST_DATABASE_URL` 未设时整体 skip）。

    TEST_DATABASE_URL=postgresql://hunter:hunter@127.0.0.1:5598/r8_test \
      cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_evolution_apply.py -q

盯住 R8 的九条硬约束（`plan/R8.md` §三）：

  · **唯一写入口**：`fin_param` 只经 `control.py` 改；仓库里**没有第二处**直改 `strategies`；
  · **CAS**：基线被并发改过 → 旧提案生效被拒（`activate_candidate` 的 `FOR UPDATE`）；
  · **配置改了必有日志**：`active_strategy` 变过而 `change_log` 无对应行的行数 = 0；
  · **放行闸门**：无 passed / 计划过期 / 样本不足 / 触及风控 / 成本模型不符 各拒一次；
  · **观察期口径不变**：生效前后 `plan_hash` 与窗口 / 阈值完全相同；
  · **紧急回滚**：触发 `rollback_line` → 自动回上一已验证版本 + 停模拟下单 + 写失败经验；
  · **回滚核验**：目标不在 `strategies` 里 → 拒绝并告警；
  · **回灌**：经验写失败 → 重试队列 + 「回灌待完成」状态位；恢复后重试成功；
  · **幂等**：同一 apply / rollback 连发两次 → 只生效一次。
"""
from __future__ import annotations

import os
import re
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
import psycopg2.errors  # noqa: E402
import psycopg2.extras  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routers import fin_evolution  # noqa: E402
from app.services.fin import control as C  # noqa: E402
from app.services.fin import evolution as E  # noqa: E402
from app.services.fin import memory as memory_svc  # noqa: E402

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
INTERNAL_KEY = "test-internal-key"

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="需要 TEST_DATABASE_URL 指向一个已跑过 0045 迁移的 postgres")


def _ready() -> bool:
    if not TEST_DATABASE_URL:
        return False
    try:
        conn = psycopg2.connect(TEST_DATABASE_URL)
    except Exception:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('fin_evolution_reinject_task')")
            return cur.fetchone()[0] is not None
    finally:
        conn.close()


if not _ready():
    pytestmark = pytest.mark.skip(reason="库里没有 fin_evolution_reinject_task —— 先跑 0045 迁移")
else:
    E.DATABASE_URL = TEST_DATABASE_URL
    C._schema_done = True                    # 测试库已有列，别再跑 DDL
    memory_svc.DATABASE_URL = TEST_DATABASE_URL
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
BASE_PARAM = dict(stop_loss_pct=0.08, take_profit_pct=0.20, hold_days_max=5,
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
        cur.execute("DELETE FROM fin_project WHERE project_id = %s", (project_id,))
    conn.commit()


@pytest.fixture()
def env():
    """一个项目 + 参数 + 一条账本成交（供回滚失败经验的证据）+ 两条经验 + 一份复盘冻结晶。"""
    uid = "u-" + uuid.uuid4().hex
    tag = uuid.uuid4().hex[:12]
    pid = f"prj_r8_{tag}"
    snap = f"SNAP-r8-{tag}"
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
                        "VALUES (%s, %s, now(), 'r8test', 5.0)", (snap, pid))
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
    cand = mutate(base) if mutate else {**base, "stop_loss_pct": 0.05}
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
            if kind:
                cur.execute("SELECT * FROM fin_evolution_event WHERE payload->>'project_id'=%s "
                            "AND kind=%s ORDER BY created_at, event_id", (pid, kind))
            else:
                cur.execute(
                    "SELECT e.* FROM fin_evolution_event e WHERE e.proposal_id IN "
                    "(SELECT proposal_id FROM fin_evolution_proposal WHERE project_id=%s) "
                    "OR e.payload->>'project_id'=%s ORDER BY e.created_at, e.event_id", (pid, pid))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _apply(proposal_id, base_hash, **over):
    body = {"proposal_id": proposal_id, "expected_base_config_hash": base_hash,
            "actor": "tester", "confirm": True}
    body.update(over)
    return client.post("/api/internal/fin/evolution/apply", json=body, headers=IK)


# ════════════════════════════════════════════════════════════════════════
# 出口标准 4 · 唯一写入口（grep 证明：没有第二处直改 fin_param.strategies）
# ════════════════════════════════════════════════════════════════════════

def test_no_second_writer_of_fin_param_strategies():
    """全仓扫：**写 `fin_param.strategies`** 的只有 `control.py`；`evolution.py` 一行不写 `fin_param`。

    新建项目时 `INSERT INTO fin_param`（`store.py` / seed 脚本）是**出生**不是**改**，不算第二写入点；
    `ledger.py` 的 `UPDATE fin_param SET params_locked_at` 不碰策略参数。
    """
    repo = Path(__file__).resolve().parents[3]
    strat_writers, evo_writes = [], []
    for path in (repo / "apps").rglob("*.py"):
        if ".venv" in path.parts or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(repo).as_posix()
        if "/tests/" in rel or rel.endswith("_test.py"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        code = "\n".join(l.split("#", 1)[0] for l in text.splitlines())
        # 「改 strategies」的具体形态：同一句 UPDATE fin_param 里带上 strategies
        if re.search(r"UPDATE\s+fin_param[^\n]*strategies", code, re.I):
            strat_writers.append(rel)
        if rel.endswith("services/fin/evolution.py") and \
                re.search(r"(UPDATE|INSERT INTO|DELETE FROM)\s+fin_param", code, re.I):
            evo_writes.append(rel)
    assert strat_writers == ["apps/api/app/services/fin/control.py"], strat_writers
    assert evo_writes == [], f"evolution.py 不许写 fin_param：{evo_writes}"


def test_top_fields_cover_whitelist():
    """`control._TOP_FIELDS` 必须覆盖白名单里的**全部顶层字段**（漏一个就静默少落一列）。

    顶层字段 = 三个风控字段 + 三个顶层策略字段；`vol_mult` / `confirm_days` 落在
    `strategies[].params` 里（不是 `fin_param` 的列），**不**在这里。
    """
    expected = set(E._RISK_FIELDS) | {"stop_loss_pct", "take_profit_pct", "hold_days_max"}
    assert set(C._TOP_FIELDS) == expected, (set(C._TOP_FIELDS), expected)
    assert expected <= set(E._STRATEGY_FIELDS) | set(E._RISK_FIELDS)


# ════════════════════════════════════════════════════════════════════════
# 出口标准 5 · 生效实测（四份 SQL）
# ════════════════════════════════════════════════════════════════════════

def test_apply_happy_path_registers_version_and_switches(env):
    prop = _propose(env, plan_overrides={"min_comparable_sample": 1})
    pid = prop["proposal_id"]
    _pass_it(pid)
    _seed_shadow(pid, 1)
    keys_before = [s["key"] for s in _param(env["pid"])["strategies"]]

    r = _apply(pid, prop["base_config_hash"])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["from_key"] == "vb" and out["to_key"].startswith("vb#")

    # ① strategies 多了一个新 key，旧条目一字未动
    items = _param(env["pid"])["strategies"]
    keys_after = [s["key"] for s in items]
    assert set(keys_before) <= set(keys_after) and len(keys_after) == len(keys_before) + 1
    assert keys_after[:len(keys_before)] == keys_before
    # ② active 已切到新 key
    assert _active_key(env["pid"]) == out["to_key"]
    # ③ change_log 有行（active 切换 + 新版本登记 + 止损字段）
    assert [l["field"] for l in _logs(env["pid"], "active_strategy")] == ["active_strategy"]
    assert _logs(env["pid"], f"strategies.{out['to_key']}")
    sl = _logs(env["pid"], "stop_loss_pct")
    assert sl and abs(float(sl[-1]["old_value"]) - 0.08) < 1e-9 and abs(float(sl[-1]["new_value"]) - 0.05) < 1e-9
    # ④ fin_evolution_event 有 applied
    assert any(e["kind"] == "applied" for e in _events(env["pid"]))


def test_apply_is_idempotent_second_call_rejected(env):
    prop = _propose(env, plan_overrides={"min_comparable_sample": 1})
    pid = prop["proposal_id"]
    _pass_it(pid)
    _seed_shadow(pid, 1)
    assert _apply(pid, prop["base_config_hash"]).status_code == 200
    r2 = _apply(pid, prop["base_config_hash"])          # 连发第二次
    assert r2.status_code == 400, r2.text                 # 基线已变（CAS / 计划过期）
    assert len(_logs(env["pid"], "active_strategy")) == 1  # 只生效一次


# ════════════════════════════════════════════════════════════════════════
# 出口标准 2 · CAS 实测（并发人工改策略 → 旧提案生效被拒）
# ════════════════════════════════════════════════════════════════════════

def test_cas_rejects_stale_base_hash(env):
    """直接打 `control.activate_candidate`：基线哈希对不上 → `CasMismatchError`，且没写日志。"""
    base = _base_config(env["pid"])
    stale = "0" * 64
    conn = _conn()
    try:
        with pytest.raises(C.CasMismatchError):
            C.activate_candidate(conn, env["pid"], candidate_config={**base, "stop_loss_pct": 0.05},
                                 expected_base_config_hash=stale, new_key="vb#stale", actor="t")
    finally:
        conn.close()
    assert _logs(env["pid"], "active_strategy") == []
    assert _active_key(env["pid"]) == "vb"


def test_apply_rejected_after_concurrent_manual_change(env):
    """先记 base_hash → 人工改一次策略（模拟并发）→ 旧提案生效 → 被拒。"""
    prop = _propose(env, plan_overrides={"min_comparable_sample": 1})
    pid = prop["proposal_id"]
    _pass_it(pid)
    _seed_shadow(pid, 1)
    base_hash = prop["base_config_hash"]

    # 「并发人工操作」：直接改 fin_param（模拟另一个真人 / 另一个进程）
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE fin_param SET stop_loss_pct = 0.06 WHERE project_id = %s", (env["pid"],))
        conn.commit()
    finally:
        conn.close()

    r = _apply(pid, base_hash)
    assert r.status_code == 400, r.text
    assert "过期" in r.text or "不符" in r.text
    assert _logs(env["pid"], "active_strategy") == []        # 配置没被改
    assert any(e["kind"] == "rejected_by_gate" for e in _events(env["pid"]))


# ════════════════════════════════════════════════════════════════════════
# 出口标准 3 · 日志完整性（配置改了而日志缺失 = 0）
# ════════════════════════════════════════════════════════════════════════

def test_no_config_change_without_log(env):
    prop = _propose(env, plan_overrides={"min_comparable_sample": 1})
    pid = prop["proposal_id"]
    _pass_it(pid)
    _seed_shadow(pid, 1)
    assert _apply(pid, prop["base_config_hash"]).status_code == 200
    E.rollback_proposal(proposal_id=pid, reason="单测", actor="t")

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM fin_param p
                 WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(p.strategies) e
                                WHERE coalesce((e->>'active')::boolean, false))
                   AND NOT EXISTS (SELECT 1 FROM fin_param_change_log l
                                    WHERE l.project_id = p.project_id AND l.field = 'active_strategy')
                """)
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════════
# 出口标准 6 · 放行闸门（各拒一次）
# ════════════════════════════════════════════════════════════════════════

def test_gate_rejects_without_passed_event(env):
    prop = _propose(env, plan_overrides={"min_comparable_sample": 1})
    _seed_shadow(prop["proposal_id"], 1)             # 没有 passed 事件
    r = _apply(prop["proposal_id"], prop["base_config_hash"])
    assert r.status_code == 400 and "passed" in r.text
    assert any(e["kind"] == "rejected_by_gate" for e in _events(env["pid"]))


def test_gate_rejects_insufficient_sample(env):
    prop = _propose(env, plan_overrides={"min_comparable_sample": 5})
    pid = prop["proposal_id"]
    _pass_it(pid)
    _seed_shadow(pid, 1)                              # 只有 1 个可比样本
    r = _apply(pid, prop["base_config_hash"])
    assert r.status_code == 400 and "可比样本" in r.text


def test_gate_rejects_risk_target(env):
    prop = _propose(env, target="risk", rationale="收紧单票上限",
                    mutate=lambda b: {**b, "max_position_pct": 0.15},
                    plan_overrides={"min_comparable_sample": 1})
    pid = prop["proposal_id"]
    _pass_it(pid)
    _seed_shadow(pid, 1)
    r = _apply(pid, prop["base_config_hash"])
    assert r.status_code == 400 and "风控" in r.text


def test_gate_rejects_bad_cost_model(env):
    prop = _propose(env, plan_overrides={"min_comparable_sample": 1, "cost_model": "bogus"})
    pid = prop["proposal_id"]
    _pass_it(pid)
    _seed_shadow(pid, 1)
    r = _apply(pid, prop["base_config_hash"])
    assert r.status_code == 400 and "成本" in r.text


def test_apply_requires_explicit_confirm(env):
    prop = _propose(env, plan_overrides={"min_comparable_sample": 1})
    pid = prop["proposal_id"]
    _pass_it(pid)
    _seed_shadow(pid, 1)
    r = _apply(pid, prop["base_config_hash"], confirm=False)
    assert r.status_code == 400 and "确认" in r.text


# ════════════════════════════════════════════════════════════════════════
# 出口标准 7 · 观察期口径不变（plan_hash 一致）
# ════════════════════════════════════════════════════════════════════════

def test_observation_keeps_frozen_plan(env):
    prop = _propose(env, plan_overrides={"min_comparable_sample": 1})
    pid = prop["proposal_id"]
    _pass_it(pid)
    _seed_shadow(pid, 1)
    before = E.get_proposal(proposal_id=pid)["plan"]
    assert _apply(pid, prop["base_config_hash"]).status_code == 200
    after = E.get_proposal(proposal_id=pid)["plan"]
    for k in ("plan_hash", "window_days", "min_comparable_sample", "pass_line",
              "fail_line", "rollback_line", "early_stop_condition", "metric"):
        assert before[k] == after[k], k
    obs = E.observe_applied(proposal_id=pid, as_of="2026-11-01")
    assert obs["window"]["window_days"] == before["window_days"]
    assert obs["rollback_line"] == float(before["rollback_line"]["max_drawdown_pct"])


# ════════════════════════════════════════════════════════════════════════
# 出口标准 8 / 11 · 紧急回滚（自动）与普通不达标（只告警）
# ════════════════════════════════════════════════════════════════════════

def _apply_ok(env, **over):
    prop = _propose(env, plan_overrides={"min_comparable_sample": 1}, **over)
    pid = prop["proposal_id"]
    _pass_it(pid)
    _seed_shadow(pid, 1)
    r = _apply(pid, prop["base_config_hash"])
    assert r.status_code == 200, r.text
    return pid


def _seed_nav(pid, navs):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            for i, nav in enumerate(navs):
                ta = 100000 * nav
                cur.execute(
                    "INSERT INTO fin_valuation (project_id, market, as_of, cash_available, "
                    "  cash_frozen, market_value, total_assets, nav, price_source, quality) "
                    "VALUES (%s, 'CN_A', %s, %s, 0, 0, %s, %s, 'r8test', 'ok') "
                    "ON CONFLICT (project_id, market, as_of) DO UPDATE SET nav=EXCLUDED.nav, "
                    "  total_assets=EXCLUDED.total_assets",
                    (pid, f"2026-10-{10 + i:02d}T08:00:00+00:00", ta, ta, nav))
        conn.commit()
    finally:
        conn.close()


def test_emergency_rollback_triggers_on_rollback_line(env):
    pid = _apply_ok(env)
    to_key = _active_key(env["pid"])
    # 净值从 1.00 跌到 0.85 → 最大回撤 -15% ≤ rollback_line -10% → 自动回滚
    _seed_nav(env["pid"], [1.00, 0.95, 0.85])

    obs = E.observe_applied(proposal_id=pid, as_of="2026-10-20")
    assert obs["action"] == "rolled_back", obs

    # ① 配置回到上一已验证版本
    assert _active_key(env["pid"]) == "vb" and _active_key(env["pid"]) != to_key
    # ② change_log 有行（active 切回 + 停模拟下单）
    assert len(_logs(env["pid"], "active_strategy")) == 2
    assert any(l["field"] == "auto_enabled" and l["new_value"] is False
               for l in _logs(env["pid"]))
    # ③ rolled_back 事件
    assert any(e["kind"] == "rolled_back" for e in _events(env["pid"]))
    # ④ 一条有证据的失败经验真的落库
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fin_experience WHERE project_id=%s AND polarity='refute'",
                        (env["pid"],))
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT count(*) FROM fin_evolution_reinject_task WHERE proposal_id=%s "
                        "AND status='pending'", (pid,))
            assert cur.fetchone()[0] == 0            # 回灌成功，不留待完成
    finally:
        conn.close()


def test_performance_miss_alerts_without_rollback(env):
    pid = _apply_ok(env)
    to_key = _active_key(env["pid"])
    # 回撤 -9%：越过 early_stop(-8%) 但没到 rollback_line(-10%) → 只告警
    _seed_nav(env["pid"], [1.00, 0.97, 0.91])

    obs = E.observe_applied(proposal_id=pid, as_of="2026-10-20")
    assert obs["action"] == "alert", obs
    assert _active_key(env["pid"]) == to_key         # 配置一动不动
    assert _param(env["pid"])["auto_enabled"] is True
    assert any(e["kind"] == "alert" for e in _events(env["pid"]))
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fin_alert_log WHERE project_id=%s AND sent_at IS NULL",
                        (env["pid"],))
            assert cur.fetchone()[0] == 1            # 告警已入队（宿主投递）
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════════
# 出口标准 9 · 回滚目标核验失败 → 拒绝并告警
# ════════════════════════════════════════════════════════════════════════

def test_rollback_refused_when_target_missing(env):
    pid = _apply_ok(env)
    # 构造：把 from_key（vb）从 strategies 里删掉（模拟版本已被移除）
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT strategies FROM fin_param WHERE project_id=%s", (env["pid"],))
            items = [s for s in cur.fetchone()[0] if s.get("key") != "vb"]
            cur.execute("UPDATE fin_param SET strategies=%s WHERE project_id=%s",
                        (psycopg2.extras.Json(items), env["pid"]))
        conn.commit()
    finally:
        conn.close()

    r = client.post("/api/internal/fin/evolution/rollback",
                    json={"proposal_id": pid, "reason": "t", "actor": "t"}, headers=IK)
    assert r.status_code == 409, r.text
    assert "拒绝回滚" in r.text
    assert any(e["kind"] == "alert" for e in _events(env["pid"]))


def test_rollback_is_idempotent(env):
    pid = _apply_ok(env)
    r1 = client.post("/api/internal/fin/evolution/rollback",
                     json={"proposal_id": pid, "reason": "t", "actor": "t"}, headers=IK)
    assert r1.status_code == 200, r1.text
    n_after_first = len(_logs(env["pid"], "active_strategy"))
    r2 = client.post("/api/internal/fin/evolution/rollback",
                     json={"proposal_id": pid, "reason": "t", "actor": "t"}, headers=IK)
    assert r2.status_code == 409, r2.text              # 版本链对不上（已回滚过）
    assert len(_logs(env["pid"], "active_strategy")) == n_after_first


# ════════════════════════════════════════════════════════════════════════
# 出口标准 10 · 回灌失败 → 重试队列 + 「回灌待完成」状态位
# ════════════════════════════════════════════════════════════════════════

def test_reinject_failure_then_retry(env):
    pid = _apply_ok(env)
    os.environ["FIN_MEMORY_ENABLED"] = "0"             # 让失败经验写不进去（记忆库关着）
    try:
        rb = E.rollback_proposal(proposal_id=pid, reason="t", actor="t")
    finally:
        os.environ["FIN_MEMORY_ENABLED"] = "1"
    assert rb["reinject"]["status"] == "pending"

    # 只读接口报「回灌待完成」
    items = E.list_proposals(project_id=env["pid"], user_id=env["uid"])
    assert items and items[0]["reinject_pending"] is True
    assert E.reinject_pending(proposal_id=pid)

    # 恢复后重试 → 成功，状态位消失
    out = E.retry_reinject(proposal_id=pid)
    assert out == {"tried": 1, "done": 1, "pending": 0}
    assert E.list_proposals(project_id=env["pid"], user_id=env["uid"])[0]["reinject_pending"] is False
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fin_experience WHERE project_id=%s AND polarity='refute'",
                        (env["pid"],))
            assert cur.fetchone()[0] == 1
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════════
# 路由 · JWT 只读通道
# ════════════════════════════════════════════════════════════════════════

def test_read_channel_requires_owner(env):
    pid = _apply_ok(env)
    r = client.get("/api/v1/fin/evolution/proposals", params={"project_id": env["pid"]},
                   headers={"x-test-user": env["uid"]})
    assert r.status_code == 200 and r.json()["items"][0].get("reinject_pending") is False
    assert client.get("/api/v1/fin/evolution/proposals",
                      params={"project_id": env["pid"]}).status_code == 401
    assert client.get("/api/v1/fin/evolution/reinject", params={"proposal_id": pid},
                      headers={"x-test-user": "u-other"}).status_code == 404
