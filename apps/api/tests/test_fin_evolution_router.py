# -*- coding: utf-8 -*-
"""R6 · 提案层：**路由 / 真库用例**（`TEST_DATABASE_URL` 未设时整体 skip）。

    TEST_DATABASE_URL=postgresql://hunter:hunter@127.0.0.1:5598/r6_test \
      cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_evolution_router.py -q

盯住的是「服务端说了算 + 计划写后不可改 + 谁试过都留痕」：

  · 正常提案：提案 + 冻结计划 + 首条事件**同一事务**；
  · **红线 8**：`target='risk'` 放宽 → 400 + **不入库** + 有 `rejected_by_gate` 事件；
  · 白名单越界 / 超单次最大变化 / `param_diff` 篡改 → 各自 400 + 拒绝事件；
  · 证据：跨项目 / holdout / 无证据 / 不在冻结快照 → 拒；**`refute` 失败经验可引用**；
  · 同一 base 唯一待验证提案（唯一索引）；
  · 冻结三件事同事务：计划写入失败 → 提案与首条事件**都没有**；
  · 投影一致性（`status` 与最后一条事件一致的行数差 = 0）；
  · 哈希链（三条事件接得上；绕过触发器改 payload → 校验报错）；
  · JWT 列出（跨用户 → 404）。

⚠️ 清理时**必须先 `ALTER TABLE … DISABLE TRIGGER`** 才能删 plan/event
（触发器连属主都拒 —— 这正是它挡得住应用代码的证明）。测试以库属主身份跑，做得到。
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

# 进化模式：default 是 off（fail-safe），测试显式开成 observe。
os.environ.setdefault("FIN_EVOLUTION_MODE", "observe")

psycopg2 = pytest.importorskip("psycopg2")
import psycopg2.extras  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routers import fin_evolution  # noqa: E402
from app.services.fin import evolution as E  # noqa: E402
from app.services.fin import memory as memory_svc  # noqa: E402

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
INTERNAL_KEY = "test-internal-key"

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="需要 TEST_DATABASE_URL 指向一个已跑过 0043 迁移的 postgres",
)


def _tables_ready() -> bool:
    if not TEST_DATABASE_URL:
        return False
    try:
        conn = psycopg2.connect(TEST_DATABASE_URL)
    except Exception:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('fin_evolution_proposal')")
            return cur.fetchone()[0] is not None
    finally:
        conn.close()


if not _tables_ready():
    pytestmark = pytest.mark.skip(reason="库里没有 fin_evolution_proposal —— 先跑 0043 迁移")
else:
    E.DATABASE_URL = TEST_DATABASE_URL
    memory_svc.DATABASE_URL = TEST_DATABASE_URL
    fin_evolution._INTERNAL_KEY = INTERNAL_KEY


# ── 测试 app：header 决定身份 / 内网口令写死 ────────────────────────────
app = FastAPI()


@app.middleware("http")
async def fake_auth(request: Request, call_next):
    request.state.user_id = request.headers.get("x-test-user") or None
    return await call_next(request)


app.include_router(fin_evolution.router, prefix="/api")
client = TestClient(app, raise_server_exceptions=False)

IK = {"X-Hunter-Internal-Key": INTERNAL_KEY}


def _conn():
    return psycopg2.connect(TEST_DATABASE_URL)


def _purge(conn, project_id: str) -> None:
    """清掉一个测试项目的全部痕迹。**必须先关触发器**才能删 plan/event。"""
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE fin_evolution_plan DISABLE TRIGGER fin_evolution_plan_immutable")
        cur.execute("ALTER TABLE fin_evolution_event DISABLE TRIGGER fin_evolution_event_immutable")
        cur.execute(
            "DELETE FROM fin_evolution_plan WHERE proposal_id IN "
            "(SELECT proposal_id FROM fin_evolution_proposal WHERE project_id = %s)", (project_id,))
        cur.execute(
            "DELETE FROM fin_evolution_event WHERE proposal_id IN "
            "(SELECT proposal_id FROM fin_evolution_proposal WHERE project_id = %s)", (project_id,))
        # 闸门拒绝事件：提案没入库，靠 payload.project_id 找到
        cur.execute("DELETE FROM fin_evolution_event WHERE payload->>'project_id' = %s", (project_id,))
        cur.execute("DELETE FROM fin_evolution_proposal WHERE project_id = %s", (project_id,))
        cur.execute("ALTER TABLE fin_evolution_plan ENABLE TRIGGER fin_evolution_plan_immutable")
        cur.execute("ALTER TABLE fin_evolution_event ENABLE TRIGGER fin_evolution_event_immutable")
        cur.execute(
            "DELETE FROM fin_experience_evidence WHERE experience_id IN "
            "(SELECT experience_id FROM fin_experience WHERE project_id = %s)", (project_id,))
        cur.execute("DELETE FROM fin_memory_snapshot WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM fin_experience WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM fin_param WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM fin_snapshot WHERE source = 'r6test' AND code = %s", (project_id,))
        cur.execute("DELETE FROM fin_project WHERE project_id = %s", (project_id,))
    conn.commit()


@pytest.fixture()
def env():
    """一个项目 + 参数 + 两个经验（support / refute）+ 一份复盘冻结晶 + 一份 holdout 冻结晶。"""
    uid = "u-" + uuid.uuid4().hex
    tag = uuid.uuid4().hex[:12]
    pid = f"prj_r6_{tag}"
    snap = f"SNAP-r6-{tag}"

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO fin_project (project_id, user_id, tier, initial_capital) "
                        "VALUES (%s, %s, 'play', 100000)", (pid, uid))
            cur.execute(
                "INSERT INTO fin_param (project_id, stop_loss_pct, take_profit_pct, hold_days_max, "
                "  max_positions, max_position_pct, min_order_amount, daily_max_new, daily_max_orders, "
                "  daily_loss_halt_pct, account_drawdown_halt_pct, strategies) "
                "VALUES (%s, 0.08, 0.20, 5, 5, 0.20, 1000, 5, 10, -0.03, -0.08, %s)",
                (pid, psycopg2.extras.Json([
                    {"key": "vb", "name": "量价突破", "version": "v1",
                     "params": {"vol_mult": 2.0, "confirm_days": 1}},
                ])))
            cur.execute("INSERT INTO fin_snapshot (snapshot_id, code, snapshot_time, source) "
                        "VALUES (%s, %s, now(), 'r6test')", (snap, pid))
        conn.commit()

        # 走唯一入口写经验（不直接 INSERT 经验表）：support + refute，都带 regime=bull
        exp_support = memory_svc.append_evidence(
            caller="internal", project_id=pid, kind="verified",
            statement="甲股缩量整理后追高的回撤概率上升",
            method="回测", sample_size=30, polarity="refute", regime_tags=["bull"],
            symbols=["CN_A:601398"], as_of="2026-09-01T15:00:00+08:00",
            evidence=[{"evidence_kind": "snapshot", "ref_id": snap}])
        exp_refute = memory_svc.append_evidence(
            caller="internal", project_id=pid, kind="verified",
            statement="乙股放量突破后三日内的回撤更小",
            method="回测", sample_size=42, polarity="support", regime_tags=["bull"],
            symbols=["CN_A:600519"], as_of="2026-09-02T15:00:00+08:00",
            evidence=[{"evidence_kind": "snapshot", "ref_id": snap}])
        exp_holdout = memory_svc.append_evidence(
            caller="internal", project_id=pid, kind="verified",
            statement="丙股在保底测试集里的表现",
            method="回测", sample_size=9, polarity="support", regime_tags=["bull"],
            as_of="2026-09-03T15:00:00+08:00",
            evidence=[{"evidence_kind": "snapshot", "ref_id": snap, "holdout_tainted": True}])

        review = memory_svc.query(project_id=pid, caller="internal", freeze=True, purpose="review")
        holdout = memory_svc.query(project_id=pid, caller="internal", freeze=True, purpose="holdout")
    finally:
        conn.close()

    ctx = {
        "uid": uid, "pid": pid, "snap": snap,
        "exp_support": exp_support["experience_id"],
        "exp_refute": exp_refute["experience_id"],
        "exp_holdout": exp_holdout["experience_id"],
        "review_snap": review["memory_snapshot_id"],
        "holdout_snap": holdout["memory_snapshot_id"],
    }
    yield ctx
    conn = _conn()
    try:
        _purge(conn, pid)
    finally:
        conn.close()


# ── 工具：基线配置 / 候选 / 请求体 ──────────────────────────────────────

def _base_and_cur(pid):
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            return E.read_config(cur, pid)
    finally:
        conn.close()


def _proposal_body(env, **over):
    base = _base_and_cur(env["pid"])
    body = {
        "project_id": env["pid"],
        "evidence_refs": [env["exp_support"], env["exp_refute"]],
        "evidence_snapshot_id": env["review_snap"],
        "candidate_config": {**base, "stop_loss_pct": 0.05},
        "param_diff": E.config_diff(base, {**base, "stop_loss_pct": 0.05}),
        "target": "strategy",
        "regime_tags": ["bull"],
        "rationale": "止损收紧，减少单笔回撤（依据两条经验）",
        "created_by": "test",
    }
    body.update(over)
    return body


def _post(body, headers=None):
    return client.post("/api/internal/fin/evolution/proposal",
                       json=body, headers={**IK, **(headers or {})})


def _count_proposals(pid):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fin_evolution_proposal WHERE project_id=%s", (pid,))
            return cur.fetchone()[0]
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
                cur.execute("SELECT e.* FROM fin_evolution_event e WHERE e.proposal_id IN "
                            "(SELECT proposal_id FROM fin_evolution_proposal WHERE project_id=%s) "
                            "OR e.payload->>'project_id'=%s ORDER BY e.created_at, e.event_id",
                            (pid, pid))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════════
# 正常路径
# ════════════════════════════════════════════════════════════════════════

def test_happy_path_writes_proposal_plan_and_created_event(env):
    r = _post(_proposal_body(env))
    assert r.status_code == 200, r.text
    out = r.json()["proposal"]
    assert out["target"] == "strategy" and out["direction"] == "tighten"
    assert out["param_diff"] == [{"field": "stop_loss_pct", "old": 0.08, "new": 0.05}]

    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM fin_evolution_proposal WHERE proposal_id=%s",
                        (out["proposal_id"],))
            p = dict(cur.fetchone())
            cur.execute("SELECT * FROM fin_evolution_plan WHERE proposal_id=%s",
                        (out["proposal_id"],))
            plan = dict(cur.fetchone())
    finally:
        conn.close()

    assert p["status"] == "draft"
    assert p["evidence_snapshot_id"] == env["review_snap"]
    assert p["proposal_algo_version"] == E.ALGO_VERSION
    assert plan["plan_hash"] == out["plan_hash"]
    assert plan["metric"] == E.DEFAULT_PLAN["metric"]
    assert plan["window_days"] == E.DEFAULT_PLAN["window_days"]

    kinds = [e["kind"] for e in _events(env["pid"])]
    assert "created" in kinds


def test_refute_experience_is_a_valid_evidence(env):
    """失败经验（polarity='refute'）能单独作为证据 —— 自进化的燃料一半是"什么不行"。"""
    body = _proposal_body(env, evidence_refs=[env["exp_refute"]])
    r = _post(body)
    assert r.status_code == 200, r.text


# ════════════════════════════════════════════════════════════════════════
# 红线 8：AI 无权放宽风控
# ════════════════════════════════════════════════════════════════════════

def test_risk_loosen_is_rejected_not_stored_but_event_exists(env):
    base = _base_and_cur(env["pid"])
    cand = {**base, "max_position_pct": 0.30}            # 0.20 → 0.30 = 放宽
    body = _proposal_body(env, candidate_config=cand,
                          param_diff=E.config_diff(base, cand),
                          target="risk", rationale="想加大仓位")
    before = _count_proposals(env["pid"])
    r = _post(body)
    assert r.status_code == 400, r.text
    assert "放宽" in r.text
    assert _count_proposals(env["pid"]) == before        # 不入库
    rejections = _events(env["pid"], kind="rejected_by_gate")
    assert rejections, "必须有一条 rejected_by_gate 事件（谁试过要留痕）"


def test_risk_tighten_is_allowed(env):
    base = _base_and_cur(env["pid"])
    cand = {**base, "max_position_pct": 0.15}            # 收紧
    body = _proposal_body(env, candidate_config=cand,
                          param_diff=E.config_diff(base, cand), target="risk",
                          rationale="收紧单票上限")
    r = _post(body)
    assert r.status_code == 200, r.text
    assert r.json()["proposal"]["direction"] == "tighten"


# ════════════════════════════════════════════════════════════════════════
# 白名单越界
# ════════════════════════════════════════════════════════════════════════

def test_param_outside_whitelist_rejected(env):
    base = _base_and_cur(env["pid"])
    body = _proposal_body(env, candidate_config={**base, "fast": 5},
                          param_diff=[{"field": "fast", "old": 10, "new": 5}])
    r = _post(body)
    assert r.status_code == 400, r.text
    assert "白名单" in r.text


def test_param_step_over_limit_rejected(env):
    base = _base_and_cur(env["pid"])
    cand = {**base, "stop_loss_pct": 0.20}               # 0.08 → 0.20，远超 max_step 0.05
    body = _proposal_body(env, candidate_config=cand,
                          param_diff=E.config_diff(base, cand))
    r = _post(body)
    assert r.status_code == 400, r.text
    assert "单次变化" in r.text


# ════════════════════════════════════════════════════════════════════════
# 证据
# ════════════════════════════════════════════════════════════════════════

def test_no_evidence_rejected(env):
    r = _post(_proposal_body(env, evidence_refs=[]))
    assert r.status_code == 400, r.text
    assert "至少一条" in r.text


def test_evidence_not_in_frozen_snapshot_rejected(env):
    """引用的经验不在那份冻结集合里（这里用 holdout 经验）→ 拒。"""
    r = _post(_proposal_body(env, evidence_refs=[env["exp_holdout"]]))
    assert r.status_code == 400, r.text
    assert "冻结快照" in r.text


def test_evidence_cross_project_rejected(env):
    """拿别的项目的快照当证据 → 拒。"""
    other = f"prj_r6_other_{uuid.uuid4().hex[:8]}"
    other_snap = f"msnap_other_{uuid.uuid4().hex[:8]}"
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO fin_project (project_id, user_id, tier, initial_capital) "
                        "VALUES (%s, %s, 'play', 1000)", (other, "u-other"))
            cur.execute("INSERT INTO fin_memory_snapshot "
                        "(memory_snapshot_id, project_id, purpose, experience_ids) "
                        "VALUES (%s, %s, 'review', '{}')", (other_snap, other))
        conn.commit()
        r = _post(_proposal_body(env, evidence_snapshot_id=other_snap))
        assert r.status_code == 400, r.text
        assert "不属于本项目" in r.text
    finally:
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM fin_memory_snapshot WHERE project_id=%s", (other,))
            cur.execute("DELETE FROM fin_project WHERE project_id=%s", (other,))
        conn.commit()
        conn.close()


def test_holdout_snapshot_rejected(env):
    r = _post(_proposal_body(env, evidence_snapshot_id=env["holdout_snap"]))
    assert r.status_code == 400, r.text
    assert "holdout" in r.text


def test_missing_snapshot_rejected(env):
    r = _post(_proposal_body(env, evidence_snapshot_id=None))
    assert r.status_code == 400, r.text
    assert "evidence_snapshot_id" in r.text


# ════════════════════════════════════════════════════════════════════════
# param_diff 篡改
# ════════════════════════════════════════════════════════════════════════

def test_tampered_param_diff_rejected(env):
    base = _base_and_cur(env["pid"])
    cand = {**base, "stop_loss_pct": 0.05}
    # 写入方谎报 old：真实基线是 0.08，它说 0.02
    body = _proposal_body(env, candidate_config=cand,
                          param_diff=[{"field": "stop_loss_pct", "old": 0.02, "new": 0.05}])
    r = _post(body)
    assert r.status_code == 400, r.text
    assert "不一致" in r.text or "篡改" in r.text


# ════════════════════════════════════════════════════════════════════════
# 唯一待验证提案
# ════════════════════════════════════════════════════════════════════════

def test_second_pending_proposal_on_same_base_conflicts(env):
    r1 = _post(_proposal_body(env))                      # 改 stop_loss_pct
    assert r1.status_code == 200, r1.text
    base = _base_and_cur(env["pid"])
    cand = {**base, "take_profit_pct": 0.15}             # 同一 base，改另一个字段
    r2 = _post(_proposal_body(env, candidate_config=cand,
                              param_diff=E.config_diff(base, cand)))
    assert r2.status_code == 409, r2.text
    assert "待验证" in r2.text


# ════════════════════════════════════════════════════════════════════════
# 冻结三件事同事务
# ════════════════════════════════════════════════════════════════════════

def test_plan_failure_rolls_back_proposal_and_event(env):
    before_p = _count_proposals(env["pid"])
    before_e = len(_events(env["pid"]))
    body = _proposal_body(env, plan_overrides={"metric": None})   # NOT NULL 违约
    r = _post(body)
    assert r.status_code >= 500, r.text                  # 服务端错误（计划写入失败）
    assert _count_proposals(env["pid"]) == before_p      # 提案没有
    assert len(_events(env["pid"])) == before_e           # 首条事件也没有


# ════════════════════════════════════════════════════════════════════════
# 投影一致性 + 哈希链
# ════════════════════════════════════════════════════════════════════════

def test_projection_consistent_and_hash_chain(env):
    r = _post(_proposal_body(env))
    assert r.status_code == 200, r.text
    pid = r.json()["proposal"]["proposal_id"]

    # 追加两条事件（validating → passed）
    e2 = E.append_event(proposal_id=pid, kind="validating", payload={"note": "开始验证"})
    e3 = E.append_event(proposal_id=pid, kind="passed", payload={"note": "通过"})
    chain = E.verify_chain(pid)
    assert chain["ok"] and chain["count"] == 3, chain
    # 第一条事件（created）的 prev_hash 必须接上第二条（validating）的 event_hash
    assert e2["prev_hash"] != "" and e2["event_hash"] != ""
    # 第三条的 prev_hash == 第二条的 event_hash
    assert e3["prev_hash"] == e2["event_hash"]
    # 提案 status 是最后一条事件的投影
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM fin_evolution_proposal WHERE proposal_id=%s", (pid,))
            assert cur.fetchone()[0] == "passed"
    finally:
        conn.close()
    # 全库投影一致性：不一致 = 0
    assert E.projection_mismatches() == []


def test_tampering_event_payload_breaks_chain(env):
    """绕过触发器改一条事件的 payload → 哈希链校验报错（触发器之外的第二层审计）。"""
    r = _post(_proposal_body(env))
    pid = r.json()["proposal"]["proposal_id"]
    E.append_event(proposal_id=pid, kind="validating", payload={"note": "原始"})

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE fin_evolution_event "
                        "DISABLE TRIGGER fin_evolution_event_immutable")
            cur.execute("UPDATE fin_evolution_event SET payload=%s "
                        "WHERE proposal_id=%s AND kind='validating'",
                        (psycopg2.extras.Json({"note": "篡改了"}), pid))
            cur.execute("ALTER TABLE fin_evolution_event "
                        "ENABLE TRIGGER fin_evolution_event_immutable")
        conn.commit()
    finally:
        conn.close()

    chain = E.verify_chain(pid)
    assert chain["ok"] is False
    assert any("内容被改过" in b["why"] for b in chain["broken"])


# ════════════════════════════════════════════════════════════════════════
# JWT 列出
# ════════════════════════════════════════════════════════════════════════

def test_jwt_list_and_cross_user_404(env):
    r = _post(_proposal_body(env))
    assert r.status_code == 200
    r2 = client.get("/api/v1/fin/evolution/proposals",
                    params={"project_id": env["pid"]}, headers={"x-test-user": env["uid"]})
    assert r2.status_code == 200, r2.text
    items = r2.json()["items"]
    assert len(items) == 1 and items[0]["plan"] is not None
    # 未登录 → 401
    assert client.get("/api/v1/fin/evolution/proposals",
                      params={"project_id": env["pid"]}).status_code == 401
    # 别人的项目 → 404
    r3 = client.get("/api/v1/fin/evolution/proposals",
                    params={"project_id": env["pid"]}, headers={"x-test-user": "u-someone-else"})
    assert r3.status_code == 404, r3.text


def test_internal_channel_requires_key(env):
    r = client.post("/api/internal/fin/evolution/proposal",
                    json=_proposal_body(env), headers={"X-Hunter-Internal-Key": "wrong"})
    assert r.status_code == 401
