#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L07 · **故障注入第 7 项**（`01方案 §14.1`）：「内容已发布，但返回超时或网络断开」。

这一项在完整度分析里是**唯一因「没有外部渠道」而做不了的**故障注入 —— 现在有了
通用 Webhook 适配器，就能真跑：

  1. 本机起假接收端，`/deliver_then_hang` **先把请求体完整收下并记账，再挂起**；
  2. 发布方 `timeout=0.5s` → 读超时 → 判 **`UNKNOWN`**（可能已送达，**不盲目重发**）；
  3. 打印接收端日志 —— **内容其实已经到了**；
  4. 不带重发标记再发同一份 → **被拒**（贴拒绝原文）；
  5. 核实：查外部状态 `/status` → 收到过 → 落 **`SUCCESS`**。

用法（需要一个跑过 `0052` 迁移的库；默认用 `TEST_DATABASE_URL`）::

    TEST_DATABASE_URL=postgresql://hunter:hunter@127.0.0.1:5598/l07_test \
      cd apps/api && PYTHONPATH=. python scripts/inject_publish_timeout.py

跑完自动清理临时数据（项目 / 报告 / 回执）。
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))
sys.path.insert(0, str(_API_ROOT / "scripts"))

import psycopg2  # noqa: E402
from fake_publish_receiver import FakeReceiver  # noqa: E402

from app.services.fin import publish as P  # noqa: E402

DSN = (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
if not DSN:
    print("需要 TEST_DATABASE_URL（或 DATABASE_URL）指向一个跑过 0052 迁移的库", file=sys.stderr)
    sys.exit(2)


def _say(step: str, msg: str = "") -> None:
    print(f"[{step}] {msg}", flush=True)


def main() -> None:
    tag = uuid.uuid4().hex[:10]
    project_id, report_id = f"prj_inj7_{tag}", f"rpt_inj7_{tag}"

    conn = psycopg2.connect(DSN)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO fin_project (project_id,user_id,tier,initial_capital) "
                    "VALUES (%s,%s,'play',10000)", (project_id, "u-inj7"))
        cur.execute("INSERT INTO fin_report (report_id,project_id,trade_date,market,"
                    "valuation_as_of,status) VALUES (%s,%s,'2026-09-30','CN_A',now(),'validated')",
                    (report_id, project_id))
        cur.execute("INSERT INTO fin_report_fact (report_id,metric_key,value,unit,source_ref,"
                    "computed_by) VALUES (%s,'nav',1.0,'ratio','fin_valuation:2026-09-30','inj')",
                    (report_id,))
    conn.commit()

    recv = FakeReceiver(hang_s=3.0).start()
    _say("1", f"假接收端已起：{recv.url}   （/deliver_then_hang：先收下、记账，再挂起 3s）")

    target = {"url": f"{recv.url}/deliver_then_hang", "status_url": f"{recv.url}/status",
              "timeout": 0.5}
    key = P.receipt_id_for(report_id, "webhook")

    out = P.submit(conn, report_id, "webhook", target=target, html="<html>报告正文</html>")
    _say("2", f"发布（客户端超时 0.5s）→ status={out['status']}  detail={out['detail']}")
    assert out["status"] == P.UNKNOWN, out

    with recv.store.lock:
        recs = recv.store.by_receipt.get(key, [])
    _say("3", f"接收端日志：其实**已收到** {len(recs)} 份，bytes={recs[0]['nbytes'] if recs else 0}"
              f"（内容已发布，只是回执超时 —— 所以不能盲目重发）")
    assert recs, "接收端必须真收到内容"

    try:
        P.submit(conn, report_id, "webhook", target=target, html="<html>报告正文</html>")
        raise SystemExit("❌ 期望被拒，实际放行了")
    except P.PublishBlocked as exc:
        _say("4", f"不带重发标记再发 → 被拒 [{exc.code}]：{exc.reason}")

    got = P.get(conn, key, resolve=True)
    _say("5", f"核实（查外部状态 {recv.url}/status）→ status={got['status']} "
              f"resolved_at={got['resolved_at']}  probe={got['probe']}")
    assert got["status"] == P.SUCCESS and got["probe"] == P.SUCCESS, got

    recv.stop()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM fin_publish_receipt WHERE report_id=%s", (report_id,))
        cur.execute("DELETE FROM fin_report_fact WHERE report_id=%s", (report_id,))
        cur.execute("DELETE FROM fin_report WHERE report_id=%s", (report_id,))
        cur.execute("DELETE FROM fin_project WHERE project_id=%s", (project_id,))
    conn.commit()
    conn.close()
    print("\n故障注入第 7 项：**通过** —— 超时判 UNKNOWN、拒重发、核实后落 SUCCESS；临时数据已清理。")


if __name__ == "__main__":
    main()
