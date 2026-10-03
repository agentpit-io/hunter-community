# -*- coding: utf-8 -*-
"""开户接口的路由层用例（真库，`TEST_DATABASE_URL` 未设时整体 skip）。

    TEST_DATABASE_URL=postgresql://hunter:hunter@127.0.0.1:5599/hunter \
      cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_project_router.py -q

盯住三条会静默出错的规矩：
  · **重复开户不建出第二个进行中项目**（靠 `fin_project_one_active` 部分唯一索引 + 服务端幂等）
  · **本金与档位一一对应**（三个档位各建一次，读回来的 initial_capital 必须是 1万/10万/100万）
  · **参数由服务端写死**（请求体里塞 initial_capital / stop_loss_pct 一律不被采纳）

做法：真 import 路由，挂到最小 FastAPI 上，用测试中间件按 header 设 `request.state.user_id`
（替代 JWT 中间件）；每个用例用一个全新的随机 user_id，跑完清掉自己写进去的行。
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

# ── sys.path 归一（同 test_fin_tiers.py 的说明）─────────────────────────
# `apps/api/__init__.py` 是空文件 → `/app` 会被当成包 `app`；共享 conftest 又把
# `/` 插进 sys.path，于是在 pytest 进程内 `import app` 会命中 `/app` 目录包而不是
# `/app/app`。脚本式用例跑在子进程里不会撞上，pytest 进程内收集会。
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

from app.routers import fin_project  # noqa: E402
from app.services.fin import store  # noqa: E402

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="需要 TEST_DATABASE_URL 指向一个已跑过 0023 迁移的 postgres（见 test_migrate.py）",
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
            cur.execute("SELECT to_regclass('fin_project')")
            return cur.fetchone()[0] is not None
    finally:
        conn.close()


_ready = _tables_ready()
if not _ready:
    pytestmark = pytest.mark.skip(reason="库里没有 fin_project —— 先跑 0023 迁移")
else:
    store.DATABASE_URL = TEST_DATABASE_URL


# ── 测试 app：header 决定登录身份 ──────────────────────────────────────
app = FastAPI()


@app.middleware("http")
async def fake_auth(request: Request, call_next):
    request.state.user_id = request.headers.get("x-test-user") or None
    return await call_next(request)


app.include_router(fin_project.router, prefix="/api")
client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def uid():
    u = "u-" + uuid.uuid4().hex
    yield u
    # 清掉这个测试用户写进去的行（fin_param 有 FK，先删子表）
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            # P1 起 fin_project_market 也引用项目（市场集合），先删它
            cur.execute("DELETE FROM fin_project_market WHERE project_id IN "
                        "(SELECT project_id FROM fin_project WHERE user_id = %s)", (u,))
            # M-16 起 fin_param_change_log 也会引用项目（关停留痕），先删它
            cur.execute("DELETE FROM fin_param_change_log WHERE project_id IN "
                        "(SELECT project_id FROM fin_project WHERE user_id = %s)", (u,))
            cur.execute("DELETE FROM fin_param WHERE project_id IN "
                        "(SELECT project_id FROM fin_project WHERE user_id = %s)", (u,))
            cur.execute("DELETE FROM fin_project WHERE user_id = %s", (u,))
            cur.execute("DELETE FROM fin_account WHERE user_id = %s", (u,))
        conn.commit()
    finally:
        conn.close()


def _count_active(uid: str) -> int:
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fin_project WHERE user_id = %s AND status = 'active'", (uid,))
            return cur.fetchone()[0]
    finally:
        conn.close()


def test_未登录_401():
    r = client.get("/api/v1/fin/projects/current")
    assert r.status_code == 401
    assert r.json()["detail"]["need_login"] is True


def test_档位清单三档():
    r = client.get("/api/v1/fin/tiers", headers={"x-test-user": "u-any"})
    assert r.status_code == 200
    assert [t["tier"] for t in r.json()["tiers"]] == ["play", "manage", "operate"]


def test_当前项目为空时返回_null():
    r = client.get("/api/v1/fin/projects/current", headers={"x-test-user": "u-" + uuid.uuid4().hex})
    assert r.status_code == 200
    assert r.json() == {"project": None, "param": None, "tier_template": None}


@pytest.mark.parametrize("tier,capital", [("play", 10_000), ("manage", 100_000), ("operate", 1_000_000)])
def test_三个档位各建一次_本金一一对应(tier, capital, uid):
    r = client.post("/api/v1/fin/projects", json={"tier": tier}, headers={"x-test-user": uid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True
    assert body["project"]["tier"] == tier
    assert body["project"]["initial_capital"] == capital
    assert body["project"]["status"] == "active"
    assert body["project"]["currency"] == "CNY"
    assert body["project"]["market_scope"] == "CN_A"
    assert body["project"]["run_mode"] == "auto"          # 一期恒 auto
    # 参数照档位写死：止损/止盈来自 tiers，不是请求体
    assert body["param"]["stop_loss_pct"] == body["tier_template"]["stop_loss_pct"]
    assert body["param"]["take_profit_pct"] == body["tier_template"]["take_profit_pct"]
    assert _count_active(uid) == 1


def test_重复开户不建第二个(uid):
    r1 = client.post("/api/v1/fin/projects", json={"tier": "manage"}, headers={"x-test-user": uid})
    r2 = client.post("/api/v1/fin/projects", json={"tier": "manage"}, headers={"x-test-user": uid})
    r3 = client.post("/api/v1/fin/projects", json={"tier": "manage"}, headers={"x-test-user": uid})
    assert r1.json()["created"] is True
    assert r2.json()["created"] is False and r3.json()["created"] is False
    assert r1.json()["project"]["project_id"] == r2.json()["project"]["project_id"] == r3.json()["project"]["project_id"]
    assert _count_active(uid) == 1


def test_已有项目时选别的档位也返回现成的(uid):
    r1 = client.post("/api/v1/fin/projects", json={"tier": "play"}, headers={"x-test-user": uid})
    r2 = client.post("/api/v1/fin/projects", json={"tier": "operate"}, headers={"x-test-user": uid})
    assert r2.json()["created"] is False
    # 返回的是现成那一个的真实档位（不改档位）—— 换档位要走「开新项目」
    assert r2.json()["project"]["tier"] == "play"
    assert r2.json()["project"]["initial_capital"] == 10_000
    assert _count_active(uid) == 1


def test_请求体里的数字不被采纳(uid):
    # 客户端塞本金/止损/持仓数 —— 服务端只认 tier，其余一律忽略
    payload = {"tier": "play", "initial_capital": 999_999_999, "stop_loss_pct": -0.5,
               "max_positions": 99}
    r = client.post("/api/v1/fin/projects", json=payload, headers={"x-test-user": uid})
    assert r.status_code == 200, r.text
    assert r.json()["project"]["initial_capital"] == 10_000
    assert r.json()["param"]["stop_loss_pct"] == -0.04
    assert r.json()["param"]["max_positions"] == 2


def test_查当前项目带参数(uid):
    client.post("/api/v1/fin/projects", json={"tier": "operate"}, headers={"x-test-user": uid})
    r = client.get("/api/v1/fin/projects/current", headers={"x-test-user": uid})
    body = r.json()
    assert body["project"]["tier"] == "operate"
    assert body["param"]["max_positions"] == 8
    assert body["param"]["board_flags"]["star"] is True
    assert body["param"]["params_locked_at"] is None       # 一期没有成交，还没锁


def test_未知档位_400(uid):
    r = client.post("/api/v1/fin/projects", json={"tier": "rich"}, headers={"x-test-user": uid})
    assert r.status_code == 400
    assert _count_active(uid) == 0


# ══════════════════════════════════════════════════════════════════════
# M-16 · 项目制与「开新项目」
# ══════════════════════════════════════════════════════════════════════

def _change_log(project_id: str) -> list[tuple]:
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT field, old_value, new_value FROM fin_param_change_log "
                "WHERE project_id = %s ORDER BY id", (project_id,))
            return cur.fetchall()
    finally:
        conn.close()


def test_开新项目先关闭旧项目不是暂停(uid):
    r1 = client.post("/api/v1/fin/projects", json={"tier": "play"}, headers={"x-test-user": uid})
    old_id = r1.json()["project"]["project_id"]

    r2 = client.post("/api/v1/fin/projects/new",
                     json={"tier": "manage", "reason": "换成 10 万档重来"},
                     headers={"x-test-user": uid})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["created"] is True
    assert body["project"]["tier"] == "manage"
    assert body["closed_project"]["project_id"] == old_id
    assert body["closed_project"]["status"] == "closed"
    assert body["closed_project"]["close_reason"] == "换成 10 万档重来"
    assert body["closed_project"]["closed_at"] is not None

    # 旧项目真的落成 closed（不是 paused —— 状态枚举里根本没有 paused）
    r_old = client.get(f"/api/v1/fin/projects/{old_id}", headers={"x-test-user": uid})
    assert r_old.status_code == 200
    assert r_old.json()["project"]["status"] == "closed"
    assert r_old.json()["project"]["close_reason"] == "换成 10 万档重来"

    # 同一用户仍然只有一个 active
    assert _count_active(uid) == 1


def test_开新项目关停动作记入变更日志(uid):
    r1 = client.post("/api/v1/fin/projects", json={"tier": "play"}, headers={"x-test-user": uid})
    old_id = r1.json()["project"]["project_id"]
    client.post("/api/v1/fin/projects/new",
                json={"tier": "operate", "reason": "重开"}, headers={"x-test-user": uid})

    entries = dict((f, (o, n)) for f, o, n in _change_log(old_id))
    assert entries["status"] == ("active", "closed")
    assert entries["close_reason"] == (None, "重开")


def test_连续开新项目始终只有一个active(uid):
    ids = []
    for tier in ("play", "manage", "operate", "manage"):
        r = client.post("/api/v1/fin/projects/new",
                        json={"tier": tier, "reason": "再开一个"}, headers={"x-test-user": uid})
        assert r.status_code == 200, r.text
        ids.append(r.json()["project"]["project_id"])
        assert _count_active(uid) == 1          # 每一步都只有一个 active
    # 旧的那几个都还能读到，且都是 closed
    for old in ids[:-1]:
        got = client.get(f"/api/v1/fin/projects/{old}", headers={"x-test-user": uid})
        assert got.status_code == 200
        assert got.json()["project"]["status"] == "closed"


def test_列表能看到全部项目含已关闭(uid):
    r1 = client.post("/api/v1/fin/projects", json={"tier": "play"}, headers={"x-test-user": uid})
    client.post("/api/v1/fin/projects/new",
                json={"tier": "manage", "reason": "重开"}, headers={"x-test-user": uid})
    r = client.get("/api/v1/fin/projects", headers={"x-test-user": uid})
    items = r.json()["items"]
    assert len(items) == 2
    statuses = {i["project_id"]: i["status"] for i in items}
    assert statuses[r1.json()["project"]["project_id"]] == "closed"
    assert sorted(statuses.values()) == ["active", "closed"]


def test_读别人的项目_404(uid):
    r1 = client.post("/api/v1/fin/projects", json={"tier": "play"}, headers={"x-test-user": uid})
    other = "u-" + uuid.uuid4().hex
    r = client.get(f"/api/v1/fin/projects/{r1.json()['project']['project_id']}",
                   headers={"x-test-user": other})
    assert r.status_code == 404


def test_开新项目_空原因_400(uid):
    client.post("/api/v1/fin/projects", json={"tier": "play"}, headers={"x-test-user": uid})
    r = client.post("/api/v1/fin/projects/new",
                    json={"tier": "play", "reason": "  "}, headers={"x-test-user": uid})
    assert r.status_code == 400
    assert _count_active(uid) == 1
