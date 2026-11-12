# -*- coding: utf-8 -*-
"""L07 · 发布路由真库用例（`TEST_DATABASE_URL` 未设时整体 skip）。

盯住「**不盲目重发**」在 **HTTP 层**也拦得住：`UNKNOWN` 回执下再发 → **409** + 拒绝原文；
带重发标记才放行；核实入口（probe / 手动）能落定；待核实队列只看得到自己的。

    TEST_DATABASE_URL=postgresql://hunter:hunter@127.0.0.1:5598/l07_test \
      cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_publish_router.py -q
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
sys.path.insert(0, str(_API_ROOT / "scripts"))
_bad_app = sys.modules.get("app")
if _bad_app is not None and Path(getattr(_bad_app, "__file__", "") or "").parent == _API_ROOT:
    del sys.modules["app"]

psycopg2 = pytest.importorskip("psycopg2")
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routers import fin_publish  # noqa: E402
from app.services.fin import publish as P  # noqa: E402
from app.services.fin import report as report_svc  # noqa: E402
from fake_publish_receiver import FakeReceiver  # noqa: E402

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()


def _db_ready() -> bool:
    if not TEST_DATABASE_URL:
        return False
    try:
        conn = psycopg2.connect(TEST_DATABASE_URL)
    except Exception:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='fin_publish_receipt' AND column_name='resend_of'")
            return cur.fetchone() is not None
    finally:
        conn.close()


pytestmark = pytest.mark.skipif(
    not _db_ready(), reason="需要 TEST_DATABASE_URL 指向一个跑过 0052 迁移的库")

report_svc.DATABASE_URL = TEST_DATABASE_URL

app = FastAPI()


@app.middleware("http")
async def fake_auth(request: Request, call_next):
    request.state.user_id = request.headers.get("x-test-user") or None
    return await call_next(request)


app.include_router(fin_publish.router, prefix="/api")
client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def env():
    uid = "u-" + uuid.uuid4().hex
    tag = uuid.uuid4().hex[:12]
    project_id, report_id = f"prj_pr_{tag}", f"rpt_pr_{tag}"
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO fin_project (project_id,user_id,tier,initial_capital) "
                        "VALUES (%s,%s,'play',10000)", (project_id, uid))
            cur.execute("INSERT INTO fin_report (report_id,project_id,trade_date,market,"
                        "valuation_as_of,status) VALUES (%s,%s,'2026-09-30','CN_A',now(),'validated')",
                        (report_id, project_id))
            cur.execute("INSERT INTO fin_report_fact (report_id,metric_key,value,unit,source_ref,"
                        "computed_by) VALUES (%s,'nav',1.0,'ratio','fin_valuation:2026-09-30','test')",
                        (report_id,))
        conn.commit()
    finally:
        conn.close()
    yield {"uid": uid, "project_id": project_id, "report_id": report_id}
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM fin_publish_receipt WHERE report_id=%s", (report_id,))
            cur.execute("DELETE FROM fin_report_fact WHERE report_id=%s", (report_id,))
            cur.execute("DELETE FROM fin_report WHERE report_id=%s", (report_id,))
            cur.execute("DELETE FROM fin_project WHERE project_id=%s", (project_id,))
        conn.commit()
    finally:
        conn.close()


def _h(uid):
    return {"x-test-user": uid}


def test_publish_webhook_success(env):
    with FakeReceiver() as recv:
        r = client.post(f"/api/v1/fin/reports/{env['report_id']}/publish",
                        json={"channel": "webhook", "target": f"{recv.url}/deliver"},
                        headers=_h(env["uid"]))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "SUCCESS"
    assert recv.store.order, "接收端收到才算发出去了"


def test_unknown_blocks_resubmit_409_then_resend(env):
    rid = env["report_id"]
    # 造一个 UNKNOWN 回执（等价于上次超时）。
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO fin_publish_receipt (receipt_id,report_id,channel,status,"
                        "target,detail) VALUES (%s,%s,'webhook','UNKNOWN','http://x','超时')",
                        (P.receipt_id_for(rid, "webhook"), rid))
        conn.commit()
    finally:
        conn.close()

    # ① 不带重发标记 → 409，拒绝原文里点名 UNKNOWN 与重发。
    r = client.post(f"/api/v1/fin/reports/{rid}/publish",
                    json={"channel": "webhook", "target": "http://x"}, headers=_h(env["uid"]))
    assert r.status_code == 409, r.text
    msg = r.json()["detail"]["message"]
    assert "UNKNOWN" in msg and "重发" in msg

    # 跨用户不可见（这条报告不属于他）→ 404。
    r2 = client.post(f"/api/v1/fin/reports/{rid}/publish",
                     json={"channel": "webhook", "target": "http://x"}, headers=_h("u-other"))
    assert r2.status_code == 404

    # ② 带重发标记 + 原因 → 放行。
    with FakeReceiver() as recv:
        r3 = client.post(f"/api/v1/fin/reports/{rid}/publish",
                         json={"channel": "webhook", "target": f"{recv.url}/deliver",
                               "resend": True, "reason": "确认未送达"},
                         headers=_h(env["uid"]))
    assert r3.status_code == 200 and r3.json()["status"] == "SUCCESS"


def test_resolve_and_pending_queue(env):
    rid = env["report_id"]
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO fin_publish_receipt (receipt_id,report_id,channel,status,"
                        "detail) VALUES (%s,%s,'file','UNKNOWN','落盘后进程挂了')",
                        (P.receipt_id_for(rid, "file"), rid))
        conn.commit()
    finally:
        conn.close()
    rec = P.receipt_id_for(rid, "file")

    # 待核实队列看得到。
    r = client.get("/api/v1/fin/publish-receipts", headers=_h(env["uid"]))
    assert r.status_code == 200 and any(x["receipt_id"] == rec for x in r.json()["pending"])

    # 读回执（不核实）→ 仍是 UNKNOWN。
    r = client.get(f"/api/v1/fin/publish-receipts/{rec}", headers=_h(env["uid"]))
    assert r.json()["status"] == "UNKNOWN"

    # 手动核实 → FAILED。
    r = client.post(f"/api/v1/fin/publish-receipts/{rec}/resolve",
                    json={"status": "FAILED", "note": "目录里没有文件"}, headers=_h(env["uid"]))
    assert r.status_code == 200 and r.json()["status"] == "FAILED" and r.json()["resolved_at"]

    # 非法核实值 → 400。
    r = client.post(f"/api/v1/fin/publish-receipts/{rec}/resolve",
                    json={"status": "UNKNOWN"}, headers=_h(env["uid"]))
    assert r.status_code == 400

    # 队列里已没有它。
    r = client.get("/api/v1/fin/publish-receipts", headers=_h(env["uid"]))
    assert all(x["receipt_id"] != rec for x in r.json()["pending"])


def test_unauthenticated_401(env):
    r = client.post(f"/api/v1/fin/reports/{env['report_id']}/publish",
                    json={"channel": "webhook", "target": "http://x"})
    assert r.status_code == 401


def test_unknown_channel_400(env):
    r = client.post(f"/api/v1/fin/reports/{env['report_id']}/publish",
                    json={"channel": "wechat", "target": "http://x"}, headers=_h(env["uid"]))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_channel"


def test_unvalidated_report_400(env):
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE fin_report SET status='failed' WHERE report_id=%s",
                        (env["report_id"],))
        conn.commit()
    finally:
        conn.close()
    r = client.post(f"/api/v1/fin/reports/{env['report_id']}/publish",
                    json={"channel": "file", "target": "/tmp"}, headers=_h(env["uid"]))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "not_validated"
