# -*- coding: utf-8 -*-
"""R3 · 真库路由用例（`TEST_DATABASE_URL` 未设时整体 skip）：

    TEST_DATABASE_URL=postgresql://hunter:hunter@127.0.0.1:5598/r3_test \
      cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_review_router.py -q

盯住三件 R3 新增、且**必须落库才验得出来**的事：

1. **`/internal/fin/review/collect`** 真的取回当日的报告 + 事实行 + 成交（内网口令，只读）；
2. **内容哈希冻结**（§3b）：`memory.query(freeze=true)` 的快照里带 `content_hashes` /
   `aggregate_hash`；**改了某条经验的可见性之后再回放，冻结的那个 `aggregate_hash` 一字不变**，
   而 `content_drift` 把被改的那条点出来；
3. **`human_mixed` 由证据真值判定**（§4.1）：内网通道写、证据全是人工成交 → `source='human_mixed'`；
   掺一条 AI 成交 → 退回 `ai`。
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

psycopg2 = pytest.importorskip("psycopg2")
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routers import fin_memory, fin_review  # noqa: E402
from app.services.fin import memory as memory_svc  # noqa: E402
from app.services.fin import review as review_svc  # noqa: E402

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
INTERNAL_KEY = "test-internal-key"

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="需要 TEST_DATABASE_URL 指向一个已跑过 0041 迁移的 postgres",
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
            cur.execute("SELECT to_regclass('fin_experience')")
            return cur.fetchone()[0] is not None
    finally:
        conn.close()


if not _tables_ready():
    pytestmark = pytest.mark.skip(reason="库里没有 fin_experience —— 先跑 0041 迁移")
else:
    memory_svc.DATABASE_URL = TEST_DATABASE_URL
    review_svc.DATABASE_URL = TEST_DATABASE_URL
    fin_memory._INTERNAL_KEY = INTERNAL_KEY
    fin_review._INTERNAL_KEY = INTERNAL_KEY

app = FastAPI()
app.include_router(fin_memory.router, prefix="/api")
app.include_router(fin_review.router, prefix="/api")
client = TestClient(app, raise_server_exceptions=False)

IK = {"X-Hunter-Internal-Key": INTERNAL_KEY}
TRADE_DATE = "2026-10-09"


def _conn():
    return psycopg2.connect(TEST_DATABASE_URL)


@pytest.fixture()
def env():
    """一个项目 + 一份报告 + 一条事实 + 一张行情快照 + 两笔成交（一 AI、一人工）。"""
    tag = uuid.uuid4().hex[:12]
    project_id = f"prj_r3_{tag}"
    # 报告 id 是**从业务身份推导**的（`report.report_id_for`）—— 复核服务按同一个公式查，
    # 所以夹具必须用同一把公式建行，不能随便编一个 rpt_。
    report_id = review_svc._report_id(project_id, TRADE_DATE, "CN_A")
    snapshot_id = f"SNAP-r3-{tag}"
    order_ai, order_human = f"ord_ai_{tag}", f"ord_hu_{tag}"
    trade_ai, trade_human = f"trd_ai_{tag}", f"trd_hu_{tag}"

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO fin_project (project_id, user_id, tier, initial_capital) "
                "VALUES (%s, %s, 'play', 10000)", (project_id, "u-" + tag))
            cur.execute(
                "INSERT INTO fin_report (report_id, project_id, trade_date, valuation_as_of, "
                "  market, self_review) VALUES (%s, %s, %s, now(), 'CN_A', %s)",
                (report_id, project_id, TRADE_DATE,
                 '{"did_well":"守住了纪律","did_bad":"进场偏晚","change_tomorrow":"等回踩"}'))
            cur.execute(
                "INSERT INTO fin_report_fact (report_id, metric_key, value, unit, source_ref, computed_by) "
                "VALUES (%s, 'nav', 1.0, 'ratio', 'fin_valuation:2026-10-09', 'test')", (report_id,))
            cur.execute(
                "INSERT INTO fin_snapshot (snapshot_id, code, snapshot_time, source) "
                "VALUES (%s, '601398', now(), 'test')", (snapshot_id,))
            for oid, tid, src, actor in ((order_ai, trade_ai, "ai", "fin-worker"),
                                         (order_human, trade_human, "human", "user")):
                cur.execute(
                    "INSERT INTO fin_order (order_id, project_id, code, side, qty, price_type, "
                    "  status, source, actor, market) "
                    "VALUES (%s, %s, '601398', 'buy', 100, 'limit', 'filled', %s, %s, 'CN_A')",
                    (oid, project_id, src, actor))
                cur.execute(
                    "INSERT INTO fin_trade (trade_id, order_id, project_id, code, side, qty, price, "
                    "  amount, snapshot_id, source, fee_model_version, traded_at, market) "
                    "VALUES (%s, %s, %s, '601398', 'buy', 100, 10, 1000, %s, %s, 'fee-test', "
                    "  %s::timestamptz, 'CN_A')",
                    (tid, oid, project_id, snapshot_id, src,
                     f"{TRADE_DATE} 09:31:00+08"))
        conn.commit()
    finally:
        conn.close()

    yield {"project_id": project_id, "report_id": report_id, "snapshot_id": snapshot_id,
           "trade_ai": trade_ai, "trade_human": trade_human,
           "order_ai": order_ai, "order_human": order_human}

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE fin_experience SET superseded_by = NULL WHERE project_id = %s",
                        (project_id,))
            cur.execute("DELETE FROM fin_experience_evidence WHERE experience_id IN "
                        "(SELECT experience_id FROM fin_experience WHERE project_id = %s)",
                        (project_id,))
            cur.execute("DELETE FROM fin_experience WHERE project_id = %s", (project_id,))
            cur.execute("DELETE FROM fin_memory_snapshot WHERE project_id = %s", (project_id,))
            cur.execute("DELETE FROM fin_trade WHERE project_id = %s", (project_id,))
            cur.execute("DELETE FROM fin_report_fact WHERE report_id = %s", (report_id,))
            cur.execute("DELETE FROM fin_report WHERE project_id = %s", (project_id,))
            cur.execute("DELETE FROM fin_order WHERE project_id = %s", (project_id,))
            cur.execute("DELETE FROM fin_snapshot WHERE snapshot_id = %s", (snapshot_id,))
            cur.execute("DELETE FROM fin_project WHERE project_id = %s", (project_id,))
        conn.commit()
    finally:
        conn.close()


def _collect(**over):
    body = {"project_id": over.pop("project_id"), "trade_date": TRADE_DATE, "market": "CN_A"}
    body.update(over)
    return client.post("/api/internal/fin/review/collect", json=body, headers=IK)


def _evidence(env, **over):
    body = {"project_id": env["project_id"], "kind": "fact",
            "statement": "本日的成交归因清楚",
            "evidence": [{"evidence_kind": "trade", "ref_id": env["trade_ai"]}]}
    body.update(over)
    return client.post("/api/internal/fin/memory/evidence", json=body, headers=IK)


def _query(env, **over):
    body = {"project_id": env["project_id"]}
    body.update(over)
    return client.post("/api/internal/fin/memory/query", json=body, headers=IK)


# ── ① 收集端点 ────────────────────────────────────────────────────────────

def test_collect_returns_report_facts_and_trades(env):
    r = _collect(project_id=env["project_id"])
    assert r.status_code == 200
    out = r.json()
    assert out["report"]["report_id"] == env["report_id"]
    assert out["report"]["self_review"]["did_well"] == "守住了纪律"
    assert [f["metric_key"] for f in out["facts"]] == ["nav"]
    assert {t["trade_id"] for t in out["trades"]} == {env["trade_ai"], env["trade_human"]}
    # 人机归因一路带到收集结果里（复核要据此分开累计）
    by_id = {t["trade_id"]: t for t in out["trades"]}
    assert by_id[env["trade_ai"]]["source"] == "ai"
    assert by_id[env["trade_human"]]["source"] == "human"


def test_collect_requires_internal_key(env):
    r = client.post("/api/internal/fin/review/collect",
                    json={"project_id": env["project_id"], "trade_date": TRADE_DATE},
                    headers={"X-Hunter-Internal-Key": "nope"})
    assert r.status_code == 401


def test_collect_unknown_project_404(env):
    r = _collect(project_id="prj_does_not_exist")
    assert r.status_code == 404


# ── ② human_mixed 由证据真值判定 ─────────────────────────────────────────

def test_internal_write_with_human_trade_evidence_is_human_mixed(env):
    r = _evidence(env, statement="人工介入的这笔事后看是好的",
                  evidence=[{"evidence_kind": "trade", "ref_id": env["trade_human"]}])
    assert r.status_code == 200
    assert r.json()["experience"]["source"] == "human_mixed"
    assert r.json()["experience"]["created_by"] == "human:trade"


def test_internal_write_with_ai_trade_evidence_stays_ai(env):
    r = _evidence(env)
    assert r.status_code == 200 and r.json()["experience"]["source"] == "ai"


def test_internal_write_with_mixed_evidence_stays_ai(env):
    r = _evidence(env, statement="人机混着的这一天",
                  evidence=[{"evidence_kind": "trade", "ref_id": env["trade_human"]},
                            {"evidence_kind": "trade", "ref_id": env["trade_ai"]}])
    assert r.status_code == 200 and r.json()["experience"]["source"] == "ai"


# ── ③ 内容哈希冻结 + 改可见性后重放不变 ──────────────────────────────────

def test_freeze_records_content_hashes_and_replay_survives_status_change(env):
    # 一条 `verified`（会进决策口径）—— 冻结它
    w = _evidence(env, kind="verified", statement="缩量整理后突破持续性更强",
                  method="全样本回测", sample_size=42,
                  evidence=[{"evidence_kind": "trade", "ref_id": env["trade_ai"]}])
    assert w.status_code == 200, w.text
    exp_id = w.json()["experience"]["experience_id"]

    frozen = _query(env, freeze=True, for_decision=True, purpose="decision",
                    trade_date=TRADE_DATE, point="CN_A-review")
    assert frozen.status_code == 200, frozen.text
    snap_id = frozen.json()["memory_snapshot_id"]
    assert snap_id and snap_id.startswith("msnap_")

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT query_filter FROM fin_memory_snapshot WHERE memory_snapshot_id = %s",
                        (snap_id,))
            qf = cur.fetchone()[0]
    finally:
        conn.close()
    assert qf["content_hash_version"] == memory_svc.CONTENT_HASH_VERSION
    assert exp_id in qf["content_hashes"]
    assert qf["aggregate_hash"] == memory_svc.aggregate_hash(qf["content_hashes"])
    assert qf["filters"]["for_decision"] is True          # §3b 要求的嵌套形态

    # 回放一次（未改动）—— match
    r1 = client.get(f"/api/v1/fin/memory/snapshots/{snap_id}")
    assert r1.status_code == 401    # JWT 通道没有 token → 401（归属校验在服务层）

    # 改可见性：把这条经验的 status 从「已确认」改成「已推翻」（模拟后来变化）
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE fin_experience SET status = '已推翻' WHERE experience_id = %s",
                        (exp_id,))
        conn.commit()
    finally:
        conn.close()

    # 直接读服务层（回放不重跑查询，按 id 取）
    out = memory_svc.get_snapshot(memory_snapshot_id=snap_id)
    drift = out["content_drift"]
    assert drift["hashes_frozen"] is True
    # **冻结时的 aggregate_hash 一字不变**
    assert drift["frozen_aggregate_hash"] == qf["aggregate_hash"]
    # 而此刻算出来的不一样，且点出被改的那一条
    assert drift["match"] is False
    assert exp_id in drift["drifted"]
    assert drift["current_aggregate_hash"] != drift["frozen_aggregate_hash"]
    # 集合本身（id 名单）不变 —— 回放不因后来变化而多/少条目
    assert out["experience_ids"] == [exp_id]
