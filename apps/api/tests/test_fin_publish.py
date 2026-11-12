# -*- coding: utf-8 -*-
"""L07 · 发布适配器 + `UNKNOWN` 待核实（`plan/L07.md` §四 出口标准）。

两组：

  A. **不连库**（快、必跑）：三个适配器的三态判定 —— 用**本机假接收端**
     （`scripts/fake_publish_receiver.py`）真发真收，不 mock httpx；
  B. **连库**（`TEST_DATABASE_URL` 未设时整体 skip）：`UNKNOWN` 的完整生命周期 ——
     第 7 项故障注入（超时 → UNKNOWN）、**不盲目重发**、核实落定。

    # A 组（不需要库）
    cd apps/api && PYTHONPATH=. .venv/bin/python -m pytest tests/test_fin_publish.py -q

    # B 组（需要一个跑过 0052 迁移的副本库）
    TEST_DATABASE_URL=postgresql://hunter:hunter@127.0.0.1:5598/l07_test \
      PYTHONPATH=. .venv/bin/python -m pytest tests/test_fin_publish.py -q
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
sys.path.insert(0, str(_API_ROOT / "scripts"))     # 假接收端
_bad_app = sys.modules.get("app")
if _bad_app is not None and Path(getattr(_bad_app, "__file__", "") or "").parent == _API_ROOT:
    del sys.modules["app"]

from app.services.fin import publish as P            # noqa: E402
from app.services.fin.publish import webhook as W    # noqa: E402
from app.services.fin.publish import file as F       # noqa: E402
from app.services.fin.publish import in_app as IA    # noqa: E402
from fake_publish_receiver import FakeReceiver       # noqa: E402

import hashlib                                        # noqa: E402
import socket                                         # noqa: E402


# ════════════════════════════════════════════════════════════════════════
# A · 适配器三态（不连库）
# ════════════════════════════════════════════════════════════════════════

def _report(**over) -> dict:
    r = {"report_id": "rpt_unit01", "trade_date": "2026-09-30", "market": "CN_A",
         "currency": "CNY", "status": "validated", "analysis_text": "测试"}
    r.update(over)
    return r


def test_receipt_id_in_app_matches_legacy():
    """`in_app` 的回执 id 必须与改造前**逐字节一致**（老数据 / 老用例不动）。"""
    legacy = "rcp_" + hashlib.sha256("rpt_abc:in_app".encode()).hexdigest()[:24]
    assert P.receipt_id_for("rpt_abc", "in_app") == legacy
    # 不同渠道天然不同 id（同一报告可发多个渠道，各留一行回执）。
    assert P.receipt_id_for("rpt_abc", "webhook") != legacy


def test_parse_target_two_forms():
    assert P.parse_target("http://x/y") == {"url": "http://x/y"}
    assert P.parse_target({"url": "http://x/y", "timeout": 1}) == {"url": "http://x/y", "timeout": 1}
    assert P.parse_target(None) == {}


def test_webhook_success():
    with FakeReceiver() as recv:
        res = W.WebhookAdapter().submit(_report(), f"{recv.url}/deliver", html="<p>hi</p>")
        assert res.status == P.SUCCESS, res
        assert res.external_id                      # 对方回执号
        assert res.target == f"{recv.url}/deliver"
        # 「发出去了」的证据：接收端真收到了这份报告。
        assert recv.store.order and recv.store.order[0]["receipt_key"] == \
            P.receipt_id_for("rpt_unit01", "webhook")
        assert "rpt_unit01" in recv.store.order[0]["body_head"]


def test_webhook_timeout_is_unknown_then_probe_resolves():
    """**故障注入第 7 项**：内容已送达，但回执超时 → UNKNOWN；回查外部状态才落定。"""
    with FakeReceiver(hang_s=2.0) as recv:
        target = {"url": f"{recv.url}/deliver_then_hang", "status_url": f"{recv.url}/status",
                  "timeout": 0.4}
        res = W.WebhookAdapter().submit(_report(), target, html="<p>hi</p>")
        assert res.status == P.UNKNOWN, res
        # **内容其实已经到了** —— 这就是「不盲目重发」的理由。
        assert recv.store.received(P.receipt_id_for("rpt_unit01", "webhook"))

        # 核实：查外部状态 → 收到过 → 落 SUCCESS。
        probe = W.WebhookAdapter().probe({"receipt_id": P.receipt_id_for("rpt_unit01", "webhook"),
                                          "target": target, "detail": ""})
        assert probe == P.SUCCESS


def test_webhook_refused_is_failed_not_unknown():
    """连不上（端口无人监听）→ **没发出去** → FAILED（可安全重试），不是 UNKNOWN。"""
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    res = W.WebhookAdapter().submit(_report(), f"http://127.0.0.1:{port}/deliver")
    assert res.status == P.FAILED, res


def test_webhook_no_url_is_failed():
    res = W.WebhookAdapter().submit(_report(), None)
    assert res.status == P.FAILED and "未配置" in res.detail


def test_file_success_checksum_and_probe(tmp_path):
    dest = tmp_path / "out"
    res = F.FileAdapter().submit(_report(), str(dest), html="<html>ok</html>")
    assert res.status == P.SUCCESS, res
    path = Path(res.external_id)
    assert path.exists() and path.name == "rpt_unit01.html"
    assert f"sha256={hashlib.sha256(b'<html>ok</html>').hexdigest()}" in res.detail

    receipt = {"external_id": res.external_id, "detail": res.detail, "target": res.target,
               "report_id": "rpt_unit01"}
    assert F.FileAdapter().probe(receipt) == P.SUCCESS        # 没动过 → 一致
    path.write_text("tampered", encoding="utf-8")
    assert F.FileAdapter().probe(receipt) == P.FAILED          # 被改过 → 落 FAILED
    path.unlink()
    assert F.FileAdapter().probe(receipt) == P.FAILED          # 没了 → 落 FAILED


def test_file_no_target_is_failed():
    assert F.FileAdapter().submit(_report(), None, html="x").status == P.FAILED


def test_in_app_rejects_unvalidated():
    """校验没过 → 站内也不发（**连产物都不生成**）。"""
    res = IA.InAppAdapter().submit(_report(status="failed"), None, html="<p>x</p>",
                                   ctx={"cur": object(), "project": {"project_id": "p"}})
    assert res.status == P.FAILED and "回读校验未通过" in res.detail


def test_only_three_channels_registered():
    """§6.1 边界：**只有三个渠道**，不许自行放大到第三方平台。"""
    assert set(P.ADAPTERS) == {"in_app", "webhook", "file"}
    assert P.CHANNELS == ("in_app", "webhook", "file")


# ════════════════════════════════════════════════════════════════════════
# B · UNKNOWN 生命周期（连库）
# ════════════════════════════════════════════════════════════════════════

psycopg2 = pytest.importorskip("psycopg2")
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
            cur.execute("SELECT to_regclass('fin_publish_receipt')")
            if cur.fetchone()[0] is None:
                return False
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='fin_publish_receipt' AND column_name='resend_of'")
            return cur.fetchone() is not None      # 0052 是否已应用
    finally:
        conn.close()


# A 组（适配器三态）不连库，**照常跑**；只把 B 组（UNKNOWN 生命周期）标成 skip。
_db = pytest.mark.skipif(
    not _db_ready(), reason="需要 TEST_DATABASE_URL 指向一个跑过 0052 迁移的库")


@pytest.fixture()
def env():
    """一个项目 + 一份**已校验通过**的报告（发布只认 validated）。跑完精删。"""
    from app.services.fin import report as report_svc
    report_svc.DATABASE_URL = TEST_DATABASE_URL
    uid = "u-" + uuid.uuid4().hex
    tag = uuid.uuid4().hex[:12]
    project_id, report_id = f"prj_pub_{tag}", f"rpt_pub_{tag}"
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO fin_project (project_id, user_id, tier, initial_capital) "
                        "VALUES (%s,%s,'play',10000)", (project_id, uid))
            cur.execute(
                "INSERT INTO fin_report (report_id, project_id, trade_date, market, valuation_as_of, status) "
                "VALUES (%s,%s,'2026-09-30','CN_A', now(), 'validated')", (report_id, project_id))
            cur.execute(
                "INSERT INTO fin_report_fact (report_id, metric_key, value, unit, source_ref, computed_by) "
                "VALUES (%s,'nav',1.0,'ratio','fin_valuation:2026-09-30','test')", (report_id,))
        conn.commit()
    finally:
        conn.close()
    yield {"uid": uid, "project_id": project_id, "report_id": report_id}
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM hunter_artifacts.published_artifact "
                        "WHERE source_message_id = %s", (f"fin-report:{report_id}",))
            cur.execute("DELETE FROM fin_publish_receipt WHERE report_id=%s", (report_id,))
            cur.execute("DELETE FROM fin_report_fact WHERE report_id=%s", (report_id,))
            cur.execute("DELETE FROM fin_report WHERE report_id=%s", (report_id,))
            cur.execute("DELETE FROM fin_project WHERE project_id=%s", (project_id,))
        conn.commit()
    finally:
        conn.close()


def _conn():
    return psycopg2.connect(TEST_DATABASE_URL)


def _receipt(report_id, channel):
    conn = _conn()
    try:
        return P.get(conn, P.receipt_id_for(report_id, channel))
    finally:
        conn.close()


@_db
def test_submit_webhook_success_and_receipt(env):
    with FakeReceiver() as recv:
        conn = _conn()
        try:
            out = P.submit(conn, env["report_id"], "webhook",
                           target=f"{recv.url}/deliver", html="<p>hi</p>")
        finally:
            conn.close()
        assert out["status"] == P.SUCCESS and out["idempotent"] is False
        row = _receipt(env["report_id"], "webhook")
        assert row["channel"] == "webhook" and row["status"] == "SUCCESS"
        assert row["target"] == f"{recv.url}/deliver"
        assert recv.store.order, "接收端必须真收到"


@_db
def test_submit_unknown_then_blocks_resubmit_then_resend(env):
    """**核心**：超时 → UNKNOWN → 再发被拒 → 带重发标记才放行（且留痕）。"""
    rid = env["report_id"]
    # 先手工造一个 UNKNOWN（等价于上次超时留下的回执）。
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO fin_publish_receipt (receipt_id, report_id, channel, status, target, detail) "
                "VALUES (%s,%s,'webhook','UNKNOWN','http://127.0.0.1:9/deliver','超时')",
                (P.receipt_id_for(rid, "webhook"), rid))
        conn.commit()
    finally:
        conn.close()

    # ① 不带重发标记再发 → **拒绝**。
    conn = _conn()
    try:
        with pytest.raises(P.PublishBlocked) as ei:
            P.submit(conn, rid, "webhook", target="http://127.0.0.1:9/deliver", html="x")
        assert ei.value.code == "unknown_needs_resend"
        assert "UNKNOWN" in ei.value.reason and "重发" in ei.value.reason
    finally:
        conn.close()

    # ② 带重发标记 + 原因 → 放行，且**留痕**（谁 / 何时 / 为何）。
    with FakeReceiver() as recv:
        conn = _conn()
        try:
            out = P.submit(conn, rid, "webhook", target=f"{recv.url}/deliver", html="x",
                           resend=True, actor="u-boss", reason="上次超时，已确认未送达")
        finally:
            conn.close()
    assert out["status"] == P.SUCCESS and out["resend"] is True
    row = _receipt(rid, "webhook")
    assert row["resend_of"] and row["resend_by"] == "u-boss"
    assert row["resend_reason"] == "上次超时，已确认未送达" and row["resend_at"]


@_db
def test_submit_success_is_idempotent(env):
    """已成功 → 再发**幂等返回**，不重复发布（`01方案 §2`）。"""
    with FakeReceiver() as recv:
        conn = _conn()
        try:
            P.submit(conn, env["report_id"], "webhook", target=f"{recv.url}/deliver", html="x")
            again = P.submit(conn, env["report_id"], "webhook",
                             target=f"{recv.url}/deliver", html="x")
        finally:
            conn.close()
    assert again["idempotent"] is True
    assert len(recv.store.order) == 1, "第二次不许再打接收端"


@_db
def test_resolve_manual_and_probe(env):
    """核实入口：查外部状态（probe）落定；查不出可**手动**落定。"""
    rid = env["report_id"]
    with FakeReceiver() as recv:
        target = {"url": f"{recv.url}/deliver_then_hang", "status_url": f"{recv.url}/status",
                  "timeout": 0.4}
        conn = _conn()
        try:
            out = P.submit(conn, rid, "webhook", target=target, html="<p>z</p>")
        finally:
            conn.close()
        assert out["status"] == P.UNKNOWN
        receipt_id = P.receipt_id_for(rid, "webhook")

        # 查外部状态 → 收到过 → 落 SUCCESS，resolved_at 写上。
        conn = _conn()
        try:
            got = P.get(conn, receipt_id, resolve=True)
        finally:
            conn.close()
        assert got["status"] == P.SUCCESS and got["resolved_at"]
        assert got["probe"] == P.SUCCESS

    # 手动核实路径（另一条渠道，制造一个查不出的 UNKNOWN）。
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO fin_publish_receipt (receipt_id, report_id, channel, status, detail) "
                "VALUES (%s,%s,'file','UNKNOWN','落盘后进程挂了')",
                (P.receipt_id_for(rid, "file"), rid))
        conn.commit()
        manual = P.resolve(conn, P.receipt_id_for(rid, "file"), P.FAILED,
                           actor="u-ops", note="目录里没有这个文件")
        pend = P.list_pending(conn, owner_user_id=env["uid"])
    finally:
        conn.close()
    assert manual["status"] == P.FAILED and manual["resolved_at"]
    assert "u-ops" in manual["detail"]
    assert all(r["report_id"] == rid for r in pend)      # 队列里只剩这一条（webhook 已落定）


@_db
def test_submit_rejects_unvalidated_report(env):
    """校验没过 → 一律拒绝发布（**红线 5 不放松**）。"""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE fin_report SET status='failed' WHERE report_id=%s",
                        (env["report_id"],))
        conn.commit()
        with pytest.raises(P.PublishBlocked) as ei:
            P.submit(conn, env["report_id"], "file", target="/tmp", html="x")
        assert ei.value.code == "not_validated"
    finally:
        conn.close()


@_db
def test_submit_bad_channel_and_missing_report(env):
    conn = _conn()
    try:
        with pytest.raises(P.PublishBlocked) as ei:
            P.submit(conn, env["report_id"], "wechat", html="x")
        assert ei.value.code == "bad_channel"
        with pytest.raises(P.PublishBlocked) as ei2:
            P.submit(conn, "rpt_nope", "file", html="x")
        assert ei2.value.code == "not_found"
    finally:
        conn.close()


@_db
def test_persist_in_app_receipt_unchanged(env):
    """生成流程里的站内发布改走适配器后，回执**逐字段与改造前一致**（老行为不变）。"""
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal
    from app.services.fin import report as report_svc
    report_svc.DATABASE_URL = TEST_DATABASE_URL
    sh = timezone(timedelta(hours=8))
    as_of = datetime(2026, 9, 30, 15, 30, tzinfo=sh)
    ctx = {
        "project": {"project_id": env["project_id"], "user_id": env["uid"],
                    "tier": "play", "initial_capital": Decimal(100000)},
        "trade_date": "2026-09-30", "market": "CN_A", "currency": "CNY",
        "valuation_latest": {"as_of": as_of, "nav": 1.0, "total_assets": 100000,
                             "cash_available": 100000, "cash_frozen": Decimal(0),
                             "market_value": Decimal(0), "quality": "ok", "missing_flag": False},
        "valuation_series": [{"as_of": as_of, "nav": 1.0, "total_assets": 100000}],
        "trades": [], "positions": [], "open_order_count": 0, "recon_latest": {"passed": True},
    }
    facts = report_svc.build_facts(ctx)
    analysis = {"analysis_text": "本日无成交。", "self_review": None,
                "llm_provider": "test", "llm_model": "t", "used_fallback": True}
    conn = _conn()
    try:
        out = report_svc.persist(conn, ctx, facts, analysis)
    finally:
        conn.close()
    assert out["status"] == "validated"
    row = _receipt(env["report_id"], "in_app")
    assert row["receipt_id"] == P.receipt_id_for(env["report_id"], "in_app")
    assert row["channel"] == "in_app" and row["status"] == "SUCCESS"
    assert row["external_id"] == out["artifact_ref"] and row["detail"] == "站内产物"
    assert row["target"] is None and row["resend_of"] is None     # 新列对老渠道保持空
