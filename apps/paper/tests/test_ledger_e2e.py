"""账本端到端（需要真实账本库）：入金 → 买入 → 持仓 → 估值 → 对账 → 卖出。

走真实 HTTP 路由（TestClient + 内部口令），不是直接调函数 —— 这样路由、鉴权、
响应序列化都一起验到。

测试库是**一次性**的，用例会往库里追加行（账本只追加，本来也删不掉）；
用随机 project_id / user_id，互不干扰。
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("HUNTER_INTERNAL_KEY", "test-internal-key")
os.environ.setdefault("PAPER_MODE", "PAPER")

from helpers import order_body, seed_reference  # noqa: E402

pytestmark = pytest.mark.db

pytest.importorskip("psycopg2")
if not os.getenv("PAPER_TEST_DSN"):
    pytest.skip("未设置 PAPER_TEST_DSN，跳过账本端到端用例", allow_module_level=True)

from app.main import app  # noqa: E402

client = TestClient(app)
H = {"X-Hunter-Internal-Key": "test-internal-key"}


def _get(path):
    r = client.get(path, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def _post(path, body):
    r = client.post(path, json=body, headers=H)
    return r.status_code, (r.json() if r.content else None)


@pytest.fixture
def ready(pg, project):
    seed_reference(pg, code="600519")
    pg.connection.commit()
    assert _post(f"/api/v1/projects/{project}/funding", {})[0] == 200
    return project


def test_funding_is_idempotent(ready):
    first = _post(f"/api/v1/projects/{ready}/funding", {})[1]
    assert first["created"] is False            # fixture 已经记过一次
    cash = _get(f"/api/v1/projects/{ready}/cash")
    assert cash["available"] == "10000.0000"
    assert cash["frozen"] == "0.0000"
    # 本金只有一条 deposit 流水
    assert sum(1 for e in cash["entries"] if e["kind"] == "deposit") == 1


def test_buy_updates_all_seven_entities(ready, pg):
    before = _get(f"/api/v1/projects/{ready}")

    status, receipt = _post("/api/v1/orders", order_body(ready))
    assert status == 200, receipt
    assert receipt["status"] == "filled"
    assert receipt["amount"] == "1000.0000"
    assert receipt["fee"]["total"] == "5.0100"

    # fin_trade +1
    trades = _get(f"/api/v1/projects/{ready}/trades")["items"]
    assert len(trades) == 1
    assert trades[0]["qty"] == 100 and trades[0]["price"] == "10.0000"
    assert trades[0]["snapshot_id"] == receipt["snapshot_id"]
    assert trades[0]["fee_model_version"] == "fee-cn-a-v1"

    # fin_cash_ledger +3（freeze / buy / fee），另有入金那条 deposit
    cash = _get(f"/api/v1/projects/{ready}/cash")
    trade_entries = [e for e in cash["entries"] if e["kind"] != "deposit"]
    assert len(trade_entries) == 3
    assert {e["kind"] for e in trade_entries} == {"freeze", "buy", "fee"}
    assert cash["available"] == "8994.9900"
    assert cash["frozen"] == "0.0000"          # 冻结在同一事务里归零

    # fin_position 更新：T+1 → sellable 仍为 0
    pos = _get(f"/api/v1/projects/{ready}/positions")["items"]
    assert len(pos) == 1 and pos[0]["qty"] == 100 and pos[0]["sellable_qty"] == 0

    # fin_project.version +1
    after = _get(f"/api/v1/projects/{ready}")
    assert after["project"]["version"] == before["project"]["version"] + 1

    # fin_order 落了一条 filled
    orders = _get(f"/api/v1/projects/{ready}/orders")["items"]
    assert orders[0]["status"] == "filled" and orders[0]["filled_qty"] == 100


def test_balance_equation_holds_after_buy(ready):
    _post("/api/v1/orders", order_body(ready))
    status, val = _post(f"/api/v1/projects/{ready}/valuation",
                        {"as_of": "2026-10-02T10:05:00+08:00"})
    assert status == 200, val
    # 持仓市值 1000 + 可用 8994.99 + 冻结 0 = 9994.99（本金 10000 − 费用 5.01）
    assert val["market_value"] == "1000.0000"
    assert val["cash_available"] == "8994.9900"
    assert val["cash_frozen"] == "0.0000"
    assert val["total_assets"] == "9994.9900"
    assert val["nav"] == "0.999499"

    status, recon = _post(f"/api/v1/projects/{ready}/recon",
                          {"as_of": "2026-10-02T10:05:00+08:00"})
    assert status == 200, recon
    assert recon["passed"] is True
    assert all(c["passed"] for c in recon["checks"])
    names = {c["name"] for c in recon["checks"]}
    assert {"cash_sum", "balance_equation", "buy_lot", "cash_ties_trades",
            "position_ties_trades", "project_version", "frozen_zero_no_open"} <= names


def test_odd_lot_buy_rejected_with_reason(ready):
    status, rec = _post("/api/v1/orders", order_body(ready, qty=150))
    assert status == 200
    assert rec["status"] == "rejected"
    assert "100 股的整数倍" in rec["decline_reason"]
    assert "lot" in rec["failed_checks"]

    # 被拒的委托**照样留痕**（append），但没有成交、没有流水
    orders = _get(f"/api/v1/projects/{ready}/orders")["items"]
    assert orders[0]["status"] == "rejected" and orders[0]["decline_reason"]
    assert _get(f"/api/v1/projects/{ready}/trades")["items"] == []
    # 只有入金那条 deposit，没有任何成交相关流水
    assert {e["kind"] for e in _get(f"/api/v1/projects/{ready}/cash")["entries"]} == {"deposit"}


def test_missing_instrument_rejected(ready):
    status, rec = _post("/api/v1/orders", order_body(ready, code="999999"))
    assert status == 200
    assert rec["status"] == "rejected"
    assert "元数据" in rec["decline_reason"]
    assert "price_limit" in rec["failed_checks"]


def test_outside_session_rejected(ready):
    body = order_body(ready, at="2026-10-02T12:00:00+08:00")
    status, rec = _post("/api/v1/orders", body)
    assert status == 200 and rec["status"] == "rejected"
    assert "session" in rec["failed_checks"]


def test_insufficient_cash_rejected(ready):
    status, rec = _post("/api/v1/orders", order_body(ready, price="200.00", prev_close="200.00"))
    assert status == 200 and rec["status"] == "rejected"
    assert "可用资金不足" in rec["decline_reason"]


def test_t1_blocks_same_day_sell_then_allows_after_cutover(ready):
    _post("/api/v1/orders", order_body(ready))
    status, rec = _post("/api/v1/orders", order_body(ready, side="sell", qty=100))
    assert rec["status"] == "rejected" and "t1" in rec["failed_checks"]

    # 日切：次日起可卖
    from app import db, ledger

    with db.cursor(commit=True) as cur:
        ledger.confirm_t1(cur, ready)

    status, rec = _post("/api/v1/orders", order_body(ready, side="sell", qty=100))
    assert rec["status"] == "filled", rec
    assert rec["fee"]["stamp_tax"] == "0.5000"        # 卖出才有印花税
    cash = _get(f"/api/v1/projects/{ready}/cash")
    assert cash["available"] == "9989.4800"
    pos = _get(f"/api/v1/projects/{ready}/positions")["items"]
    assert pos[0]["qty"] == 0


def test_human_order_follows_same_path(ready):
    """source='human' 与 'ai' 走同一条风控与记账路径（09 §六-10）。"""
    body = order_body(ready, source="human", actor="user-1")
    status, rec = _post("/api/v1/orders", body)
    assert rec["status"] == "filled"
    trades = _get(f"/api/v1/projects/{ready}/trades")["items"]
    assert trades[0]["source"] == "human"


def test_snapshot_row_written_and_linked(ready, pg):
    snap_id = f"SNAP-TEST-{uuid.uuid4().hex[:12]}"
    _post("/api/v1/orders", order_body(ready, snapshot_id=snap_id))
    pg.execute("SELECT code, last_price FROM fin_snapshot WHERE snapshot_id = %s", (snap_id,))
    row = pg.fetchone()
    assert row and row["code"] == "600519"
    pg.connection.rollback()
