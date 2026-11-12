# -*- coding: utf-8 -*-
"""R7 · 影子层：**真库用例**（`TEST_DATABASE_URL` 未设时整体 skip）。

    TEST_DATABASE_URL=postgresql://hunter:hunter@127.0.0.1:5598/r7_test \
      cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_evolution_shadow.py -q

盯住的是这一轮的出口标准：

  · **幂等**：同一 `(validation_id, arm, trade_date, point, symbol)` 重复记账 → 行数不变；
  · **两臂同行情时间戳**：同一 `(date, point, symbol)` 两臂 `quote_as_of` **完全相同**；
  · **样本口径**：只有一臂出决策的交易日**不计入**可比样本（红线 11）；
  · **窗口未到不得通过**：窗口未结束 → 任何路径都写不出 `passed`；
  · **`inconclusive` 是合法终局**：窗口结束但样本不足 → 追 `inconclusive` 事件；
  · **估值可复算**：`valuation_ref` 里 `total_assets = 可用 + 冻结 + Σ(股数×价)`；
  · **两臂同初始资金**：t=0 两臂状态相同。

⚠️ 清理 `fin_evolution_event` 必须先关触发器（触发器连属主都拒）。
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date, timedelta
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

os.environ.setdefault("FIN_EVOLUTION_MODE", "observe")

psycopg2 = pytest.importorskip("psycopg2")
import psycopg2.extras  # noqa: E402

from app.services.fin import evolution as E  # noqa: E402

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="需要 TEST_DATABASE_URL 指向一个已跑过 0044 迁移的 postgres")


def _shadow_ready() -> bool:
    if not TEST_DATABASE_URL:
        return False
    try:
        conn = psycopg2.connect(TEST_DATABASE_URL)
    except Exception:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT data_type FROM information_schema.columns WHERE "
                "table_name='fin_evolution_shadow_event' AND column_name='position_ref'")
            row = cur.fetchone()
            return row is not None and row[0] == "jsonb"
    finally:
        conn.close()


if not _shadow_ready():
    pytestmark = pytest.mark.skip(reason="库里没有 fin_evolution_shadow_event(jsonb) —— 先跑 0044 迁移")
else:
    E.DATABASE_URL = TEST_DATABASE_URL


def _conn():
    return psycopg2.connect(TEST_DATABASE_URL)


# ── 夹具：一个项目 + 一份**直接写的**提案与计划 + 一段假交易日历 ────────────
#
# 直接写提案/计划（而不是走 `propose`）：本文件的靶子是**影子层**；提案闸门由
# `test_fin_evolution*.py` 覆盖。计划里的窗口 / 样本 / 阈值由每个用例按需给。

_MARKET = "CN_A"


def _seed_calendar(cur, start: date, n: int):
    """从 start 起 n 个「交易日」（这里的日历只服务本用例的窗口计算）。"""
    day = start
    while n > 0:
        cur.execute(
            "INSERT INTO fin_market_calendar (market, trade_date, is_trading, sessions, calendar_source)"
            " VALUES (%s, %s, true, %s, 'r7-shadow-test')"
            " ON CONFLICT (market, trade_date) DO UPDATE SET is_trading = true",
            (_MARKET, day, psycopg2.extras.Json([{"open": "09:30", "close": "15:00"}])))
        day += timedelta(days=1)
        n -= 1
    return day


@pytest.fixture()
def env():
    tag = uuid.uuid4().hex[:12]
    pid = f"prj_r7_{tag}"
    eid = f"evp_r7_{tag}"
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO fin_project (project_id, user_id, tier, initial_capital) "
                        "VALUES (%s, %s, 'play', 100000)", (pid, "u-" + uuid.uuid4().hex))
            cur.execute(
                "INSERT INTO fin_param (project_id, stop_loss_pct, take_profit_pct, hold_days_max, "
                "  max_positions, max_position_pct, min_order_amount, daily_max_new, daily_max_orders, "
                "  daily_loss_halt_pct, account_drawdown_halt_pct, strategies) "
                "VALUES (%s, -0.08, 0.20, 5, 5, 0.20, 1000, 5, 10, -0.03, -0.08, %s)",
                (pid, psycopg2.extras.Json([
                    {"key": "vb", "name": "量价突破", "version": "v1",
                     "params": {"vol_mult": 2.0, "confirm_days": 1}}])))
            cur.execute(
                "INSERT INTO fin_evolution_proposal (proposal_id, project_id, evidence_refs, "
                "  base_config_hash, candidate_config_hash, param_diff, regime_tags, rule_version, "
                "  proposal_algo_version, target, direction, status, rationale, created_by) "
                "VALUES (%s, %s, %s, 'b', 'c', %s, %s, 'r7', 'evolution-algo-v1', 'strategy', "
                "  'tighten', 'validating', 'r7 shadow test', 'test')",
                (eid, pid, ["exp_dummy"], psycopg2.extras.Json([{"field": "stop_loss_pct",
                                                                 "old": -0.08, "new": -0.05}]),
                 ["bull"]))
            # 2031 年那段假日历（远离其它用例的日期池，避免交叉污染）
            _seed_calendar(cur, date(2031, 1, 1), 40)
        conn.commit()
    finally:
        conn.close()
    yield {"pid": pid, "eid": eid}
    # 清理（先关触发器才能删 event）
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE fin_evolution_event DISABLE TRIGGER fin_evolution_event_immutable")
            cur.execute("ALTER TABLE fin_evolution_plan DISABLE TRIGGER fin_evolution_plan_immutable")
            cur.execute("DELETE FROM fin_evolution_shadow_event WHERE validation_id = %s", (eid,))
            cur.execute("DELETE FROM fin_evolution_plan WHERE proposal_id = %s", (eid,))
            cur.execute("DELETE FROM fin_evolution_event WHERE proposal_id = %s", (eid,))
            cur.execute("ALTER TABLE fin_evolution_event ENABLE TRIGGER fin_evolution_event_immutable")
            cur.execute("ALTER TABLE fin_evolution_plan ENABLE TRIGGER fin_evolution_plan_immutable")
            cur.execute("DELETE FROM fin_evolution_proposal WHERE proposal_id = %s", (eid,))
            cur.execute("DELETE FROM fin_param WHERE project_id = %s", (pid,))
            cur.execute("DELETE FROM fin_project WHERE project_id = %s", (pid,))
            cur.execute("DELETE FROM fin_market_calendar WHERE calendar_source = 'r7-shadow-test'")
        conn.commit()
    finally:
        conn.close()


def _put_plan(env, *, window_days=20, min_sample=20, pass_line=0.01, fail_line=-0.01):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            plan = {**E.DEFAULT_PLAN, "window_days": window_days,
                    "min_comparable_sample": min_sample, "pass_line": pass_line,
                    "fail_line": fail_line}
            cur.execute(
                "INSERT INTO fin_evolution_plan (plan_id, proposal_id, metric, window_days, "
                "  min_comparable_sample, cost_model, slippage_model, data_source_version, "
                "  pass_line, fail_line, plan_hash) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (proposal_id) DO NOTHING",
                (f"evpl_r7_{uuid.uuid4().hex[:12]}", env["eid"], plan["metric"],
                 window_days, min_sample, plan["cost_model"], plan["slippage_model"],
                 plan["data_source_version"], pass_line, fail_line, E.plan_hash(plan)))
        conn.commit()
    finally:
        conn.close()


def _rec(env, *, arm, day, point="CN_A-1000", symbol="600519", quote_as_of,
         filled=True, cash="100000", qty=100, price="10.00", fee="5.0", signal=True,
         realized=None):
    sig = None
    if signal:
        sig = {"side": "buy", "qty": qty, "price_type": "market",
               "fill": ({"price": price, "amount": str(qty * float(price)), "basis": "x"}
                        if filled else None),
               "realized_pnl": realized}
    return {
        "validation_id": env["eid"], "arm": arm, "trade_date": day, "point": point,
        "symbol": symbol, "quote_as_of": quote_as_of, "signal": sig,
        "filled": filled, "reject_reason": None if filled else "未成交",
        "fee": fee if filled else None, "slippage": None,
        "position": {"cash_available": cash, "cash_frozen": "0",
                     "positions": [{"code": symbol, "qty": qty, "avg_cost": price,
                                    "sellable_qty": qty, "price": price}]},
        "valuation": {"cash_available": cash, "cash_frozen": "0",
                      "market_value": str(qty * float(price)),
                      "total_assets": str(float(cash) + qty * float(price)),
                      "nav": "1.0", "initial_capital": "100000",
                      "as_of_price": price, "as_of": quote_as_of},
    }


QA = "2031-01-05T02:00:00+00:00"
QA2 = "2031-01-06T02:00:00+00:00"


def _count(env):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fin_evolution_shadow_event WHERE validation_id=%s",
                        (env["eid"],))
            return cur.fetchone()[0]
    finally:
        conn.close()


def _events(env, kind=None):
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            sql = ("SELECT kind FROM fin_evolution_event WHERE proposal_id=%s "
                   + ("AND kind=%s " if kind else "") + "ORDER BY created_at, event_id")
            cur.execute(sql, (env["eid"], kind) if kind else (env["eid"],))
            return [r["kind"] for r in cur.fetchall()]
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════════
# 幂等 + 两臂同时间戳
# ════════════════════════════════════════════════════════════════════════

def test_record_is_idempotent(env):
    recs = [_rec(env, arm="incumbent", day="2031-01-05", quote_as_of=QA),
            _rec(env, arm="candidate", day="2031-01-05", quote_as_of=QA)]
    first = E.record_shadow_events(records=recs)
    assert first["written"] == 2 and first["skipped"] == 0
    n1 = _count(env)
    # 同一批再跑一遍（重试 / 补跑）→ 行数不变
    second = E.record_shadow_events(records=recs)
    assert second["written"] == 0 and second["skipped"] == 2
    assert _count(env) == n1 == 2


def test_two_arms_share_quote_as_of(env):
    E.record_shadow_events(records=[
        _rec(env, arm="incumbent", day="2031-01-05", quote_as_of=QA),
        _rec(env, arm="candidate", day="2031-01-05", quote_as_of=QA)])
    rows = E.shadow_events(validation_id=env["eid"])
    per_arm = {r["arm"]: r["quote_as_of"] for r in rows}
    assert per_arm["incumbent"] == per_arm["candidate"] == QA


# ════════════════════════════════════════════════════════════════════════
# 样本口径：只有一臂出决策 → 不计入可比样本（红线 11）
# ════════════════════════════════════════════════════════════════════════

def test_only_candidate_trading_day_is_not_a_sample(env):
    # 只有候选臂出了决策（现行臂 signal=null）→ 可比样本 0
    E.record_shadow_events(records=[
        _rec(env, arm="incumbent", day="2031-01-05", quote_as_of=QA, filled=False, signal=False),
        _rec(env, arm="candidate", day="2031-01-05", quote_as_of=QA)])
    assert E.shadow_metrics(validation_id=env["eid"])["comparable_samples"] == 0
    # 现行臂也有了决策 → 记 1 个可比样本
    E.record_shadow_events(records=[
        _rec(env, arm="incumbent", day="2031-01-06", quote_as_of=QA2),
        _rec(env, arm="candidate", day="2031-01-06", quote_as_of=QA2)])
    assert E.shadow_metrics(validation_id=env["eid"])["comparable_samples"] == 1


# ════════════════════════════════════════════════════════════════════════
# 估值可复算 + 两臂同初始资金
# ════════════════════════════════════════════════════════════════════════

def test_valuation_ref_is_recomputable(env):
    _put_plan(env)
    E.record_shadow_events(records=[_rec(env, arm="incumbent", day="2031-01-05",
                                         quote_as_of=QA, cash="98995.0")])
    row = E.shadow_events(validation_id=env["eid"])[0]
    v = row["valuation_ref"]
    from decimal import Decimal
    mv = sum(Decimal(str(p["price"])) * int(p["qty"]) for p in row["position_ref"]["positions"])
    total = Decimal(v["cash_available"]) + Decimal(v["cash_frozen"]) + mv
    assert Decimal(v["total_assets"]) == total == Decimal("99995.0")


def test_shadow_prepare_gives_both_arms_same_initial_state(env):
    _put_plan(env)
    prep = E.shadow_prepare(proposal_id=env["eid"], market=_MARKET)
    assert prep["base_config"]["stop_loss_pct"] == -0.08
    assert prep["candidate_config"]["stop_loss_pct"] == -0.05     # base + 服务端 diff
    assert prep["states"]["incumbent"] == prep["states"]["candidate"]  # 同初始现金 / 空仓
    assert prep["states"]["incumbent"]["cash_available"] == 100000


# ════════════════════════════════════════════════════════════════════════
# 窗口未到不得通过 + inconclusive 终局
# ════════════════════════════════════════════════════════════════════════

def test_start_validation_records_window_start_and_is_idempotent(env):
    r1 = E.start_validation(proposal_id=env["eid"], market=_MARKET, trade_date="2031-01-05")
    assert r1["started"] is True
    r2 = E.start_validation(proposal_id=env["eid"], market=_MARKET, trade_date="2031-01-06")
    assert r2["started"] is False and r2["reason"] == "already_validating"
    assert _events(env, "validating").count("validating") == 1


def test_window_not_ended_never_passes(env):
    _put_plan(env, window_days=20, min_sample=1, pass_line=0.01)
    E.start_validation(proposal_id=env["eid"], market=_MARKET, trade_date="2031-01-05")
    # 窗口起点 2031-01-05，跑到 2031-01-08（3 个交易日 < 20）——即使两臂都有样本也不得通过
    for d, qa in (("2031-01-06", QA), ("2031-01-07", QA), ("2031-01-08", QA)):
        E.record_shadow_events(records=[
            _rec(env, arm="incumbent", day=d, quote_as_of=qa, cash="99000"),
            _rec(env, arm="candidate", day=d, quote_as_of=qa, cash="100000")])
    out = E.evaluate_validation(proposal_id=env["eid"], market=_MARKET, trade_date="2031-01-08")
    assert out["verdict"] is None                       # 窗口未结束 → 不判定、不写事件
    assert out["appended"] is False
    assert "passed" not in _events(env)                 # 而 passed 事件一个都没有


def test_inconclusive_when_window_ended_but_samples_insufficient(env):
    _put_plan(env, window_days=5, min_sample=20, pass_line=0.01)
    E.start_validation(proposal_id=env["eid"], market=_MARKET, trade_date="2031-01-05")
    # 只记 3 个可比样本（< 20）→ 窗口已过（跑到 2031-01-20，≥5 个交易日）→ inconclusive
    for i, d in enumerate(("2031-01-06", "2031-01-07", "2031-01-08")):
        E.record_shadow_events(records=[
            _rec(env, arm="incumbent", day=d, quote_as_of=QA, cash="99000"),
            _rec(env, arm="candidate", day=d, quote_as_of=QA, cash="100000")])
    out = E.evaluate_validation(proposal_id=env["eid"], market=_MARKET, trade_date="2031-01-20")
    assert out["window"]["ended"] is True
    assert out["verdict"] == "inconclusive"
    assert out["appended"] is True
    assert "inconclusive" in _events(env)
    assert "passed" not in _events(env)


def test_failed_when_delta_touches_fail_line(env):
    """触及失败线 → `failed`（提前失败与窗口无关）。"""
    _put_plan(env, window_days=20, min_sample=1, pass_line=0.01, fail_line=-0.01)
    E.start_validation(proposal_id=env["eid"], market=_MARKET, trade_date="2031-01-05")
    # 现行臂净值 100000（净 0），候选臂 90000（净 -10%）→ delta -10% < -1%
    E.record_shadow_events(records=[
        _rec(env, arm="incumbent", day="2031-01-06", quote_as_of=QA, cash="100000"),
        _rec(env, arm="candidate", day="2031-01-06", quote_as_of=QA, cash="90000")])
    out = E.evaluate_validation(proposal_id=env["eid"], market=_MARKET, trade_date="2031-01-06")
    assert out["verdict"] == "failed"
    assert "failed" in _events(env)


# ════════════════════════════════════════════════════════════════════════
# 指标：regime 与亏损尾部单列
# ════════════════════════════════════════════════════════════════════════

def test_metrics_list_regime_and_loss_tail(env):
    for d, cash in (("2031-01-06", "100000"), ("2031-01-07", "95000"),
                    ("2031-01-08", "93000")):
        E.record_shadow_events(records=[
            _rec(env, arm="incumbent", day=d, quote_as_of=QA, cash="100000"),
            _rec(env, arm="candidate", day=d, quote_as_of=QA, cash=cash)])
    m = E.shadow_metrics(validation_id=env["eid"])
    assert m["comparable_samples"] == 3
    assert set(m["by_regime"]) == {"unknown"}                 # 无基准行情源 → 单列 unknown
    assert m["loss_tail"]["candidate"]                        # 亏损尾部单列
    assert m["arms"]["candidate"]["max_drawdown"] is not None
    assert m["arms"]["candidate"]["realized_net_pnl_per_trade"]["auxiliary"] is True
