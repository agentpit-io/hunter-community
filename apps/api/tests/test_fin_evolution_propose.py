# -*- coding: utf-8 -*-
"""L01 · 自动提案 + 「实验」实体（`TEST_DATABASE_URL` 未设时 DB 部分整体 skip）。

    TEST_DATABASE_URL=postgresql://hunter:hunter@127.0.0.1:5598/l01_test \
      cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_evolution_propose.py -q

盯住四件事（本段不变量）：

  · **纯函数**：可用经验的判定 / 分组 / 目标字段 / 收紧一档（不连库，先跑）；
  · **闸门**：经验库关着 → 不提；预算用尽 → 不提；证据不足 → 不提（都带**看得见的原因**）；
  · **预算真的拦得住**：本该 2 条候选，预算 1 → 只回 1 条；
  · **「实验」表**：能写、能读回、**`inconclusive` 合法落库**、只追加触发器拒改拒删。

⚠️ 不与 `fin_evolution_plan` 混淆：那是不可变冻结计划（红线 10），实验是另一张表、另一套语义。
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

# 进化模式默认 off（fail-safe）；经验库默认关 —— 测试显式开成 observe / 1。
os.environ.setdefault("FIN_EVOLUTION_MODE", "observe")
os.environ.setdefault("FIN_MEMORY_ENABLED", "1")

from app.services.fin import evolution as E  # noqa: E402
from app.services.fin import memory as memory_svc  # noqa: E402

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()


# ════════════════════════════════════════════════════════════════════════
# 一 · 纯函数（不连库，永远跑）
# ════════════════════════════════════════════════════════════════════════

def _exp(**over):
    base = {"experience_id": "exp_x", "kind": "verified", "status": "已确认",
            "polarity": "refute", "sample_size": 30, "strategy_keys": ["vb"],
            "regime_tags": ["bull"]}
    base.update(over)
    return base


def test_eligible_requires_verified_confirmed_refute_enough_sample():
    items = [
        _exp(),                                               # ✅
        _exp(kind="fact"),                                    # ✗ 不是 verified
        _exp(status="待验证"),                                 # ✗ 不是已确认
        _exp(polarity="support"),                             # ✗ 不是失败经验
        _exp(polarity=None),                                  # ✗ 没有 polarity
        _exp(sample_size=5),                                  # ✗ 样本不足
        _exp(sample_size=None),                               # ✗ 样本未知
    ]
    got = E._eligible_evidence(items, min_sample=20)
    assert [i["experience_id"] for i in got] == ["exp_x"]


def test_group_key_is_first_strategy_key():
    assert E._group_key(_exp(strategy_keys=["vb", "ab"])) == "vb"
    assert E._group_key(_exp(strategy_keys=[])) == ""
    assert E._group_key(_exp(strategy_keys=None)) == ""


def test_propose_field_prefers_strategy_params_then_default():
    base = {"stop_loss_pct": -0.08, "strategies.vb.params.vol_mult": 2.0,
            "strategies.vb.params.confirm_days": 1}
    # 该策略的白名单参数里字典序第一个
    assert E._propose_field_for("vb", base) == "strategies.vb.params.confirm_days"
    # 未知策略键 → 兜底字段
    assert E._propose_field_for("zzz", base) == "stop_loss_pct"
    assert E._propose_field_for("", base) == "stop_loss_pct"


def test_tighten_moves_toward_safe_side_and_clamps():
    sl = E.whitelist_for("stop_loss_pct")[2]              # 负数，越接近 0 越紧
    assert E._tighten(-0.08, sl) == pytest.approx(-0.03)  # -0.08 + 0.05
    assert E._tighten(-0.006, sl) == pytest.approx(-0.005)  # 夹在 max 上
    assert E._tighten(-0.005, sl) is None                 # 已在最紧端点 → 没有余地
    cd = E.whitelist_for("confirm_days")[2]               # 整数，越小越松 → 收紧=加
    assert E._tighten(1, cd) == pytest.approx(3.0)
    assert isinstance(E._tighten(1, cd), float)


def test_int_env_readers_fall_back_to_default(monkeypatch):
    for bad in ("", "abc", "-5"):
        monkeypatch.setenv(E.PROPOSE_MIN_SAMPLE_ENV, bad)
        assert E.propose_min_sample() == E.PROPOSE_MIN_SAMPLE_DEFAULT == 20
    monkeypatch.setenv(E.PROPOSE_MIN_SAMPLE_ENV, "5")
    assert E.propose_min_sample() == 5
    monkeypatch.setenv(E.PROPOSE_BUDGET_ENV, "3")
    assert E.propose_budget() == 3
    monkeypatch.setenv(E.PROPOSE_WINDOW_HOURS_ENV, "0")   # 下限 1
    assert E.propose_window_hours() == E.PROPOSE_WINDOW_HOURS_DEFAULT == 24


# ════════════════════════════════════════════════════════════════════════
# 二 · 真库：闸门 + 实验表（无 TEST_DATABASE_URL 则整体 skip）
# ════════════════════════════════════════════════════════════════════════

psycopg2 = pytest.importorskip("psycopg2")
import psycopg2.extras  # noqa: E402


def _tables_ready() -> bool:
    if not TEST_DATABASE_URL:
        return False
    try:
        conn = psycopg2.connect(TEST_DATABASE_URL)
    except Exception:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('fin_evolution_experiment'), "
                        "to_regclass('fin_evolution_proposal')")
            a, b = cur.fetchone()
            return a is not None and b is not None
    finally:
        conn.close()


_db = pytest.mark.skipif(not _tables_ready(),
                         reason="需要 TEST_DATABASE_URL 指向一个已跑过 0047 迁移的 postgres")

if _tables_ready():
    E.DATABASE_URL = TEST_DATABASE_URL
    memory_svc.DATABASE_URL = TEST_DATABASE_URL


def _conn():
    return psycopg2.connect(TEST_DATABASE_URL)


def _purge(conn, project_id: str) -> None:
    """清掉测试项目的全部痕迹。**必须先关触发器**才能删 plan/event/experiment。"""
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE fin_evolution_experiment DISABLE TRIGGER fin_evolution_experiment_immutable")
        cur.execute("ALTER TABLE fin_evolution_plan DISABLE TRIGGER fin_evolution_plan_immutable")
        cur.execute("ALTER TABLE fin_evolution_event DISABLE TRIGGER fin_evolution_event_immutable")
        cur.execute("DELETE FROM fin_evolution_experiment WHERE proposal_id IN "
                    "(SELECT proposal_id FROM fin_evolution_proposal WHERE project_id = %s)",
                    (project_id,))
        cur.execute("DELETE FROM fin_evolution_plan WHERE proposal_id IN "
                    "(SELECT proposal_id FROM fin_evolution_proposal WHERE project_id = %s)",
                    (project_id,))
        cur.execute("DELETE FROM fin_evolution_event WHERE proposal_id IN "
                    "(SELECT proposal_id FROM fin_evolution_proposal WHERE project_id = %s)",
                    (project_id,))
        cur.execute("DELETE FROM fin_evolution_event WHERE payload->>'project_id' = %s", (project_id,))
        cur.execute("DELETE FROM fin_evolution_proposal WHERE project_id = %s", (project_id,))
        cur.execute("ALTER TABLE fin_evolution_experiment ENABLE TRIGGER fin_evolution_experiment_immutable")
        cur.execute("ALTER TABLE fin_evolution_plan ENABLE TRIGGER fin_evolution_plan_immutable")
        cur.execute("ALTER TABLE fin_evolution_event ENABLE TRIGGER fin_evolution_event_immutable")
        cur.execute("DELETE FROM fin_experience_evidence WHERE experience_id IN "
                    "(SELECT experience_id FROM fin_experience WHERE project_id = %s)", (project_id,))
        cur.execute("DELETE FROM fin_memory_snapshot WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM fin_experience WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM fin_param WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM fin_snapshot WHERE source = 'l01test' AND code = %s", (project_id,))
        cur.execute("DELETE FROM fin_project WHERE project_id = %s", (project_id,))
    conn.commit()


@pytest.fixture()
def env():
    """一个项目 + 参数 + **一条可用的失败经验**（verified / 已确认 / refute / sample 30）。"""
    uid = "u-" + uuid.uuid4().hex
    tag = uuid.uuid4().hex[:12]
    pid = f"prj_l01_{tag}"
    snap = f"SNAP-l01-{tag}"

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO fin_project (project_id, user_id, tier, initial_capital) "
                        "VALUES (%s, %s, 'play', 100000)", (pid, uid))
            cur.execute(
                "INSERT INTO fin_param (project_id, stop_loss_pct, take_profit_pct, hold_days_max, "
                "  max_positions, max_position_pct, min_order_amount, daily_max_new, daily_max_orders, "
                "  daily_loss_halt_pct, account_drawdown_halt_pct, strategies) "
                "VALUES (%s, -0.08, 0.20, 5, 5, 0.20, 1000, 5, 10, -0.03, -0.08, %s)",
                (pid, psycopg2.extras.Json([
                    {"key": "vb", "name": "量价突破", "version": "v1",
                     "params": {"vol_mult": 2.0, "confirm_days": 1}},
                ])))
            cur.execute("INSERT INTO fin_snapshot (snapshot_id, code, snapshot_time, source) "
                        "VALUES (%s, %s, now(), 'l01test')", (snap, pid))
        conn.commit()

        exp = memory_svc.append_evidence(
            caller="internal", project_id=pid, kind="verified",
            statement="放量突破后三日内的回撤比预期更大",
            method="回测", sample_size=42, polarity="refute", regime_tags=["bull"],
            symbols=["CN_A:600519"], strategy_keys=["vb"],
            as_of="2026-09-02T15:00:00+08:00",
            evidence=[{"evidence_kind": "snapshot", "ref_id": snap}])
    finally:
        conn.close()

    ctx = {"uid": uid, "pid": pid, "snap": snap, "exp": exp["experience_id"]}
    yield ctx
    conn = _conn()
    try:
        _purge(conn, pid)
    finally:
        conn.close()


@_db
def test_candidates_skip_when_evolution_disabled(env, monkeypatch):
    """经验库关着 → **不提**，原因看得见（R20 口径：不烧钱、不写库）。"""
    monkeypatch.setenv("FIN_EVOLUTION_MODE", "off")
    out = E.propose_candidates(project_id=env["pid"], market="CN_A")
    assert out["action"] == "skip"
    assert "关闭" in out["reason"] and out["drafts"] == []


@_db
def test_candidates_skip_when_no_eligible_evidence(env, monkeypatch):
    """证据不足（把样本门槛抬到经验之上）→ **不提**，且不硬造提案。"""
    monkeypatch.setenv(E.PROPOSE_MIN_SAMPLE_ENV, "999")
    out = E.propose_candidates(project_id=env["pid"], market="CN_A")
    assert out["action"] == "skip"
    assert "没有可用的失败经验" in out["reason"]
    assert out["min_sample"] == 999


@_db
def test_candidates_produce_a_draft_and_pass_the_gates(env):
    """有可用经验 → 产出一条**能过 propose 全部闸门**的候选（方向恒为收紧）。"""
    out = E.propose_candidates(project_id=env["pid"], market="CN_A")
    assert out["action"] == "propose", out["reason"]
    assert len(out["drafts"]) == 1
    draft = out["drafts"][0]
    # 该策略的白名单参数（字典序第一个）被**收紧**：confirm_days 1 → 3
    assert draft["target"] == "strategy"
    assert draft["project_id"] == env["pid"]          # 写入口 `ProposalIn` 的必填字段
    assert draft["param_diff"] == [{"field": "strategies.vb.params.confirm_days",
                                    "old": 1, "new": 3}]
    # 真过一遍唯一写入口（语义不变），证明候选是**真的可提**
    saved = E.propose(
        project_id=env["pid"], evidence_refs=draft["evidence_refs"],
        evidence_snapshot_id=draft["evidence_snapshot_id"],
        candidate_config=draft["candidate_config"], param_diff=draft["param_diff"],
        target=draft["target"], regime_tags=draft["regime_tags"],
        rationale=draft["rationale"], created_by="l01test", proposal_id=draft["proposal_id"])
    assert saved["direction"] == "tighten"
    assert saved["proposal_id"] == draft["proposal_id"]


@_db
def test_draft_is_accepted_by_the_write_endpoint_schema(env):
    """**跨层**：候选 dict 必须能原样过 `ProposalIn`（少了 `project_id` 就会 422）。

    这是 2026-10-04 实测出来的一个真缺口：`propose_candidates` 早期版本把 `project_id`
    放在响应顶层、没放进每条 draft，`fin.propose` 逐条提交时 api 回 422 ——
    工作流侧只看到「Activity task failed」。这里用真路由 + 真 `ProposalIn` 盯着。
    """
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from app.routers import fin_evolution as router_mod

    out = E.propose_candidates(project_id=env["pid"], market="CN_A")
    draft = out["drafts"][0]

    app = FastAPI()

    @app.middleware("http")
    async def _noop(request: Request, call_next):
        return await call_next(request)

    app.include_router(router_mod.router, prefix="/api")
    router_mod._INTERNAL_KEY = "l01-internal"
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/api/internal/fin/evolution/proposal", json=draft,
                       headers={"X-Hunter-Internal-Key": "l01-internal"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["proposal"]["direction"] == "tighten"


@_db
def test_budget_gate_caps_drafts(env, monkeypatch):
    """**预算真的拦得住**：本该 2 条候选（两个策略键两组），预算 1 → 只回 1 条。"""
    # 再写一条**另一个策略键**的失败经验 → 两个分组
    conn = _conn()
    try:
        memory_svc.append_evidence(
            caller="internal", project_id=env["pid"], kind="verified",
            statement="另一只票的回撤同样比预期大", method="回测", sample_size=41,
            polarity="refute", regime_tags=["bull"], symbols=["CN_A:601398"],
            strategy_keys=["ab"], as_of="2026-09-03T15:00:00+08:00",
            evidence=[{"evidence_kind": "snapshot", "ref_id": env["snap"]}])
    finally:
        conn.close()

    monkeypatch.setenv(E.PROPOSE_BUDGET_ENV, "2")
    monkeypatch.setenv("FIN_EVOLUTION_MODE", "observe")
    out2 = E.propose_candidates(project_id=env["pid"], market="CN_A")
    assert out2["action"] == "propose" and len(out2["drafts"]) == 2, out2

    monkeypatch.setenv(E.PROPOSE_BUDGET_ENV, "1")
    out1 = E.propose_candidates(project_id=env["pid"], market="CN_A")
    assert out1["action"] == "propose" and len(out1["drafts"]) == 1, out1
    assert out1["budget"]["limit"] == 1

    # 真的落一条进库 → 本窗口已用 1 → 再问就是「不提」
    draft = out1["drafts"][0]
    E.propose(project_id=env["pid"], evidence_refs=draft["evidence_refs"],
              evidence_snapshot_id=draft["evidence_snapshot_id"],
              candidate_config=draft["candidate_config"], param_diff=draft["param_diff"],
              target=draft["target"], regime_tags=draft["regime_tags"],
              rationale=draft["rationale"], created_by="l01test",
              proposal_id=draft["proposal_id"])
    monkeypatch.setenv(E.PROPOSE_BUDGET_ENV, "1")
    exhausted = E.propose_candidates(project_id=env["pid"], market="CN_A")
    assert exhausted["action"] == "skip", exhausted
    assert "预算" in exhausted["reason"] and exhausted["budget"]["used"] == 1


@_db
def test_experiment_writes_reads_and_accepts_inconclusive(env):
    """**实验表**：能写、能读回、`inconclusive` 合法落库；数字算不出就 NULL（不编）。"""
    out = E.propose_candidates(project_id=env["pid"], market="CN_A")
    draft = out["drafts"][0]
    saved = E.propose(project_id=env["pid"], evidence_refs=draft["evidence_refs"],
                      evidence_snapshot_id=draft["evidence_snapshot_id"],
                      candidate_config=draft["candidate_config"], param_diff=draft["param_diff"],
                      target=draft["target"], regime_tags=draft["regime_tags"],
                      rationale=draft["rationale"], created_by="l01test",
                      proposal_id=draft["proposal_id"])
    pid = saved["proposal_id"]

    e1 = E.record_experiment(
        proposal_id=pid, conclusion="inconclusive",
        data_snapshot_id="msnap_l01", sample_start="2026-01-02", sample_end="2026-09-11",
        cost_model="fee-model-v1", execution_model_version="exec-v1",
        method_text="样本外滚动验证（口径写入即冻结）", method_params={"windows": 5},
        result=None, result_note="可比样本不足，样本外窗口算不出稳定指标 —— 不编数字",
        owner="l01test")
    e2 = E.record_experiment(proposal_id=pid, conclusion=None, method_text="进行中", owner="l01test")
    assert e1["conclusion"] == "inconclusive" and e2["conclusion"] is None

    rows = E.list_experiments(proposal_id=pid)
    by_id = {r["experiment_id"]: r for r in rows}
    assert by_id[e1["experiment_id"]]["conclusion"] == "inconclusive"
    assert by_id[e1["experiment_id"]]["result"] is None            # 算不出 → NULL，不编
    assert by_id[e1["experiment_id"]]["method_params"] == {"windows": 5}

    with pytest.raises(E.EvolutionValidationError):
        E.record_experiment(proposal_id=pid, conclusion="maybe")


@_db
def test_experiment_is_append_only(env):
    """只追加：UPDATE / DELETE 都被触发器拒（照 `0043` 对 plan/event 的做法）。"""
    out = E.propose_candidates(project_id=env["pid"], market="CN_A")
    draft = out["drafts"][0]
    saved = E.propose(project_id=env["pid"], evidence_refs=draft["evidence_refs"],
                      evidence_snapshot_id=draft["evidence_snapshot_id"],
                      candidate_config=draft["candidate_config"], param_diff=draft["param_diff"],
                      target=draft["target"], regime_tags=draft["regime_tags"],
                      rationale=draft["rationale"], created_by="l01test",
                      proposal_id=draft["proposal_id"])
    e = E.record_experiment(proposal_id=saved["proposal_id"], conclusion="pass", owner="l01test")

    conn = _conn()
    try:
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.errors.RaiseException):
                cur.execute("UPDATE fin_evolution_experiment SET conclusion='fail' "
                            "WHERE experiment_id = %s", (e["experiment_id"],))
        conn.rollback()
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.errors.RaiseException):
                cur.execute("DELETE FROM fin_evolution_experiment WHERE experiment_id = %s",
                            (e["experiment_id"],))
        conn.rollback()
    finally:
        conn.close()
