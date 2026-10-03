#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""N5 · 三市场端到端回归（开户 → 下单 → 成交 → 记账 → 估值 → 报告）· 真跑本机这套容器。

这是 N5 任务书 §一.4 的机器化形式：

- 三个市场各跑一遍链路；**港美股真的要成交并记账**（任务书出口标准 ⑦ · 拍板 2026-10-03 唯一验收点）；
- 每一步都打**真实请求 / 真实响应**，并落一份 JSON 证据；
- A 股在非交易时段被真实风控拒绝时**如实记下拒绝回执**（不伪造、不绕过）。

用法（本机）::

    python3 scripts/n5_multimarket_e2e.py                     # 用默认演示项目
    N5_PROJECT_ID=prj_xxx N5_EMAIL=... N5_PASSWORD=... python3 scripts/n5_multimarket_e2e.py

依赖：只有标准库。paper / api 的地址由环境变量给（默认本机 dev 端口）。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

API = os.environ.get("N5_API", "http://127.0.0.1:8102")           # apps/api
PAPER = os.environ.get("N5_PAPER", "http://127.0.0.1:8299")       # apps/paper
INTERNAL_KEY = os.environ.get("N5_INTERNAL_KEY", "m4-verify-key")
EMAIL = os.environ.get("N5_EMAIL", "n5demo@example.com")
PASSWORD = os.environ.get("N5_PASSWORD", "n5demo123")
PROJECT_ID = os.environ.get("N5_PROJECT_ID", "")
TRADE_DATE = os.environ.get("N5_TRADE_DATE", "")                   # 空 = 按「现在」的上海日期

SH = timezone(timedelta(hours=8))
# 每市场一个真实标的（与 N4 的示例一致：A 股 000001 / 港股 00700 / 美股 AAPL）。
ORDERS = [
    ("CN_A", "000001", 100, "11.60"),
    ("HK",   "00700",  100, "430.00"),
    ("US",   "AAPL",     1, "340.00"),
]

out: dict = {"at_utc": None, "at_shanghai": None, "steps": []}


def now() -> tuple[str, str]:
    u = datetime.now(timezone.utc)
    return u.isoformat(), u.astimezone(SH).isoformat()


def call(method: str, url: str, body=None, headers=None, label: str = "") -> dict:
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data, timeout=30) as r:
            payload = json.loads(r.read().decode() or "{}")
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            payload = json.loads(e.read().decode() or "{}")
        except Exception:
            payload = {"raw": "<non-json>"}
    out["steps"].append({"label": label, "method": method, "url": url,
                         "body": body, "status": status, "response": payload})
    return {"status": status, "body": payload}


def main() -> int:
    out["at_utc"], out["at_shanghai"] = now()

    # ── ① 登录（拿 JWT；/api/v1/fin/* 不是免登录前缀）────────────────────
    login = call("POST", f"{API}/api/auth/login",
                 {"email": EMAIL, "password": PASSWORD}, label="① 登录")
    if login["status"] != 200:
        print("登录失败：", login, file=sys.stderr)
        return 1
    tok = login["body"]["access_token"]
    auth = {"Authorization": f"Bearer {tok}"}

    # ── ② 市场状态（真实日历；含 A 股休市那天）───────────────────────────
    call("GET", f"{API}/api/v1/fin/markets", None, auth, "② 市场状态（现在）")
    call("GET", f"{API}/api/v1/fin/markets?trade_date=2026-10-06", None, auth,
         "② 市场状态（2026-10-06 · A 股国庆休市对照）")

    # ── ③ 开户（没有项目时用当前项目；本机演示项目已由 setup 建好）─────────
    pid = PROJECT_ID
    if not pid:
        cur = call("GET", f"{API}/api/v1/fin/projects/current", None, auth, "③ 当前项目")
        pid = ((cur["body"].get("project") or {}).get("project_id")) or ""
        if not pid:
            print("没有进行中的项目，请先开户或设 N5_PROJECT_ID", file=sys.stderr)
            return 1
    out["project_id"] = pid
    trade_date = TRADE_DATE or datetime.now(SH).date().isoformat()
    out["trade_date"] = trade_date

    # ── ④ 三个市场各自入金（子账户本金，幂等）────────────────────────────
    for m, *_ in ORDERS:
        call("POST", f"{PAPER}/api/v1/projects/{pid}/funding?market={m}", {},
             {"X-Hunter-Internal-Key": INTERNAL_KEY}, f"④ 入金 {m}")

    # ── ⑤ 三个市场各下一笔限价单（港美股必须真成交）──────────────────────
    for m, code, qty, px in ORDERS:
        call("POST", f"{PAPER}/api/v1/orders",
             {"project_id": pid, "code": code, "side": "buy", "qty": qty,
              "price_type": "limit", "limit_price": px, "actor": "n5-e2e"},
             {"X-Hunter-Internal-Key": INTERNAL_KEY}, f"⑤ 下单 {m} {code}")

    # ── ⑥ 三市场各出一行收盘估值（报告的事实来源）────────────────────────
    # ⚠️ `fin_valuation` 是**只追加**的：同一 `(project, market, as_of)` 第二次 POST 会原样返回
    # 已有行（不重算）。所以每次跑必须用一个**新的 as_of**，否则会拿上一轮的快照当这一轮的结果。
    as_of = os.environ.get("N5_AS_OF") or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    out["valuation_as_of"] = as_of
    for m, *_ in ORDERS:
        call("POST", f"{PAPER}/api/v1/projects/{pid}/valuation",
             {"as_of": as_of, "market": m},
             {"X-Hunter-Internal-Key": INTERNAL_KEY}, f"⑥ 估值 {m}")

    # ── ⑦ 生成报告：每市场一份 + 一份跨市场汇总 ──────────────────────────
    rep = call("POST", f"{API}/api/internal/fin/reports/generate",
               {"project_id": pid, "trade_date": trade_date},
               {"X-Hunter-Internal-Key": INTERNAL_KEY}, "⑦ 生成报告")

    # ── ⑧ 读回报告列表 + 每份的正文与事实行 ──────────────────────────────
    lst = call("GET", f"{API}/api/v1/fin/reports?project_id={pid}", None, auth, "⑧ 报告列表")
    out["report_list"] = lst["body"].get("items")
    if rep["status"] == 200:
        for r in (rep["body"].get("reports") or []) + [rep["body"].get("summary") or {}]:
            rid = r.get("report_id")
            if rid:
                call("GET", f"{API}/api/v1/fin/reports/{rid}", None, auth, f"⑧ 读报告 {r.get('market')}")

    out["at_utc_end"], out["at_shanghai_end"] = now()
    return 0


if __name__ == "__main__":
    rc = main()
    dest = os.environ.get("N5_EVIDENCE", "/tmp/n5-e2e-evidence.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"证据写入 {dest}（{len(out.get('steps', []))} 步）")
    # 摘要（人眼可读）
    for s in out.get("steps", []):
        r = s["response"]
        brief = ""
        if isinstance(r, dict):
            if "status" in r:
                brief = f"status={r.get('status')} market={r.get('market')} price={r.get('price')}"
                if r.get("decline_reason"):
                    brief += f" decline={r['decline_reason']}"
            elif "markets" in r:
                brief = " · ".join(f"{m['market']}={m['state_label']}" for m in r["markets"])
            elif "reports" in r:
                brief = "reports=" + ",".join(x.get("market", "?") for x in r["reports"])
        print(f"  [{s['status']:>3}] {s['label']} — {brief}")
    sys.exit(rc)
