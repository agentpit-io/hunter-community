# -*- coding: utf-8 -*-
"""R2 · 统一经验系统：**路由层真库用例**（`TEST_DATABASE_URL` 未设时整体 skip）。

    TEST_DATABASE_URL=postgresql://hunter:hunter@127.0.0.1:5598/r2_test \
      cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_memory_router.py -q

盯住的是「服务端说了算」这件事 —— 八条硬校验 + 四条硬过滤，**一条都不能靠调用方自觉**：

  · 八条硬校验：evidence 至少一行 / hypothesis 强制待验证 / ref 必须真存在 / holdout 传染 /
    verified 必填 method+sample_size / statement 不许有数字 / source 由通道定 / 没有改只有加；
  · **零开关**：`holdout_only` 查不到，穷举 `include_holdout`/`debug`/`admin`/未知键仍查不到；
  · **时间边界**：`as_of` 晚的经验，用早的基准查不到；
  · **for_decision 严口径**：假设不进决策路径；
  · **冻结重放**：`freeze=true` 拿到 `msnap_...`，之后再写经验，回放仍是原来那批 id。

做法同 `test_fin_project_router.py`：真 import 路由，挂到最小 FastAPI，用测试中间件按 header
设 `request.state.user_id`（替代 JWT 中间件）；每个用例一套随机 uid + 独立项目，跑完清干净。
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

from app.routers import fin_memory  # noqa: E402
from app.services.fin import memory as memory_svc  # noqa: E402

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
    fin_memory._INTERNAL_KEY = INTERNAL_KEY


# ── 测试 app：header 决定身份 / 内网口令写死 ────────────────────────────
app = FastAPI()


@app.middleware("http")
async def fake_auth(request: Request, call_next):
    request.state.user_id = request.headers.get("x-test-user") or None
    return await call_next(request)


app.include_router(fin_memory.router, prefix="/api")
client = TestClient(app, raise_server_exceptions=False)

IK = {"X-Hunter-Internal-Key": INTERNAL_KEY}


def _conn():
    return psycopg2.connect(TEST_DATABASE_URL)


@pytest.fixture()
def env():
    """一套独立的测试数据：一个项目 + 一条报告 / 一条事实 / 一张快照 / 一笔成交。

    `uid` 决定所有 id 前缀，跑完按 id 精删（顺序：子表 → 父表）。
    """
    uid = "u-" + uuid.uuid4().hex
    tag = uuid.uuid4().hex[:12]
    project_id = f"prj_test_{tag}"
    report_id = f"rpt_test_{tag}"
    snapshot_id = f"SNAP-test-{tag}"
    order_id = f"ord_test_{tag}"
    trade_id = f"trd_test_{tag}"

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO fin_project (project_id, user_id, tier, initial_capital) "
                "VALUES (%s, %s, 'play', 10000)", (project_id, uid))
            cur.execute(
                "INSERT INTO fin_report (report_id, project_id, trade_date, valuation_as_of) "
                "VALUES (%s, %s, '2026-09-30', now())", (report_id, project_id))
            cur.execute(
                "INSERT INTO fin_report_fact (report_id, metric_key, value, unit, source_ref, computed_by) "
                "VALUES (%s, 'nav', 1.0, 'ratio', 'fin_valuation:2026-09-30', 'test')", (report_id,))
            cur.execute(
                "INSERT INTO fin_snapshot (snapshot_id, code, snapshot_time, source) "
                "VALUES (%s, '600519', now(), 'test')", (snapshot_id,))
            cur.execute(
                "INSERT INTO fin_order (order_id, project_id, code, side, qty, price_type, status) "
                "VALUES (%s, %s, '600519', 'buy', 100, 'limit', 'filled')", (order_id, project_id))
            cur.execute(
                "INSERT INTO fin_trade (trade_id, order_id, project_id, code, side, qty, price, "
                "  amount, snapshot_id, source, fee_model_version, traded_at) "
                "VALUES (%s, %s, %s, '600519', 'buy', 100, 10, 1000, %s, 'ai', 'fee-test', now())",
                (trade_id, order_id, project_id, snapshot_id))
        conn.commit()
    finally:
        conn.close()

    yield {
        "uid": uid, "project_id": project_id, "report_id": report_id,
        "snapshot_id": snapshot_id, "order_id": order_id, "trade_id": trade_id,
        "fact_ref": f"{report_id}:nav",
    }

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
            cur.execute("DELETE FROM fin_report_fact WHERE report_id = %s", (report_id,))
            cur.execute("DELETE FROM fin_trade WHERE project_id = %s", (project_id,))
            cur.execute("DELETE FROM fin_report WHERE project_id = %s", (project_id,))
            cur.execute("DELETE FROM fin_order WHERE project_id = %s", (project_id,))
            cur.execute("DELETE FROM fin_snapshot WHERE snapshot_id = %s", (snapshot_id,))
            cur.execute("DELETE FROM fin_project WHERE project_id = %s", (project_id,))
        conn.commit()
    finally:
        conn.close()


# ── 便利包装 ──────────────────────────────────────────────────────────────

def _post_internal_evidence(env, **over):
    body = {
        "project_id": env["project_id"], "kind": "fact",
        "statement": "港股缩量后追高胜率显著下降",
        "evidence": [{"evidence_kind": "report", "ref_id": env["report_id"]}],
    }
    body.update(over)
    return client.post("/api/internal/fin/memory/evidence", json=body, headers=IK)


def _post_internal_query(env, **over):
    body = {"project_id": env["project_id"]}
    body.update(over)
    return client.post("/api/internal/fin/memory/query", json=body, headers=IK)


def _ids(resp):
    return [it["experience_id"] for it in resp.json()["items"]]


# ════════════════════════════════════════════════════════════════════════
# 鉴权
# ════════════════════════════════════════════════════════════════════════

def test_internal_wrong_key_401(env):
    r = client.post("/api/internal/fin/memory/query", json={"project_id": env["project_id"]},
                    headers={"X-Hunter-Internal-Key": "nope"})
    assert r.status_code == 401


def test_jwt_missing_login_401(env):
    r = client.get("/api/v1/fin/memory/experiences", params={"project_id": env["project_id"]})
    assert r.status_code == 401
    assert r.json()["detail"]["need_login"] is True


# ════════════════════════════════════════════════════════════════════════
# 八条硬校验
# ════════════════════════════════════════════════════════════════════════

def test_rule2_hypothesis_forced_pending(env):
    r = _post_internal_evidence(env, kind="hypothesis", status="已确认",
                                statement="缩量后追高胜率下降")
    assert r.status_code == 200, r.text
    assert r.json()["experience"]["status"] == "待验证"


def test_rule5_verified_without_method_400(env):
    r = _post_internal_evidence(env, kind="verified", sample_size=10)
    assert r.status_code == 400
    assert "method" in r.json()["detail"]


def test_rule5_verified_without_sample_size_400(env):
    r = _post_internal_evidence(env, kind="verified", method="全样本回测")
    assert r.status_code == 400
    assert "sample_size" in r.json()["detail"]


def test_rule6_fact_statement_with_number_400(env):
    r = _post_internal_evidence(env, kind="fact", statement="缩量到 3 笔以下追高胜率下降")
    assert r.status_code == 400
    assert "阿拉伯数字" in r.json()["detail"]


def test_rule1_empty_evidence_400(env):
    r = _post_internal_evidence(env, evidence=[])
    assert r.status_code == 400
    assert "至少一行" in r.json()["detail"]


def test_rule3_nonexistent_ref_400(env):
    r = _post_internal_evidence(
        env, evidence=[{"evidence_kind": "report", "ref_id": "rpt_does_not_exist"}])
    assert r.status_code == 400
    assert "证据引用不存在" in r.json()["detail"]


@pytest.mark.parametrize("kind,make_ref", [
    ("report", lambda e: {"evidence_kind": "report", "ref_id": e["report_id"]}),
    ("trade", lambda e: {"evidence_kind": "trade", "ref_id": e["trade_id"]}),
    ("snapshot", lambda e: {"evidence_kind": "snapshot", "ref_id": e["snapshot_id"]}),
    ("fact", lambda e: {"evidence_kind": "fact", "ref_id": e["fact_ref"]}),
])
def test_rule3_real_refs_all_accepted(env, kind, make_ref):
    r = _post_internal_evidence(env, evidence=[make_ref(env)])
    assert r.status_code == 200, r.text
    assert r.json()["experience"]["kind"] == "fact"


def test_external_evidence_rejected_400(env):
    r = _post_internal_evidence(
        env, evidence=[{"evidence_kind": "external", "ref_id": "arxiv:1234"}])
    assert r.status_code == 400
    assert "external" in r.json()["detail"]


def test_rule4_holdout_tainted_contagion(env):
    """证据带 holdout_tainted=true ⇒ 本条经验强制 holdout_only + holdout_tainted。"""
    r = _post_internal_evidence(
        env, statement="保底测试集上追高胜率下降",
        evidence=[{"evidence_kind": "report", "ref_id": env["report_id"],
                   "holdout_tainted": True}])
    assert r.status_code == 200, r.text
    exp = r.json()["experience"]
    assert exp["exposure_scope"] == "holdout_only"
    assert exp["holdout_tainted"] is True
    # 入库核对（不是只看响应）
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT exposure_scope, holdout_tainted FROM fin_experience "
                        "WHERE experience_id = %s", (exp["experience_id"],))
            assert cur.fetchone() == ("holdout_only", True)
    finally:
        conn.close()


def test_rule7_source_forced_by_channel_and_ignored_in_body(env):
    """内网通道 → ai；JWT 通道 → human_mixed + created_by='user:<uuid>'；强传 source 被忽略。"""
    # 内网：body 里强行塞 source='human_mixed' —— 不认
    r1 = _post_internal_evidence(env, statement="内网写结论一", source="human_mixed")
    assert r1.status_code == 200, r1.text
    e1 = r1.json()["experience"]
    assert e1["source"] == "ai" and e1["created_by"] == "ai"

    # JWT：body 里强行塞 source='ai' —— 不认
    body = {"project_id": env["project_id"], "kind": "fact",
            "statement": "真人写结论一", "source": "ai",
            "evidence": [{"evidence_kind": "report", "ref_id": env["report_id"]}]}
    r2 = client.post("/api/v1/fin/memory/evidence", json=body, headers={"x-test-user": env["uid"]})
    assert r2.status_code == 200, r2.text
    e2 = r2.json()["experience"]
    assert e2["source"] == "human_mixed"
    assert e2["created_by"] == f"user:{env['uid']}"


def test_rule8_supersede_appends_and_points_back(env):
    """推翻 = 追加一条「已推翻」+ 原条目 superseded_by 指向它；**原条目不删**。"""
    r1 = _post_internal_evidence(env, statement="旧结论：缩量后追高胜率下降")
    old_id = r1.json()["experience"]["experience_id"]

    r2 = _post_internal_evidence(env, statement="新结论：缩量后追高胜率并未下降",
                                 supersedes=old_id)
    assert r2.status_code == 200, r2.text
    new = r2.json()["experience"]
    assert new["status"] == "已推翻"

    # 原条目还在，且指了过去
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT superseded_by FROM fin_experience WHERE experience_id = %s",
                        (old_id,))
            assert cur.fetchone()[0] == new["experience_id"]
            cur.execute("SELECT count(*) FROM fin_experience WHERE project_id = %s",
                        (env["project_id"],))
            assert cur.fetchone()[0] == 2          # 两条都在，没删
    finally:
        conn.close()


def test_rule8_unknown_supersede_target_400(env):
    r = _post_internal_evidence(env, supersedes="exp_does_not_exist")
    assert r.status_code == 400
    assert "不存在" in r.json()["detail"]


# ════════════════════════════════════════════════════════════════════════
# query 的四条硬过滤
# ════════════════════════════════════════════════════════════════════════

def test_zero_switch_holdout_invisible_even_with_every_param(env):
    """零开关：holdout_only 的经验，query 查不到；穷举放开参数也查不到。"""
    r = _post_internal_evidence(
        env, statement="保底测试集污染结论",
        evidence=[{"evidence_kind": "report", "ref_id": env["report_id"],
                   "holdout_tainted": True}])
    hidden = r.json()["experience"]["experience_id"]
    # 再写一条正常的，证明「查得到」这条路径是通的
    ok_id = _post_internal_evidence(env, statement="正常搜索路径结论").json()["experience"]["experience_id"]

    base = _post_internal_query(env)
    assert base.status_code == 200, base.text
    assert hidden not in _ids(base)
    assert ok_id in _ids(base)

    # 穷举「想放开 holdout」的入参组合 —— 未知键被忽略，服务层也没有它们的落点
    for extra in (
        {"include_holdout": True}, {"debug": True}, {"admin": True},
        {"include_holdout": True, "debug": 1, "admin": "yes"},
        {"exposure_scope": "holdout_only"}, {"holdout_only": True},
        {"scope": "holdout_only"}, {"forces": ["holdout"]}, {"whatever": 123},
    ):
        r = _post_internal_query(env, **extra)
        assert r.status_code == 200, (extra, r.text)
        assert hidden not in _ids(r), extra

    # JWT 通道同样问不到的（GET 未知 query 参数被忽略）
    for extra in ({"include_holdout": "true"}, {"debug": "1"}, {"admin": "true"}):
        r = client.get("/api/v1/fin/memory/experiences",
                       params={"project_id": env["project_id"], **extra},
                       headers={"x-test-user": env["uid"]})
        assert r.status_code == 200, (extra, r.text)
        assert hidden not in _ids(r), extra


def test_time_boundary_only_earlier_experience(env):
    """两条经验 as_of 一前一后 → 用早的基准查 → 只出前一条。"""
    a = _post_internal_evidence(env, statement="一季度结论", as_of="2026-01-01T08:00:00+08:00")
    b = _post_internal_evidence(env, statement="三季度结论", as_of="2026-07-01T08:00:00+08:00")
    aid = a.json()["experience"]["experience_id"]
    bid = b.json()["experience"]["experience_id"]

    early = _post_internal_query(env, as_of="2026-03-01T00:00:00+08:00")
    assert _ids(early) == [aid]                  # 只出前一条
    assert early.json()["as_of_basis"] == "2026-03-01T00:00:00+08:00"

    late = _post_internal_query(env, as_of="2026-09-01T00:00:00+08:00")
    assert set(_ids(late)) == {aid, bid}


def test_as_of_basis_defaults_to_query_issuance_time(env):
    _post_internal_evidence(env, statement="无基准也要能查")
    r = _post_internal_query(env)
    assert r.status_code == 200
    assert r.json()["as_of_basis"]                    # 回显「查询发起时刻」
    assert r.json()["items"]                          # 不带 as_of 时默认基准是现在，能查到


def test_for_decision_excludes_hypothesis(env):
    """决策口径：假设从决策路径上直接不返回（服务端不给，不靠调用方过滤）。"""
    fact = _post_internal_evidence(env, kind="fact", statement="已确认事实结论").json()["experience"]
    hyp = _post_internal_evidence(env, kind="hypothesis",
                                  statement="待验证的假设结论").json()["experience"]

    normal = _post_internal_query(env)
    assert {fact["experience_id"], hyp["experience_id"]} <= set(_ids(normal))

    decision = _post_internal_query(env, for_decision=True)
    ids = _ids(decision)
    assert fact["experience_id"] in ids
    assert hyp["experience_id"] not in ids


def test_cross_user_query_and_append_404(env):
    """跨用户一律 404，不区分「不存在」与「无权限」。"""
    r = client.get("/api/v1/fin/memory/experiences",
                   params={"project_id": env["project_id"]},
                   headers={"x-test-user": "u-someone-else-" + uuid.uuid4().hex})
    assert r.status_code == 404

    r = client.post("/api/v1/fin/memory/evidence",
                    json={"project_id": env["project_id"], "kind": "fact",
                          "statement": "越权写入", "evidence": [
                              {"evidence_kind": "report", "ref_id": env["report_id"]}]},
                    headers={"x-test-user": "u-someone-else-" + uuid.uuid4().hex})
    assert r.status_code == 404


def test_jwt_read_same_filters_as_internal(env):
    """JWT 读与内网读是**同一套服务端过滤**。"""
    _post_internal_evidence(env, statement="JWT 读得到的结论")
    internal = _post_internal_query(env)
    jwt = client.get("/api/v1/fin/memory/experiences",
                     params={"project_id": env["project_id"]},
                     headers={"x-test-user": env["uid"]})
    assert jwt.status_code == 200, jwt.text
    assert _ids(jwt) == _ids(internal)


# ════════════════════════════════════════════════════════════════════════
# 冻结 + 回放
# ════════════════════════════════════════════════════════════════════════

def test_freeze_then_replay_ignores_later_writes(env):
    """freeze=true → msnap_…；之后写新经验，回放仍是原来那批 id（不因新增变多）。"""
    first = _post_internal_evidence(env, statement="冻结时的结论一").json()["experience"]
    _post_internal_evidence(env, statement="冻结时的结论二")

    frozen = _post_internal_query(env, freeze=True, purpose="decision",
                                  trade_date="2026-10-03", point="1430",
                                  as_of="2027-01-01T00:00:00+08:00")
    assert frozen.status_code == 200, frozen.text
    snap_id = frozen.json()["memory_snapshot_id"]
    assert snap_id.startswith("msnap_")
    frozen_ids = _ids(frozen)
    assert len(frozen_ids) == 2

    # 冻结之后新增
    _post_internal_evidence(env, statement="冻结之后的结论三")

    # 再查（不冻结）→ 三条都在
    assert len(_ids(_post_internal_query(env, as_of="2027-01-01T00:00:00+08:00"))) == 3

    # 回放：仍是那 2 条，**一次都不多**
    r1 = client.get(f"/api/v1/fin/memory/snapshots/{snap_id}",
                    headers={"x-test-user": env["uid"]})
    r2 = client.get(f"/api/v1/fin/memory/snapshots/{snap_id}",
                    headers={"x-test-user": env["uid"]})
    assert r1.status_code == 200, r1.text
    assert r1.json()["experience_ids"] == r2.json()["experience_ids"] == frozen_ids
    assert len(r1.json()["experience_ids"]) == 2
    assert first["experience_id"] in r1.json()["experience_ids"]


def test_no_freeze_returns_null_snapshot(env):
    _post_internal_evidence(env, statement="不冻结时不该有快照")
    r = _post_internal_query(env)
    assert r.json()["memory_snapshot_id"] is None


def test_snapshot_cross_user_404(env):
    _post_internal_evidence(env, statement="别人看不到的快照")
    snap_id = _post_internal_query(env, freeze=True).json()["memory_snapshot_id"]
    r = client.get(f"/api/v1/fin/memory/snapshots/{snap_id}",
                   headers={"x-test-user": "u-other-" + uuid.uuid4().hex})
    assert r.status_code == 404
    r2 = client.get("/api/v1/fin/memory/snapshots/msnap_does_not_exist",
                    headers={"x-test-user": env["uid"]})
    assert r2.status_code == 404


# ════════════════════════════════════════════════════════════════════════
# 派生标志与市场口径
# ════════════════════════════════════════════════════════════════════════

def test_needs_recheck_is_derived_flag(env):
    _post_internal_evidence(env, statement="已过期的结论", valid_until="2026-01-01T00:00:00+08:00")
    _post_internal_evidence(env, statement="仍有效的结论", valid_until="2099-01-01T00:00:00+08:00")
    _post_internal_evidence(env, statement="无期限的结论")
    items = {it["statement"]: it for it in _post_internal_query(env).json()["items"]}
    assert items["已过期的结论"]["needs_recheck"] is True
    assert items["仍有效的结论"]["needs_recheck"] is False
    assert items["无期限的结论"]["needs_recheck"] is False
    # needs_recheck 是派生标志，**不是状态值** —— status 仍只有三种
    assert items["已过期的结论"]["status"] in ("待验证", "已确认", "已推翻")


def test_for_decision_excludes_expired(env):
    _post_internal_evidence(env, kind="verified", method="全样本回测", sample_size=5,
                            statement="已过期的验证结论",
                            valid_until="2026-01-01T00:00:00+08:00")
    _post_internal_evidence(env, kind="verified", method="全样本回测", sample_size=5,
                            statement="仍有效的验证结论",
                            valid_until="2099-01-01T00:00:00+08:00")
    ids = [it["statement"] for it in _post_internal_query(env, for_decision=True).json()["items"]]
    assert "仍有效的验证结论" in ids
    assert "已过期的验证结论" not in ids


def test_market_filter_includes_cross_market(env):
    """market=NULL 是跨市场结论（表注释），按某市场查时一并返回。"""
    _post_internal_evidence(env, statement="港股专属结论", market="HK")
    _post_internal_evidence(env, statement="美股专属结论", market="US")
    _post_internal_evidence(env, statement="跨市场结论", market=None)

    hk = [it["statement"] for it in _post_internal_query(env, market="HK").json()["items"]]
    assert "港股专属结论" in hk
    assert "跨市场结论" in hk
    assert "美股专属结论" not in hk


def test_invalid_market_400(env):
    r = _post_internal_evidence(env, market="JP")
    assert r.status_code == 400
    assert "market" in r.json()["detail"]
