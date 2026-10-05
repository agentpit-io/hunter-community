# -*- coding: utf-8 -*-
"""L15 · 「只对收紧」自动生效 —— 现场取证驱动（真库 + HTTP 内网入口）。

跑法（本机，一次性进程，不打运行中的容器）：

    cd apps/api
    DATABASE_URL=...l15_api TEST_DATABASE_URL=...l15_api PYTHONPATH=. \
      .venv/bin/python ../../scripts/l15_auto_apply_verify.py

造两条提案（各带 `passed` 事件 + 1 个可比样本），分别打内网自动入口
`POST /internal/fin/evolution/{id}/auto-apply`，打印**原始返回体**与库里的留痕：

  · ① 收紧（止损 -0.08 → -0.05）→ 自动生效成功，参数真的变了；
  · ② 放宽（止损 -0.08 → -0.12）→ 被拒（不发请求改配置），留痕 + 状态仍等人确认；
  · ③ 闸门没被绕过（没有 passed 事件）→ 自动也上不去。

本脚本**只写自造的验收项目**（前缀 `prj_l15verify_`），跑完即清。
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(_API_ROOT))

os.environ.setdefault("FIN_EVOLUTION_MODE", "observe")
os.environ.setdefault("FIN_AUTO_APPLY", "1")

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routers import fin_evolution  # noqa: E402
from app.services.fin import control as C  # noqa: E402
from app.services.fin import evolution as E  # noqa: E402
from app.services.fin import memory as memory_svc  # noqa: E402
from app.services.fin import switches as switches_svc  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL", "").strip()
if not DSN:
    raise SystemExit("需要 TEST_DATABASE_URL（指向已跑过 0055 迁移的库）")

INTERNAL_KEY = "l15-internal-key"
AUTO_ACTOR = "system:auto-apply"

E.DATABASE_URL = DSN
memory_svc.DATABASE_URL = DSN
switches_svc.DATABASE_URL = DSN
C._schema_done = True
fin_evolution._INTERNAL_KEY = INTERNAL_KEY

app = FastAPI()


@app.middleware("http")
async def fake_auth(request: Request, call_next):
    request.state.user_id = request.headers.get("x-test-user") or None
    return await call_next(request)


app.include_router(fin_evolution.router, prefix="/api")
client = TestClient(app, raise_server_exceptions=False)
IK = {"X-Hunter-Internal-Key": INTERNAL_KEY}

BASE_STRATEGY = {"key": "vb", "name": "量价突破", "version": "v1",
                 "params": {"vol_mult": 2.0, "confirm_days": 1}}
BASE_PARAM = dict(stop_loss_pct=-0.08, take_profit_pct=0.20, hold_days_max=5,
                  max_position_pct=0.20, daily_loss_halt_pct=-0.03,
                  account_drawdown_halt_pct=-0.08)


def _conn():
    return psycopg2.connect(DSN)


def _seed():
    tag = uuid.uuid4().hex[:10]
    uid = f"u-l15verify-{tag}"      # fin_project_one_active：一个用户只能有一个活跃项目
    pid = f"prj_l15verify_{tag}"
    snap = f"SNAP-l15-{tag}"
    order = f"ord_{tag}"
    trade = f"trd_{tag}"
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO fin_project (project_id, user_id, tier, initial_capital, "
                        "  market_scope) VALUES (%s, %s, 'play', 100000, 'CN_A')", (pid, uid))
            cur.execute(
                "INSERT INTO fin_param (project_id, stop_loss_pct, take_profit_pct, hold_days_max, "
                "  max_positions, max_position_pct, min_order_amount, daily_max_new, daily_max_orders, "
                "  daily_loss_halt_pct, account_drawdown_halt_pct, strategies) "
                "VALUES (%s, %s, %s, %s, 5, %s, 1000, 5, 10, %s, %s, %s)",
                (pid, BASE_PARAM["stop_loss_pct"], BASE_PARAM["take_profit_pct"],
                 BASE_PARAM["hold_days_max"], BASE_PARAM["max_position_pct"],
                 BASE_PARAM["daily_loss_halt_pct"], BASE_PARAM["account_drawdown_halt_pct"],
                 psycopg2.extras.Json([dict(BASE_STRATEGY)])))
            cur.execute("INSERT INTO fin_snapshot (snapshot_id, code, snapshot_time, source, last_price) "
                        "VALUES (%s, %s, now(), 'l15verify', 5.0)", (snap, pid))
            cur.execute("INSERT INTO fin_order (order_id, project_id, code, side, qty, price_type, "
                        "  status, filled_qty, source, actor, market) "
                        "VALUES (%s, %s, '601398', 'buy', 100, 'limit', 'filled', 100, 'ai', 'system', 'CN_A')",
                        (order, pid))
            cur.execute("INSERT INTO fin_trade (trade_id, order_id, project_id, code, side, qty, price, "
                        "  amount, snapshot_id, source, fee_model_version, traded_at, market, currency) "
                        "VALUES (%s, %s, %s, '601398', 'buy', 100, 5.0, 500, %s, 'ai', "
                        "  'fee-model-v1', now(), 'CN_A', 'CNY')", (trade, order, pid, snap))
        conn.commit()
        exp = memory_svc.append_evidence(
            caller="internal", project_id=pid, kind="verified",
            statement="甲股缩量整理后追高的回撤概率上升", method="回测", sample_size=30,
            polarity="support", regime_tags=["bull"], symbols=["CN_A:601398"],
            as_of="2026-09-01T15:00:00+08:00",
            evidence=[{"evidence_kind": "snapshot", "ref_id": snap}])
        review = memory_svc.query(project_id=pid, caller="internal", freeze=True, purpose="review")
    finally:
        conn.close()
    return {"uid": uid, "pid": pid, "snap": snap, "trade": trade,
            "exp": exp["experience_id"], "review_snap": review["memory_snapshot_id"]}


def _purge(pid):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE fin_evolution_plan DISABLE TRIGGER fin_evolution_plan_immutable")
            cur.execute("ALTER TABLE fin_evolution_event DISABLE TRIGGER fin_evolution_event_immutable")
            cur.execute("DELETE FROM fin_evolution_plan WHERE proposal_id IN "
                        "(SELECT proposal_id FROM fin_evolution_proposal WHERE project_id = %s)", (pid,))
            cur.execute("DELETE FROM fin_evolution_event WHERE proposal_id IN "
                        "(SELECT proposal_id FROM fin_evolution_proposal WHERE project_id = %s)"
                        " OR payload->>'project_id' = %s", (pid, pid))
            cur.execute("DELETE FROM fin_evolution_shadow_event WHERE validation_id IN "
                        "(SELECT proposal_id FROM fin_evolution_proposal WHERE project_id = %s)", (pid,))
            cur.execute("DELETE FROM fin_evolution_reinject_task WHERE project_id = %s", (pid,))
            cur.execute("DELETE FROM fin_evolution_proposal WHERE project_id = %s", (pid,))
            cur.execute("ALTER TABLE fin_evolution_plan ENABLE TRIGGER fin_evolution_plan_immutable")
            cur.execute("ALTER TABLE fin_evolution_event ENABLE TRIGGER fin_evolution_event_immutable")
            cur.execute("DELETE FROM fin_experience_evidence WHERE experience_id IN "
                        "(SELECT experience_id FROM fin_experience WHERE project_id = %s)", (pid,))
            cur.execute("DELETE FROM fin_memory_snapshot WHERE project_id = %s", (pid,))
            cur.execute("DELETE FROM fin_experience WHERE project_id = %s", (pid,))
            cur.execute("DELETE FROM fin_param_change_log WHERE project_id = %s", (pid,))
            cur.execute("DELETE FROM fin_param WHERE project_id = %s", (pid,))
            cur.execute("DELETE FROM fin_trade WHERE project_id = %s", (pid,))
            cur.execute("DELETE FROM fin_order WHERE project_id = %s", (pid,))
            cur.execute("DELETE FROM fin_valuation WHERE project_id = %s", (pid,))
            cur.execute("DELETE FROM fin_snapshot WHERE code = %s", (pid,))
            cur.execute("DELETE FROM fin_memory_switch_log WHERE project_id = %s", (pid,))
            cur.execute("DELETE FROM fin_memory_switch WHERE project_id = %s", (pid,))
            cur.execute("DELETE FROM fin_project WHERE project_id = %s", (pid,))
        conn.commit()
    finally:
        conn.close()


def _base_config(pid):
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            return E.read_config(cur, pid)
    finally:
        conn.close()


def _propose(env, mutate):
    base = _base_config(env["pid"])
    cand = mutate(base)
    return E.propose(
        project_id=env["pid"], evidence_refs=[env["exp"]],
        evidence_snapshot_id=env["review_snap"], candidate_config=cand,
        param_diff=E.config_diff(base, cand), target="strategy", regime_tags=["bull"],
        rationale="L15 取证", created_by="l15verify",
        plan_overrides={"min_comparable_sample": 1})


def _pass_it(proposal_id):
    return E.append_event(proposal_id=proposal_id, kind="passed", payload={"note": "L15 取证"})


def _seed_shadow(proposal_id, n=1):
    recs = []
    for i in range(n):
        d = f"2026-10-{10 + (i % 18):02d}"
        for arm in ("incumbent", "candidate"):
            recs.append({
                "validation_id": proposal_id, "arm": arm, "trade_date": d,
                "point": "CN_A-0930", "symbol": f"60139{i % 10}",
                "quote_as_of": "2026-10-09T08:00:00+00:00",
                "signal": {"side": "buy", "qty": 100, "note": "t"}, "filled": False,
                "reject_reason": "风控拒绝（测试）", "fee": None, "slippage": None,
                "position": None, "valuation": None})
    return E.record_shadow_events(records=recs)


def _param(pid):
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM fin_param WHERE project_id = %s", (pid,))
            return dict(cur.fetchone())
    finally:
        conn.close()


def _events(pid, kind=None):
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT e.* FROM fin_evolution_event e WHERE (e.proposal_id IN "
                "(SELECT proposal_id FROM fin_evolution_proposal WHERE project_id=%s) "
                " OR e.payload->>'project_id'=%s) "
                + ("AND e.kind=%s " if kind else "")
                + "ORDER BY e.created_at, event_id",
                ((pid, pid, kind) if kind else (pid, pid)))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _status_of(proposal_id):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM fin_evolution_proposal WHERE proposal_id=%s", (proposal_id,))
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def _auto(proposal_id):
    return client.post(f"/api/internal/fin/evolution/{proposal_id}/auto-apply",
                       json={}, headers=IK)


def hr(t):
    print("\n" + "═" * 76)
    print(t)
    print("═" * 76)


def main():
    # 每个场景一个**全新项目**：① 会改掉 base，②③ 若复用同一个项目，
    # 「单次变化上限 0.05」的白名单闸门会先拦下（那是另一个闸门，不是「只收紧」这条）。
    pids = []
    try:
        # ① 收紧：止损 -0.08 → -0.05
        hr("① 收紧（止损 -0.08 → -0.05）+ 验证通过 + 开关开 → 自动生效")
        env = _seed(); pids.append(env["pid"]); pid = env["pid"]
        print(f"验收项目 {pid} · 初始 stop_loss_pct = {_param(pid)['stop_loss_pct']}")
        prop = _propose(env, lambda b: {**b, "stop_loss_pct": -0.05})
        pid1 = prop["proposal_id"]
        print(f"提案 {pid1} · direction={prop['direction']} · target={prop['target']}")
        _pass_it(pid1)
        _seed_shadow(pid1, 1)
        r = _auto(pid1)
        print("--- HTTP", r.status_code, "返回体 ---")
        print(json.dumps(r.json(), ensure_ascii=False, indent=2))
        print("--- 库里的效果 ---")
        print("stop_loss_pct now =", _param(pid)["stop_loss_pct"])
        ap = [e for e in _events(pid, "applied")]
        print("applied 事件 actor =", ap[0]["actor"],
              "| payload.auto =", ap[0]["payload"].get("auto"),
              "| payload.applied_by =", ap[0]["payload"].get("applied_by"))

        # ② 放宽：止损 -0.08 → -0.12（单次 0.04 < 白名单 0.05，能过提案闸门）
        hr("② 放宽（止损 -0.08 → -0.12）+ 验证通过 + 开关开 → 被拒（不改配置）")
        env2 = _seed(); pids.append(env2["pid"]); pid2p = env2["pid"]
        print(f"验收项目 {pid2p} · 初始 stop_loss_pct = {_param(pid2p)['stop_loss_pct']}")
        prop2 = _propose(env2, lambda b: {**b, "stop_loss_pct": -0.12})
        pid2 = prop2["proposal_id"]
        print(f"提案 {pid2} · direction={prop2['direction']} · target={prop2['target']}")
        _pass_it(pid2)
        _seed_shadow(pid2, 1)
        r2 = _auto(pid2)
        print("--- HTTP", r2.status_code, "返回体 ---")
        print(json.dumps(r2.json(), ensure_ascii=False, indent=2))
        print("--- 库里的效果 ---")
        print("stop_loss_pct now =", _param(pid2p)["stop_loss_pct"], "（放宽那条没生效）")
        skips = [e for e in _events(pid2p, "rejected_by_gate")
                 if (e.get("payload") or {}).get("stage") == "auto-apply"]
        print("auto-apply 留痕条数 =", len(skips),
              "| actor =", skips[0]["actor"] if skips else None,
              "| direction =", skips[0]["payload"].get("direction") if skips else None)
        print("提案状态 =", _status_of(pid2), "（被自动拒绝 ≠ 被驳回，仍等人确认）")

        # ③ 闸门没被绕过：没有 passed 事件
        hr("③ 闸门没被绕过（没有 passed 事件）+ 开关开 → 自动也上不去")
        env3 = _seed(); pids.append(env3["pid"]); pid3p = env3["pid"]
        prop3 = _propose(env3, lambda b: {**b, "stop_loss_pct": -0.05})
        pid3 = prop3["proposal_id"]
        _seed_shadow(pid3, 1)          # 故意不写 passed
        r3 = _auto(pid3)
        print("--- HTTP", r3.status_code, "返回体 ---")
        print(json.dumps(r3.json(), ensure_ascii=False, indent=2))
        print("stop_loss_pct now =", _param(pid3p)["stop_loss_pct"], "（没生效）")
    finally:
        for p in pids:
            _purge(p)
        print(f"\n已清理验收项目 {pids}")


if __name__ == "__main__":
    main()
