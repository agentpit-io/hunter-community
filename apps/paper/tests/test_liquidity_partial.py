"""成交量约束（`L05` 第 1 项）+ 部分成交（第 2 项）。

两项共用一条纯函数 `matching.pricing.match(..., participation=)`（`L05` 的口径）：

- **成交量约束**：成交股数不许超过 `floor(盘口量 × 参与率)`。参与率来自
  `fin_param.liquidity_max_participation`；**没配（NULL）→ 不加约束**，与加约束之前逐字节一致。
- **部分成交**：超上限时 `part_fill=false`（默认）→ 整笔不成交、挂单；
  `part_fill=true` → 成交上限股数（`partial`）+ 剩余挂着。

**红线 11（两臂同条件）**：这条约束写在**一个纯函数**里，真实撮合（`matching.engine`）
与影子撮合（`app.shadow`）都调它、都传同一份参与率 —— 本轮任何撮合改动同时作用于两臂。
`test_two_arms_share_the_participation_cap` 是这条的证据。

前一组是纯函数（不连库），后一组要账本库。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from decimal import Decimal

import pytest

from app.matching.model import ExecutionModel
from app.matching.pricing import (
    FILLED,
    PARTIAL,
    PENDING,
    OrderSpec,
    available_volume,
    liquidity_cap,
    match,
)

MODEL_OFF = ExecutionModel(version="paper-model-v1", slippage_ticks=Decimal("1"),
                           tick_size=Decimal("0.01"), part_fill=False)
MODEL_ON = ExecutionModel(version="paper-model-l05", slippage_ticks=Decimal("1"),
                          tick_size=Decimal("0.01"), part_fill=True)


def snap(last="10.00", ask1="10.00", bid1="10.00", *, ask1_volume=None, bid1_volume=None):
    """一张快照。盘口量默认**不给**（老报价的形状 → 算不出 → 不加约束）。"""
    return {
        "snapshot_id": "SNAP-TEST", "code": "600519",
        "last_price": None if last is None else Decimal(last),
        "bid1_price": None if bid1 is None else Decimal(bid1),
        "ask1_price": None if ask1 is None else Decimal(ask1),
        "ask1_volume": ask1_volume, "bid1_volume": bid1_volume,
        "prev_close": Decimal("10.00"),
        "quality": "ok", "missing_flag": False, "tradable": True,
    }


# ════════════════════════════════════════════════════════════════════════
# 一 · 纯函数：参与率与盘口量的上限
# ════════════════════════════════════════════════════════════════════════

def test_available_volume_uses_the_matching_side():
    """买入看卖盘挂量（ask1），卖出看买盘挂量（bid1）——「此刻能接走多少」。"""
    s = snap(ask1_volume=300, bid1_volume=80)
    assert available_volume(s, "buy") == 300
    assert available_volume(s, "sell") == 80


def test_available_volume_missing_is_none_not_zero():
    """算不出（没有盘口量）→ `None`（不是 0）——`None` 不加约束，0 会一律判不成交。"""
    assert available_volume(snap(), "buy") is None
    assert available_volume(snap(ask1_volume=None), "sell") is None


def test_cap_is_floor_of_volume_times_participation():
    """上限 = floor(盘口量 × 参与率)，向下取整（买不满一股就是买不了那一股）。"""
    assert liquidity_cap(snap(ask1_volume=250), "buy", Decimal("0.2")) == 50
    assert liquidity_cap(snap(ask1_volume=333), "buy", Decimal("0.3")) == 99  # 99.9 → 99
    assert liquidity_cap(snap(ask1_volume=100), "buy", Decimal("1.0")) == 100


def test_cap_none_when_participation_unset():
    """没配参与率 → 无上限（`None`）——现有项目（列为 NULL）行为不变。"""
    assert liquidity_cap(snap(ask1_volume=250), "buy", None) is None


def test_cap_none_when_book_volume_missing():
    """没有盘口量 → 算不出上限 → `None`（**不拿最新价顶替**，红线 5）。"""
    assert liquidity_cap(snap(), "buy", Decimal("0.5")) is None


def test_cap_zero_when_book_empty():
    """盘口挂着 0 股 → 上限 0（此刻一股也接不走）。"""
    assert liquidity_cap(snap(ask1_volume=0), "buy", Decimal("0.5")) == 0


def test_no_participation_fills_full_unchanged():
    """**未配参与率 → 整笔成交**（与加约束之前逐字节一致，老用例靠这条不变）。"""
    r = match(OrderSpec("buy", 100, "limit", Decimal("10.00")), snap(ask1_volume=10), MODEL_OFF)
    assert r.outcome == FILLED and r.price == Decimal("10.00") and r.qty is None


def test_qty_within_cap_fills_full():
    """委托量 ≤ 上限 → 整笔成交。"""
    r = match(OrderSpec("buy", 100, "limit", Decimal("10.00")),
              snap(ask1_volume=250), MODEL_OFF, participation=Decimal("1.0"))
    assert r.outcome == FILLED and r.cap_reason is None


def test_over_cap_without_part_fill_goes_pending():
    """超上限且 `part_fill=false` → **整笔不成交、挂单**（不改数量），理由点名约束。"""
    r = match(OrderSpec("buy", 100, "limit", Decimal("10.00")),
              snap(ask1_volume=250), MODEL_OFF, participation=Decimal("0.2"))
    assert r.outcome == PENDING and r.price is None
    assert "成交量约束" in (r.pending_reason or "")
    assert "50 股" in (r.pending_reason or "")
    assert r.cap_reason and "50 股" in r.cap_reason


def test_over_cap_with_part_fill_returns_partial():
    """超上限且 `part_fill=true` → 成交上限股数（`partial`）。"""
    r = match(OrderSpec("buy", 100, "limit", Decimal("10.00")),
              snap(ask1_volume=250), MODEL_ON, participation=Decimal("0.2"))
    assert r.outcome == PARTIAL and r.qty == 50 and r.price == Decimal("10.00")
    assert r.matched and not r.filled


def test_zero_cap_is_pending_even_with_part_fill():
    """盘口量为 0 → 上限 0，`part_fill=true` 也无可成交 → 挂单（不产生 0 股成交）。"""
    r = match(OrderSpec("buy", 100, "limit", Decimal("10.00")),
              snap(ask1_volume=0), MODEL_ON, participation=Decimal("0.9"))
    assert r.outcome == PENDING


def test_volume_constraint_does_not_fire_when_book_missing():
    """没有盘口量 → 不加约束（即便配了参与率）：不是「一律挂单」。"""
    r = match(OrderSpec("buy", 1000, "limit", Decimal("10.00")),
              snap(), MODEL_ON, participation=Decimal("0.0001"))
    assert r.outcome == FILLED


def test_sell_uses_bid_volume():
    """卖出看买盘挂量：卖 100 股、买盘只有 60 股、参与率 1.0 → 部分成交 60。"""
    r = match(OrderSpec("sell", 100, "limit", Decimal("10.00")),
              snap(bid1_volume=60), MODEL_ON, participation=Decimal("1.0"))
    assert r.outcome == PARTIAL and r.qty == 60


# ════════════════════════════════════════════════════════════════════════
# 二 · 影子臂与真实臂共用同一份参与率（红线 11）
# ════════════════════════════════════════════════════════════════════════

def test_two_arms_share_the_participation_cap():
    """同一（快照、委托、参与率）经 `match` 得到同一个结果 —— 两臂同假设的形式化。

    真实撮合（`matching.engine.execute`）与影子撮合（`app.shadow._simulate_one`）
    都调这一个函数、都传 `fin_param.liquidity_max_participation` 的**同一个值**，
    所以「成交量约束」这条撮合假设在两臂上一字不差。
    """
    spec = OrderSpec("buy", 100, "limit", Decimal("10.00"))
    s = snap(ask1_volume=250)
    a = match(spec, s, MODEL_ON, participation=Decimal("0.2"))
    b = match(spec, s, MODEL_ON, participation=Decimal("0.2"))
    assert a == b and a.outcome == PARTIAL and a.qty == 50


# ════════════════════════════════════════════════════════════════════════
# 三 · 端到端（需要账本库）
# ════════════════════════════════════════════════════════════════════════

os.environ.setdefault("HUNTER_INTERNAL_KEY", "test-internal-key")
os.environ.setdefault("PAPER_MODE", "PAPER")

from helpers import install_quote_source, order_body, seed_reference, uniq_when  # noqa: E402

from app import ledger  # noqa: E402
from app.matching import engine  # noqa: E402
from app.snapshot.source import set_source  # noqa: E402

pytest.importorskip("psycopg2")
if not os.getenv("PAPER_TEST_DSN"):
    pytest.skip("未设置 PAPER_TEST_DSN，跳过成交量/部分成交端到端用例", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
H = {"X-Hunter-Internal-Key": "test-internal-key"}


def _code_for(project_id: str) -> str:
    n = int(project_id.rsplit("_", 1)[-1], 16) % 900000
    return f"6{n + 100000:06d}"


def _set_participation(pg, project_id: str, value) -> None:
    """把参与率写进 `fin_param`（路由用自己的连接读 → 必须提交）。"""
    pg.execute("UPDATE fin_param SET liquidity_max_participation = %s WHERE project_id = %s",
               (value, project_id))
    pg.connection.commit()


@pytest.mark.db
def test_participation_rate_changes_fill_outcome(pg, project):
    """**改参与率 → 成交量跟着变**：同一笔委托，参与率 1.0 整笔成交、0.2 变成挂单。"""
    code = _code_for(project)
    when = uniq_when(project)
    seed_reference(pg, code=code, trade_date=when[:10])
    pg.connection.commit()
    # 盘口卖挂 250 股；申报 100 股。
    install_quote_source(when=when, code=code, price="10.00", ask1="10.00", ask1_volume=250)
    assert client.post(f"/api/v1/projects/{project}/funding",
                       headers=H, json={}).status_code == 200

    # ① 参与率 1.0 → 上限 floor(250 × 1.0) = 250 ≥ 100 → 整笔成交
    _set_participation(pg, project, Decimal("1.0"))
    resp = client.post("/api/v1/orders", headers=H,
                       json=order_body(project, code=code, qty=100))
    assert resp.status_code == 200, resp.text
    r1 = resp.json()
    assert r1["status"] == "filled", r1
    assert r1["filled_qty"] == 100

    # ② 参与率 0.2 → 上限 floor(250 × 0.2) = 50 < 100 → 整笔不成交、挂单
    _set_participation(pg, project, Decimal("0.2"))
    resp2 = client.post("/api/v1/orders", headers=H,
                        json=order_body(project, code=code, qty=100))
    assert resp2.status_code == 200, resp2.text
    r2 = resp2.json()
    assert r2["status"] == "pending", r2
    assert r2["filled_qty"] == 0
    assert "成交量约束" in (r2["pending_reason"] or "")

    # 库里看得见：一条 filled、一条 pending
    orders = client.get(f"/api/v1/projects/{project}/orders", headers=H).json()["items"]
    assert sorted(o["status"] for o in orders) == ["filled", "pending"]


@pytest.mark.db
def test_part_fill_on_books_partial_and_keeps_remainder(pg, project):
    """**开关开着时真的 partial**：成交上限股数、写 `partially_filled`、剩余挂着。

    直接调 `engine.execute`（用 `pg` 的事务，不提交）—— 把执行模型改成 `part_fill=true`
    只在本用例的事务里可见，**不污染别的用例**。
    """
    code = _code_for(project)
    when = uniq_when(project)
    seed_reference(pg, code=code, trade_date=when[:10])
    # 本事务内把整笔成交改成部分成交（不提交 → 用例结束回滚）
    pg.execute("UPDATE fin_execution_model SET part_fill = true")
    _set_participation(pg, project, Decimal("0.2"))
    install_quote_source(when=when, code=code, price="10.00", ask1="10.00", ask1_volume=250)
    ledger.seed_funding(pg, project)

    req = {"project_id": project, "code": code, "side": "buy", "qty": 100,
           "price_type": "limit", "limit_price": "10.00"}
    receipt = engine.execute(pg, req)

    assert receipt["status"] == "partially_filled", receipt
    assert receipt["filled_qty"] == 50
    assert receipt["price"] == Decimal("10.0000")

    # fin_trade：一条成交，50 股
    pg.execute("SELECT qty, price FROM fin_trade WHERE order_id = %s", (receipt["order_id"],))
    trade = pg.fetchone()
    assert int(trade["qty"]) == 50 and str(trade["price"]) == "10.0000"

    # fin_order：partially_filled，filled_qty=50，**剩余 50 股仍挂着**
    pg.execute("SELECT status, qty, filled_qty FROM fin_order WHERE order_id = %s",
               (receipt["order_id"],))
    order = pg.fetchone()
    assert order["status"] == "partially_filled"
    assert int(order["qty"]) == 100 and int(order["filled_qty"]) == 50

    # 持仓 50 股；冻结里还留着剩余 50 股的占用（对账的 frozen_matches_open 靠它）
    pg.execute("SELECT qty FROM fin_position WHERE project_id = %s AND code = %s", (project, code))
    assert int(pg.fetchone()["qty"]) == 50
    available, frozen = ledger.cash_balance(pg, project, "CN_A")
    assert frozen > 0, (available, frozen)
    assert ledger.open_buy_frozen(pg, project, "CN_A") == frozen
