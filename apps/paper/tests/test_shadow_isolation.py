"""R7 · 影子路径 · **零订单出口**（红线 12）—— 需要账本库（`PAPER_TEST_DSN`）。

    PAPER_TEST_DSN=postgresql://hunter:hunter@127.0.0.1:5598/r7_test \
      cd apps/paper && python -m pytest tests/test_shadow_isolation.py -q

盯住的是这一轮的硬验收：

  · **接口隔离**：影子路径的依赖里只有 `DecisionRecorder`；把执行端
    （`EngineOrderExecutor.place_order` 与 `matching.engine.execute`）换成**计数替身**后
    跑一次完整的候选臂运行，断言**零调用**；
  · **影子成交不写账本**：跑完 `fin_trade` / `fin_order` **零新增**；影子事件只在 recorder 里；
  · **同一行情快照**（红线 11）：两臂共用同一张 `fin_snapshot`（同一个 `snapshot_id` /
    `quote_as_of`）；
  · **复用同一套口径**：费率来自 `fin_fee_model`、撮合来自 `matching.pricing.match`
    （影子成交价与直接调 `match(...)` 逐位相同）；
  · **估值可复算**：`valuation_ref.total_assets == 可用 + 冻结 + Σ(股数 × 价)`；
  · **行情缺口**：没有可用快照时两臂都 `filled=false`（`inconclusive` 的依据），仍各记一行。
"""
from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal

import pytest

os.environ.setdefault("HUNTER_INTERNAL_KEY", "test-internal-key")
os.environ.setdefault("PAPER_MODE", "PAPER")

pytestmark = pytest.mark.db

psycopg2 = pytest.importorskip("psycopg2")
import psycopg2.extras  # noqa: E402

from helpers import install_quote_source  # noqa: E402

from app import shadow as S  # noqa: E402
from app import snapshot as snap_mod  # noqa: E402
from app import ledger  # noqa: E402
from app.matching.model import load_execution_model  # noqa: E402
from app.matching.pricing import OrderSpec, match  # noqa: E402
from app.market_time import market_tz  # noqa: E402
from app.snapshot.source import NullQuoteSource, set_source  # noqa: E402

CODE = "600519"
DAY = "2026-10-08"          # 一个固定的 CN_A 交易日
WHEN = f"{DAY}T10:00:00+08:00"


def _seed_reference(pg, project_id):
    """标的元数据 + 当天日历。市场规则 / 费率 / 执行模型用生产行（迁移已种）。"""
    pg.execute(
        """
        INSERT INTO fin_instrument
          (code, name, exchange, board, is_st, limit_up_pct, limit_down_pct, lot_size,
           market, currency, source)
        VALUES (%s, %s, 'SH', 'main', false, 0.10, 0.10, 100, 'CN_A', 'CNY', 'r7-test')
        ON CONFLICT (code) DO UPDATE SET market = 'CN_A', currency = 'CNY', lot_size = 100,
          limit_up_pct = 0.10, limit_down_pct = 0.10
        """,
        (CODE, CODE),
    )
    pg.execute(
        """
        INSERT INTO fin_market_calendar (market, trade_date, is_trading, sessions, calendar_source)
        VALUES ('CN_A', %s, true, %s, 'r7-test')
        ON CONFLICT (market, trade_date) DO UPDATE SET is_trading = true,
          sessions = EXCLUDED.sessions, calendar_source = EXCLUDED.calendar_source
        """,
        (date.fromisoformat(DAY),
         psycopg2.extras.Json([{"open": "09:30", "close": "11:30"},
                               {"open": "13:00", "close": "15:00"}])),
    )
    pg.connection.commit()


def _req(project_id, *, arms, initial="100000"):
    return {
        "project_id": project_id, "market": "CN_A", "symbol": CODE,
        "trade_date": DAY, "point": "CN_A-1000", "initial_capital": initial, "arms": arms,
    }


def _arm(name, *, cash="100000", positions=None, qty=100, decided=True):
    return {"arm": name, "decided": decided, "side": "buy", "qty": qty,
            "price_type": "market", "limit_price": None,
            "state": {"cash_available": cash, "cash_frozen": "0", "positions": positions or []}}


# ════════════════════════════════════════════════════════════════════════
# 零订单出口（红线 12）
# ════════════════════════════════════════════════════════════════════════

def test_shadow_never_touches_the_order_executor(pg, project):
    """把执行端换成**计数替身**跑一次完整的两臂运行 → **零调用**。"""
    _seed_reference(pg, project)
    install_quote_source(when=WHEN, price="10.00", prev_close="10.00")

    calls = {"executor": 0, "engine_execute": 0}

    real_place = S.EngineOrderExecutor.place_order

    def spy_place(self, cur, req):
        calls["executor"] += 1
        return real_place(self, cur, req)

    import app.matching.engine as engine_mod
    real_execute = engine_mod.execute

    def spy_execute(cur, req, **kw):
        calls["engine_execute"] += 1
        return real_execute(cur, req, **kw)

    S.EngineOrderExecutor.place_order = spy_place
    engine_mod.execute = spy_execute
    try:
        recorder = S.CollectingRecorder()
        out = S.simulate_arms(pg, _req(project, arms=[_arm("incumbent"), _arm("candidate")]),
                              recorder=recorder)
    finally:
        S.EngineOrderExecutor.place_order = real_place
        engine_mod.execute = real_execute

    # **本轮的硬断言**：影子路径从未调用执行端。
    assert calls == {"executor": 0, "engine_execute": 0}
    assert len(recorder.events) == 2
    assert len(out["results"]) == 2


def test_shadow_writes_no_ledger_rows(pg, project):
    """跑完影子：`fin_trade` / `fin_order` **零新增**；影子事件只在内存里。"""
    _seed_reference(pg, project)
    install_quote_source(when=WHEN, price="10.00", prev_close="10.00")

    def count(table):
        pg.execute(f"SELECT count(*) AS n FROM {table} WHERE project_id = %s", (project,))
        return pg.fetchone()["n"]

    before = (count("fin_trade"), count("fin_order"))
    recorder = S.CollectingRecorder()
    S.simulate_arms(pg, _req(project, arms=[_arm("incumbent"), _arm("candidate")]),
                    recorder=recorder)
    after = (count("fin_trade"), count("fin_order"))

    assert before == after == (0, 0)     # 影子成交一笔都没进账本


# ════════════════════════════════════════════════════════════════════════
# 同一行情快照（红线 11）+ 复用撮合口径
# ════════════════════════════════════════════════════════════════════════

def test_two_arms_share_one_snapshot_and_same_quote_as_of(pg, project):
    _seed_reference(pg, project)
    install_quote_source(when=WHEN, price="10.00", prev_close="10.00")
    recorder = S.CollectingRecorder()
    out = S.simulate_arms(pg, _req(project, arms=[_arm("incumbent"), _arm("candidate")]),
                          recorder=recorder)
    q = [e["quote_as_of"] for e in recorder.events]
    assert q[0] == q[1] == out["quote_as_of"]
    assert out["snapshot_id"] is not None
    # 两臂成交价 = 直接调纯函数 match() 的结果（证明用的是同一套撮合实现）
    model = load_execution_model(pg)
    snap = {**snap_mod.get_snapshot(pg, out["snapshot_id"]), "tradable": True}
    expected = match(OrderSpec(side="buy", qty=100, price_type="market"), snap, model)
    assert expected.filled
    for e in recorder.events:
        assert e["filled"] is True
        assert e["signal"]["fill"]["price"] == format(expected.price, "f")


def test_valuation_recomputes_from_the_snapshot(pg, project):
    """估值可复算：`total_assets == 可用 + 冻结 + Σ(股数 × 价)`。"""
    _seed_reference(pg, project)
    install_quote_source(when=WHEN, price="10.00", prev_close="10.00")
    recorder = S.CollectingRecorder()
    S.simulate_arms(pg, _req(project, arms=[_arm("incumbent")]), recorder=recorder)
    ev = recorder.events[0]
    v = ev["valuation"]
    pos = ev["position"]["positions"]
    mv = sum(Decimal(p["price"]) * int(p["qty"]) for p in pos)
    total = Decimal(v["cash_available"]) + Decimal(v["cash_frozen"]) + mv
    assert Decimal(v["total_assets"]) == total
    assert Decimal(v["nav"]) == (total / Decimal("100000")).quantize(Decimal("0.000001"))


# ════════════════════════════════════════════════════════════════════════
# 行情缺口（inconclusive 的依据）
# ════════════════════════════════════════════════════════════════════════

def test_market_gap_records_both_arms_unfilled(pg, project):
    _seed_reference(pg, project)
    set_source(NullQuoteSource())        # 行情未接通 → capture 返回 None
    recorder = S.CollectingRecorder()
    out = S.simulate_arms(pg, _req(project, arms=[_arm("incumbent"), _arm("candidate")]),
                          recorder=recorder)
    assert out["gap"] is True
    assert out["quote_as_of"] is None
    assert len(recorder.events) == 2
    for e in recorder.events:
        assert e["filled"] is False
        assert "行情缺口" in e["reject_reason"]


# ════════════════════════════════════════════════════════════════════════
# 无决策的臂 → signal 为空（不计入可比样本，红线 11）
# ════════════════════════════════════════════════════════════════════════

def test_undecided_arm_has_null_signal(pg, project):
    _seed_reference(pg, project)
    install_quote_source(when=WHEN, price="10.00", prev_close="10.00")
    recorder = S.CollectingRecorder()
    S.simulate_arms(
        pg, _req(project, arms=[_arm("incumbent", decided=False, qty=0),
                                _arm("candidate")]),
        recorder=recorder)
    by_arm = {e["arm"]: e for e in recorder.events}
    assert by_arm["incumbent"]["signal"] is None
    assert by_arm["candidate"]["signal"] is not None


# ════════════════════════════════════════════════════════════════════════
# 路由（走真实 HTTP 形状）
# ════════════════════════════════════════════════════════════════════════

def test_shadow_simulate_route(pg, project):
    from fastapi.testclient import TestClient
    from app.main import app
    _seed_reference(pg, project)
    install_quote_source(when=WHEN, price="10.00", prev_close="10.00")
    client = TestClient(app)
    body = _req(project, arms=[_arm("incumbent"), _arm("candidate")])
    body["initial_capital"] = "100000"
    r = client.post("/api/v1/shadow/simulate", json=body,
                    headers={"X-Hunter-Internal-Key": "test-internal-key"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["results"]) == 2
    assert data["quote_as_of"] == data["results"][0]["quote_as_of"]
