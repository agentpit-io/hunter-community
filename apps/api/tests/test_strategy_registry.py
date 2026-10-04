# -*- coding: utf-8 -*-
"""L04 · 自有策略服务：身份证 + 版本锁 + 三个正式入口（真库用例；`TEST_DATABASE_URL` 未设时整体 skip）。

    TEST_DATABASE_URL=postgresql://hunter:hunter@127.0.0.1:5598/l04_test \
      cd apps/api && PYTHONPATH=. python -m pytest tests/test_strategy_registry.py -q

盯住 L04 的硬约束（`plan/L04.md` §一 / §五）：

  · **版本不可改靠机制**：改 / 删一条已登记策略 → 触发器抛异常（不是靠自觉）；
  · **改策略 = 登记新版本**：内容一变就是新版本键（老行一个字节不动）；
  · **三个入口**：`submit`（未生效）→ `get` 查得到 → `cancel` 掉 → 状态对；
  · **cancel 已生效版本 → 拒绝**（那走回滚路，红线 9）；
  · **不给策略开放松风控的口子**：`target='risk'` 一律拒绝 + 记拒绝事件（红线 8）；
  · **稳定版本键可反查登记行**：`strategy_version` → `fin_strategy_definition`（JOIN 命中）；
  · **内置示例策略登记在册**：`ensure_builtins` 幂等，键与 `tiers` 播种的一致。
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
import psycopg2.errors  # noqa: E402
import psycopg2.extras  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routers import fin_strategy  # noqa: E402
from app.services.fin import strategy as S  # noqa: E402
from app.services.fin import tiers as T  # noqa: E402

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
INTERNAL_KEY = "test-internal-key"

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="需要 TEST_DATABASE_URL 指向一个已跑过 0049 迁移的 postgres")


def _ready() -> bool:
    if not TEST_DATABASE_URL:
        return False
    try:
        conn = psycopg2.connect(TEST_DATABASE_URL)
    except Exception:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('fin_strategy_definition')")
            return cur.fetchone()[0] is not None
    finally:
        conn.close()


if not _ready():
    pytestmark = pytest.mark.skip(reason="库里没有 fin_strategy_definition —— 先跑 0049 迁移")
else:
    S.DATABASE_URL = TEST_DATABASE_URL
    fin_strategy._INTERNAL_KEY = INTERNAL_KEY

app = FastAPI()


@app.middleware("http")
async def fake_auth(request: Request, call_next):
    request.state.user_id = request.headers.get("x-test-user") or None
    return await call_next(request)


app.include_router(fin_strategy.router, prefix="/api")
client = TestClient(app, raise_server_exceptions=False)
IK = {"X-Hunter-Internal-Key": INTERNAL_KEY}

# 内置策略的第一个 key 与它登记后的稳定版本键（`ensure_builtins` 之后可解析）。
_SAMPLE_KEY = next(iter(T._STRATEGIES))
_SAMPLE_DEF = {
    "strategy_key": _SAMPLE_KEY,
    "name": T._STRATEGIES[_SAMPLE_KEY]["name"],
    "source_ref": S.BUILTIN_SOURCE_REF,
    "params": dict(T._STRATEGIES[_SAMPLE_KEY]["params"]),
}
_SAMPLE_VID = S.version_id_of(**_SAMPLE_DEF)


def _conn():
    return psycopg2.connect(TEST_DATABASE_URL)


def _purge(conn, project_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE fin_strategy_candidate DISABLE TRIGGER fin_strategy_candidate_immutable")
        cur.execute("ALTER TABLE fin_strategy_definition DISABLE TRIGGER fin_strategy_definition_immutable")
        cur.execute("DELETE FROM fin_strategy_candidate WHERE project_id = %s", (project_id,))
        # 只删本用例提交的「user」登记（内置的留着，跨用例共用、幂等）。
        cur.execute("DELETE FROM fin_strategy_definition WHERE origin = 'user'")
        cur.execute("ALTER TABLE fin_strategy_definition ENABLE TRIGGER fin_strategy_definition_immutable")
        cur.execute("ALTER TABLE fin_strategy_candidate ENABLE TRIGGER fin_strategy_candidate_immutable")
        cur.execute("DELETE FROM fin_param_change_log WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM fin_param WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM fin_project WHERE project_id = %s", (project_id,))
    conn.commit()


@pytest.fixture()
def env():
    """一个项目 + 参数（active 策略指向内置登记行）+ 已登记的内置策略。"""
    uid = "u-" + uuid.uuid4().hex
    tag = uuid.uuid4().hex[:12]
    pid = f"prj_l04_{tag}"
    conn = _conn()
    try:
        with conn.cursor() as cur:
            S.ensure_builtins(cur)                      # 幂等登记内置策略
            strategies = [
                {"key": "ma_momentum", "name": T._STRATEGIES["ma_momentum"]["name"],
                 "params": dict(T._STRATEGIES["ma_momentum"]["params"]),
                 "version": S.version_id_of(
                     strategy_key="ma_momentum", name=T._STRATEGIES["ma_momentum"]["name"],
                     source_ref=S.BUILTIN_SOURCE_REF,
                     params=dict(T._STRATEGIES["ma_momentum"]["params"])),
                 "active": True},
            ]
            cur.execute("INSERT INTO fin_project (project_id, user_id, tier, initial_capital, "
                        "  market_scope) VALUES (%s, %s, 'play', 100000, 'CN_A')", (pid, uid))
            cur.execute(
                "INSERT INTO fin_param (project_id, stop_loss_pct, take_profit_pct, hold_days_max, "
                "  max_positions, max_position_pct, min_order_amount, daily_max_new, daily_max_orders, "
                "  daily_loss_halt_pct, account_drawdown_halt_pct, strategies) "
                "VALUES (%s, -0.04, 0.06, 3, 2, 0.35, 3000, 1, 2, -0.02, -0.08, %s)",
                (pid, psycopg2.extras.Json(strategies)))
        conn.commit()
    finally:
        conn.close()
    yield {"pid": pid, "uid": uid}
    conn = _conn()
    try:
        _purge(conn, pid)
    finally:
        conn.close()


# ── 内置登记 + 稳定版本键 ────────────────────────────────────────────────

def test_builtins_registered_and_key_matches_tiers(env):
    """内置策略登记在册；`tiers` 播种的版本键 == 登记行的键（同一个内容哈希）。"""
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            S.ensure_builtins(cur)
            cur.execute("SELECT count(*) AS n FROM fin_strategy_definition WHERE origin='builtin'")
            assert cur.fetchone()["n"] == len(T._STRATEGIES)
            row = S._definition_by_version(cur, _SAMPLE_VID)
            assert row is not None and row["origin"] == "builtin"
        # tiers 播种的 version 就是这个键。
        seeded = [s for s in T.build_template("operate")["strategies"] if s["key"] == _SAMPLE_KEY][0]
        assert seeded["version"] == _SAMPLE_VID
    finally:
        conn.close()


def test_version_id_deterministic_and_content_sensitive():
    """同内容 → 同键；内容变一个字 → 新键（「改策略 = 新版本」的机械保证）。"""
    a = S.version_id_of(strategy_key="k", name="n", source_ref="s", params={"p": 1})
    b = S.version_id_of(strategy_key="k", name="n", source_ref="s", params={"p": 1})
    assert a == b and a.startswith("strv_") and len(a) == len("strv_") + 24
    # 键序不影响哈希
    assert a == S.version_id_of(strategy_key="k", name="n", source_ref="s", params={"p": 1})
    # 内容变 → 键变
    assert S.version_id_of(strategy_key="k", name="n", source_ref="s", params={"p": 2}) != a
    assert S.version_id_of(strategy_key="k", name="n2", source_ref="s", params={"p": 1}) != a


# ── 触发器：改 / 删已登记策略都要报错 ────────────────────────────────────

def test_update_registered_definition_is_rejected(env):
    """改一条已登记策略的内容 → 触发器报错（不是靠自觉）。"""
    conn = _conn()
    try:
        with pytest.raises(psycopg2.Error) as ei:
            with conn.cursor() as cur:
                cur.execute("UPDATE fin_strategy_definition SET name='改过的名字' "
                            " WHERE strategy_version_id = %s", (_SAMPLE_VID,))
        conn.rollback()
        assert "只追加" in str(ei.value)
    finally:
        conn.close()


def test_delete_registered_definition_is_rejected(env):
    conn = _conn()
    try:
        with pytest.raises(psycopg2.Error) as ei:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM fin_strategy_definition WHERE strategy_version_id = %s",
                            (_SAMPLE_VID,))
        conn.rollback()
        assert "只追加" in str(ei.value)
    finally:
        conn.close()


# ── 三个入口 ─────────────────────────────────────────────────────────────

def test_submit_get_cancel_roundtrip(env):
    """`submit` 候选 → `get` 查得到 → `cancel` 掉 → 状态对。"""
    body = {"project_id": env["pid"], "strategy_key": "my_v2", "name": "我的新策略",
            "source_ref": "apps/fin-worker/app/strategy/my_v2.py:build_decision",
            "params": {"fast": 8}}
    r = client.post("/api/internal/fin/strategy/submit", headers=IK, json=body)
    assert r.status_code == 200, r.text
    sub = r.json()
    cid = sub["candidate_id"]
    assert cid.startswith("strv_") and sub["status"] == "candidate"

    r = client.get("/api/internal/fin/strategy/get", headers=IK,
                   params={"candidate_id": cid, "project_id": env["pid"]})
    assert r.status_code == 200, r.text
    got = r.json()["candidate"]
    assert got["status"] == "candidate"
    assert [e["kind"] for e in got["events"]] == ["submitted"]

    r = client.post("/api/internal/fin/strategy/cancel", headers=IK, json={"candidate_id": cid})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"

    r = client.get("/api/internal/fin/strategy/get", headers=IK, params={"candidate_id": cid})
    assert r.json()["candidate"]["status"] == "cancelled"


def test_submit_is_idempotent_on_same_content(env):
    """同一份内容登记两次 → 同一个版本键、只有一行定义。"""
    body = {"project_id": env["pid"], "strategy_key": "dup", "name": "重复",
            "source_ref": "x.py:y", "params": {"a": 1}}
    first = client.post("/api/internal/fin/strategy/submit", headers=IK, json=body).json()
    second = client.post("/api/internal/fin/strategy/submit", headers=IK, json=body).json()
    assert first["candidate_id"] == second["candidate_id"]
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fin_strategy_definition WHERE strategy_key='dup'")
            assert cur.fetchone()[0] == 1
    finally:
        conn.close()


def test_cancel_active_version_is_refused(env):
    """`cancel` 一个**已生效**的版本 → 拒绝（那走回滚路，红线 9）。

    项目 active 策略指向内置登记行；`submit` 同一份内容得到同一个版本键（幂等）——
    于是这个候选人**就是当前生效版本**。
    """
    body = {"project_id": env["pid"], **_SAMPLE_DEF}
    sub = client.post("/api/internal/fin/strategy/submit", headers=IK, json=body).json()
    assert sub["candidate_id"] == _SAMPLE_VID
    got = client.get("/api/internal/fin/strategy/get", headers=IK,
                     params={"candidate_id": _SAMPLE_VID, "project_id": env["pid"]}).json()
    assert got["candidate"]["status"] == "active"          # get 认出「生效中」

    r = client.post("/api/internal/fin/strategy/cancel", headers=IK,
                    json={"candidate_id": _SAMPLE_VID, "project_id": env["pid"]})
    assert r.status_code == 409, r.text
    assert "已经生效" in r.json()["detail"]


def test_cancel_unknown_candidate_is_404(env):
    r = client.post("/api/internal/fin/strategy/cancel", headers=IK,
                    json={"candidate_id": "strv_ffffffffffffffffffffffff"})
    assert r.status_code == 404


def test_cancel_active_is_refused_even_without_submitted_event(env):
    """回归：生效的内置版本**从没 submit 过**时，也不能被 cancel。

    真出过的 bug：旧 `_project_status` 见到「没有事件」直接返回 candidate，把生效判据短路掉，
    于是 `cancel 已生效版本 → 拒绝` 被绕过（线上实测被 cancel 成功）。生效判据**必须**
    独立于事件 —— 这个用例盯着它不许退回去。
    """
    r = client.post("/api/internal/fin/strategy/cancel", headers=IK,
                    json={"candidate_id": _SAMPLE_VID, "project_id": env["pid"]})
    assert r.status_code == 409, r.text
    # 库里没有留下 cancelled 事件（拒绝是真的拒绝，不是「记了再说」）。
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fin_strategy_candidate WHERE candidate_id = %s "
                        " AND kind = 'cancelled'", (_SAMPLE_VID,))
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()


def test_cancel_unsubmitted_registered_version_refused(env):
    """不是候选（从没 submit）→ 拒绝：只有 submit 过的候选才能 cancel。"""
    vid = S.version_id_of(strategy_key="never_submitted", name="没提交过",
                          source_ref="x.py:y", params={})
    conn = _conn()
    try:
        with conn.cursor() as cur:
            S.register(cur, strategy_key="never_submitted", name="没提交过",
                       source_ref="x.py:y", params={})
        conn.commit()
    finally:
        conn.close()
    r = client.post("/api/internal/fin/strategy/cancel", headers=IK,
                    json={"candidate_id": vid, "project_id": env["pid"]})
    assert r.status_code == 400, r.text
    assert "没有提交记录" in r.json()["detail"]


def test_get_reports_registered_status_for_builtin(env):
    """从没 submit 过的内置登记行 → `get` 如实报「已登记（未提交候选）」，不假称候选。"""
    r = client.get("/api/internal/fin/strategy/get", headers=IK,
                   params={"candidate_id": _SAMPLE_VID})
    # `_SAMPLE_VID` 正好是 env 的 active 版本 → 生效中；换一个非 active 的内置 key。
    assert r.status_code == 200
    other = [k for k in T._STRATEGIES if k != "ma_momentum"][0]
    other_vid = S.version_id_of(
        strategy_key=other, name=T._STRATEGIES[other]["name"],
        source_ref=S.BUILTIN_SOURCE_REF, params=dict(T._STRATEGIES[other]["params"]))
    r = client.get("/api/internal/fin/strategy/get", headers=IK,
                   params={"candidate_id": other_vid, "project_id": env["pid"]})
    assert r.json()["candidate"]["status"] == "registered"


# ── 红线 8：不给策略开放松风控的口子 ─────────────────────────────────────

def test_risk_target_refused_and_events_recorded(env):
    body = {"project_id": env["pid"], "strategy_key": "sneaky", "name": "偷偷放风控",
            "source_ref": "x.py:y", "params": {"max_position_pct": 0.99},
            "target": "risk"}
    r = client.post("/api/internal/fin/strategy/submit", headers=IK, json=body)
    assert r.status_code == 400, r.text
    assert "风控" in r.json()["detail"]
    # 记了拒绝事件（红线：一律拒绝并记拒绝事件）。
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fin_strategy_candidate "
                        " WHERE kind='rejected' AND project_id = %s", (env["pid"],))
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT count(*) FROM fin_strategy_definition WHERE strategy_key='sneaky'")
            assert cur.fetchone()[0] == 0                 # 拒绝了就不登记
    finally:
        conn.close()


# ── 稳定版本键可反查登记行（决策对象带的 strategy_version）──────────────

def test_active_resolves_to_registry_row(env):
    """`/active` 解析出的 `strategy_version` 能 JOIN 回登记行（决策复盘的反查路径）。"""
    r = client.get("/api/internal/fin/strategy/active", headers=IK,
                   params={"project_id": env["pid"]})
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["strategy_version"] == _SAMPLE_VID
    assert info["resolved_by"] == "declared_version"
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 这条 SQL 就是出口标准里「strategy_version 反查到登记行」的形态。
            cur.execute(
                "SELECT d.strategy_key, d.name, d.content_hash FROM fin_strategy_definition d "
                " WHERE d.strategy_version_id = %s", (info["strategy_version"],))
            row = cur.fetchone()
            assert row is not None and row["strategy_key"] == "ma_momentum"
            assert len(row["content_hash"]) == 64
    finally:
        conn.close()


def test_active_falls_back_by_key_for_legacy_version(env):
    """老库 `version` 还是自由字符串 `"v1"` 时，按 key 解析到内置登记行（不编版本键）。"""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE fin_param SET strategies = %s WHERE project_id = %s",
                        (psycopg2.extras.Json([
                            {"key": "ma_momentum", "version": "v1", "active": True}]),
                         env["pid"]))
        conn.commit()
        r = client.get("/api/internal/fin/strategy/active", headers=IK,
                       params={"project_id": env["pid"]})
        info = r.json()
        assert info["resolved_by"] == "builtin_key"
        assert info["strategy_version"] == _SAMPLE_VID
    finally:
        conn.close()


def test_active_unregistered_reports_honestly(env):
    """key 没有登记行 → 如实返回声明原值、`resolved_by='unregistered'`（不假称登记过）。"""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE fin_param SET strategies = %s WHERE project_id = %s",
                        (psycopg2.extras.Json([
                            {"key": "no_such_key", "version": "v9", "active": True}]),
                         env["pid"]))
        conn.commit()
        r = client.get("/api/internal/fin/strategy/active", headers=IK,
                       params={"project_id": env["pid"]})
        info = r.json()
        assert info["resolved_by"] == "unregistered"
        assert info["strategy_version"] == "v9" and info["definition"] is None
    finally:
        conn.close()
