"""账本端到端（需要真实账本库）：入金 → 买入 → 持仓 → 估值 → 对账 → 卖出。

走真实 HTTP 路由（TestClient + 内部口令），不是直接调函数 —— 这样路由、鉴权、
响应序列化都一起验到。

M3 起委托**不携带快照**：快照由服务端按 code 现取。测试用
`helpers.install_quote_source()` 装一个固定报价源，走的路与非测试环境逐行相同。

测试库是**一次性**的，用例会往库里追加行（账本只追加，本来也删不掉）；
用随机 project_id / user_id，互不干扰。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("HUNTER_INTERNAL_KEY", "test-internal-key")
os.environ.setdefault("PAPER_MODE", "PAPER")

from helpers import (  # noqa: E402
    install_quote_source,
    order_body,
    prime_quote,
    seed_reference,
    uniq_date,
    uniq_when,
)

pytestmark = pytest.mark.db

pytest.importorskip("psycopg2")
if not os.getenv("PAPER_TEST_DSN"):
    pytest.skip("未设置 PAPER_TEST_DSN，跳过账本端到端用例", allow_module_level=True)

from app.main import app  # noqa: E402


# 用例用的标代码。**故意避开 600519**：`fin_snapshot` 是全库共享的，
# M6 的演示夹具（`apps/api/scripts/seed_m6_demo.py`）给 600519 种了 2026-09 的价格，
# 而估值 / 对账按「该代码截至 as_of 的最新一张快照」取价 —— 用同一个代码就会取到
# 夹具的价，`market_value` 与用例无关地变成 1724.50。换一个夹具不用的代码，
# 断言就只依赖本用例自己种下的那一张快照（与 `uniq_when` 避开撞快照编号同一思路）。
#
# ⚠ **每个用例一个独有的代码**（`ready` fixture 按 project_id 现算并改写本全局）：
# `fin_snapshot` 是全库共享、只追加的，而估值 / 对账按「该代码 ≤ as_of 的最新快照」
# 取价 —— 全文件共用一个代码时，后跑的用例会取到前一个用例留下的、**日期更晚**的
# 那张快照（报价日期是随机的 1990–2022，谁大谁赢）。这是本文件自 M3 起就有的
# 非幂等缺陷（N3 基线同样随机失败 1~2 条），表现为 `market_value` 差一截 /
# `balance_equation` 报「缺价」。按用例隔离代码后，每个用例只看得到自己那张快照。
CODE = "600000"        # 默认值；`ready` fixture 会按项目改写（见下）


def _code_for(project_id: str) -> str:
    """由 project_id 派一个**纯数字**的独有代码（6 位，归 CN_A）。

    纯数字是必须的：`market_of()` 按代码形态判市场，带字母会被判成美股。
    """
    n = int(project_id.rsplit("_", 1)[-1], 16) % 900000
    return f"6{n + 100000:06d}"


def _snap_id(when: str, code: str) -> str:
    """按 `SNAP-{日期}-{时刻}-{代码}` 拼出编号（口径与 `snapshot.store` 一致）。"""
    dt = datetime.fromisoformat(when)
    return f"SNAP-{dt:%Y%m%d}-{dt:%H%M%S}-{code}"


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
    """干净项目 + 参考数据 + 一个**只属于这个项目**的报价时刻。

    快照编号只到秒，而 `fin_snapshot` 是全库共享、只追加的 —— 时刻不唯一的话，
    两个用例会抢同一行快照（`ON CONFLICT DO NOTHING`），第二个拿到第一个的价格。

    本 fixture **同时给用例一个独有代码**（改写模块级 `CODE`）：估值 / 对账按
    「该代码 ≤ as_of 的最新快照」取价，共用代码会让后跑的用例取到别人的价。
    用例都是在调用时才读 `CODE`，所以在 fixture 里改写即可覆盖全文件。
    """
    global CODE
    CODE = _code_for(project)
    seed_reference(pg, code=CODE, trade_date=uniq_date(project))
    pg.connection.commit()
    install_quote_source(when=uniq_when(project), code=CODE)
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

    status, receipt = _post("/api/v1/orders", order_body(ready, code=CODE))
    assert status == 200, receipt
    assert receipt["status"] == "filled", receipt
    assert receipt["amount"] == "1000.0000"
    assert receipt["fee"]["total"] == "5.0100"
    assert receipt["price_basis"] == "snapshot_last_price"

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


def test_trade_snapshot_is_linked_and_time_comes_from_source(ready, pg):
    """成交行能反查快照，且那张快照的 `snapshot_time` **就是数据源给的时刻**。"""
    when = uniq_when(ready)
    _, receipt = _post("/api/v1/orders", order_body(ready, code=CODE))
    snap_id = receipt["snapshot_id"]
    assert snap_id == _snap_id(when, CODE)

    pg.execute(
        """
        SELECT t.trade_id, t.price, s.snapshot_id, s.snapshot_time, s.source, s.last_price
          FROM fin_trade t JOIN fin_snapshot s ON s.snapshot_id = t.snapshot_id
         WHERE t.trade_id = %s
        """,
        (receipt["trade_id"],),
    )
    row = pg.fetchone()
    pg.connection.rollback()
    assert row is not None
    assert row["snapshot_id"] == snap_id
    # 成交时刻 == 数据源的时刻（比的是**同一瞬间**，不依赖会话时区怎么显示）
    assert row["snapshot_time"] == datetime.fromisoformat(when)
    assert str(row["price"]) == "10.0000" == str(row["last_price"])
    assert row["source"] == "test"


def test_balance_equation_holds_after_buy(ready):
    _post("/api/v1/orders", order_body(ready, code=CODE))
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
            "position_ties_trades", "project_version", "frozen_zero_no_open",
            "frozen_matches_open"} <= names


def test_odd_lot_buy_rejected_with_reason(ready):
    status, rec = _post("/api/v1/orders", order_body(ready, code=CODE, qty=150))
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


def test_outside_session_rejected(ready, pg):
    # 用**不带偏移**的时刻串，由 `helpers.at()` 按真实 `Asia/Shanghai` 补偏移 ——
    # 写死 `+08:00` 时，1990/1991 夏令时那几个月的 12:00 实际是当地 13:00（午休结束），
    # 就落进下午时段、不再被拒（N3 起就有的非幂等，见 helpers.CST 的注释）。
    prime_quote(pg, ready, salt=1, when=uniq_date(ready, 1) + "T12:00:00")
    status, rec = _post("/api/v1/orders", order_body(ready, code=CODE))
    assert status == 200 and rec["status"] == "rejected"
    assert "session" in rec["failed_checks"]


def test_insufficient_cash_rejected(ready, pg):
    prime_quote(pg, ready, salt=2, price="200.00", prev_close="200.00")
    status, rec = _post("/api/v1/orders", order_body(ready, code=CODE, price="200.00"))
    assert status == 200 and rec["status"] == "rejected"
    assert "可用资金不足" in rec["decline_reason"]


def test_t1_blocks_same_day_sell_then_allows_after_cutover(ready):
    _post("/api/v1/orders", order_body(ready, code=CODE))
    status, rec = _post("/api/v1/orders", order_body(ready, code=CODE, side="sell", qty=100))
    assert rec["status"] == "rejected" and "t1" in rec["failed_checks"]

    # 日切：次日起可卖
    from app import db, ledger

    with db.cursor(commit=True) as cur:
        ledger.confirm_t1(cur, ready)

    status, rec = _post("/api/v1/orders", order_body(ready, code=CODE, side="sell", qty=100))
    assert rec["status"] == "filled", rec
    assert rec["fee"]["stamp_tax"] == "0.5000"        # 卖出才有印花税
    cash = _get(f"/api/v1/projects/{ready}/cash")
    assert cash["available"] == "9989.4800"
    pos = _get(f"/api/v1/projects/{ready}/positions")["items"]
    assert pos[0]["qty"] == 0


def test_human_order_follows_same_path(ready):
    """source='human' 与 'ai' 走同一条风控与记账路径（09 §六-10）。"""
    body = order_body(ready, code=CODE, source="human", actor="user-1")
    status, rec = _post("/api/v1/orders", body)
    assert rec["status"] == "filled"
    trades = _get(f"/api/v1/projects/{ready}/trades")["items"]
    assert trades[0]["source"] == "human"


def test_order_requires_no_snapshot_from_client(ready):
    """客户端**不能**自己塞一个快照进来 —— 成交价不接受调用方指定。"""
    body = order_body(ready, code=CODE)
    body["snapshot"] = {"snapshot_id": "SNAP-CLIENT-1", "last_price": "0.01"}
    status, rec = _post("/api/v1/orders", body)
    # 多出来的字段被 pydantic 忽略，成交价仍来自服务端取到的快照（10.00）
    assert rec["status"] == "filled"
    assert rec["price"] == "10.0000"
    assert rec["snapshot_id"] == _snap_id(uniq_when(ready), CODE)


def test_unusable_snapshot_parks_the_order(ready, pg):
    """行情断流（快照旧了 → `missing_flag=true`）→ **不成交，转挂单**。

    报价时刻定在 2026-01-05（早就过去了），把新鲜度上限临时收到 60 秒，
    于是这张快照被判 `stale` + `missing_flag=true`。委托照旧落库、买入资金冻上，
    但**一笔成交都没有** —— 宁可挂单，绝不用过期价成交（`09 §4.4`）。
    """
    from helpers import relax_staleness, restore_staleness

    previous = relax_staleness(60)
    try:
        prime_quote(pg, ready, salt=9,
                    when=uniq_date(ready, 9) + "T10:00:00")   # 同上：偏移交给 helpers.at()
        status, rec = _post("/api/v1/orders", order_body(ready, code=CODE))
    finally:
        restore_staleness(previous)

    assert status == 200 and rec["status"] == "pending", rec
    assert rec["snapshot_quality"] == "stale"
    assert rec["snapshot_missing_flag"] is True
    assert "快照不可用于成交" in rec["pending_reason"]

    assert _get(f"/api/v1/projects/{ready}/trades")["items"] == []
    cash = _get(f"/api/v1/projects/{ready}/cash")
    assert cash["available"] == "8994.9900"     # 1005.01 冻上了
    assert cash["frozen"] == "1005.0100"
    orders = _get(f"/api/v1/projects/{ready}/orders")["items"]
    assert orders[0]["status"] == "pending" and orders[0]["frozen_amount"] == "1005.0100"


def test_no_quote_at_all_rejects_the_order(ready):
    """数据源什么都给不出来 → 一张快照都没有 → 拒绝（不是挂单）。

    挂单的前提是「我们见过这只票的有效报价」；连涨跌停都算不出的委托挂进队列，
    等于把风控绕过延后到成交那一刻。
    """
    install_quote_source(when=None)              # 数据源没给时间戳
    status, rec = _post("/api/v1/orders", order_body(ready, code=CODE))
    assert status == 200 and rec["status"] == "rejected", rec
    assert "没有可用快照" in rec["decline_reason"]
    assert rec["snapshot_id"] is None
    assert _get(f"/api/v1/projects/{ready}/trades")["items"] == []
    assert {e["kind"] for e in _get(f"/api/v1/projects/{ready}/cash")["entries"]} == {"deposit"}


def test_limit_not_reached_parks_then_fills_on_new_snapshot(ready, pg):
    """限价单快照价劣于限价 → 挂单；换一张更好的快照，再撮一次就成交。"""
    prime_quote(pg, ready, salt=3, price="10.50", prev_close="10.50")
    status, rec = _post("/api/v1/orders", order_body(ready, code=CODE, price="10.00"))
    assert status == 200 and rec["status"] == "pending", rec
    assert "劣于买入限价" in rec["pending_reason"]
    order_id = rec["order_id"]

    # 新快照：跌到 9.90（优于限价）
    prime_quote(pg, ready, salt=4, price="9.90", prev_close="10.50")
    status, matched = _post(f"/api/v1/projects/{ready}/orders/match-open", {})
    assert status == 200, matched
    assert matched["matched"] == 1
    assert matched["fills"][0]["order_id"] == order_id
    assert matched["fills"][0]["price"] == "9.9000"

    trades = _get(f"/api/v1/projects/{ready}/trades")["items"]
    assert len(trades) == 1 and trades[0]["price"] == "9.9000"
    # 受理时按限价冻 1005.01；实际只花 990 + 5.0099（含过户费 0.0099）→ 多冻的退回可用
    cash = _get(f"/api/v1/projects/{ready}/cash")
    assert cash["frozen"] == "0.0000"
    assert cash["available"] == "9004.9901"
    recon = _post(f"/api/v1/projects/{ready}/recon",
                  {"as_of": "2026-10-02T10:06:00+08:00"})[1]
    assert recon["passed"] is True, [c for c in recon["checks"] if not c["passed"]]


def test_expire_open_orders_unfreezes(ready, pg):
    """收盘仍未成交 → 撤单（`expired`）并**解冻**。"""
    prime_quote(pg, ready, salt=5, price="10.50", prev_close="10.50")
    status, rec = _post("/api/v1/orders", order_body(ready, code=CODE, price="10.00"))
    assert rec["status"] == "pending", rec
    order_id = rec["order_id"]

    before = _get(f"/api/v1/projects/{ready}/cash")
    assert before["frozen"] == "1005.0100"

    status, out = _post(f"/api/v1/projects/{ready}/orders/expire",
                        {"at": "2026-10-02T15:05:00+08:00", "reason": "close"})
    assert status == 200 and out["expired"] == 1
    assert out["orders"][0]["order_id"] == order_id
    assert out["orders"][0]["unfrozen"] == "1005.0100"

    cash = _get(f"/api/v1/projects/{ready}/cash")
    assert cash["frozen"] == "0.0000"
    assert cash["available"] == "10000.0000"
    kinds = [e["kind"] for e in cash["entries"] if e["kind"] != "deposit"]
    assert "freeze" in kinds and "unfreeze" in kinds

    orders = _get(f"/api/v1/projects/{ready}/orders")["items"]
    assert orders[0]["status"] == "expired"
    assert _get(f"/api/v1/projects/{ready}/trades")["items"] == []

    recon = _post(f"/api/v1/projects/{ready}/recon",
                  {"as_of": "2026-10-02T15:06:00+08:00"})[1]
    assert recon["passed"] is True, [c for c in recon["checks"] if not c["passed"]]


def test_expire_only_validity_when_asked(ready, pg):
    """`reason='validity'` 只撤越期的；还没到期的挂单留着。"""
    prime_quote(pg, ready, salt=6, price="10.50", prev_close="10.50")
    _post("/api/v1/orders", order_body(ready, code=CODE, price="10.00",
                                       valid_until="2026-10-02T14:00:00+08:00"))
    cash = _get(f"/api/v1/projects/{ready}/cash")
    assert cash["frozen"] == "1005.0100"

    # 还没到期 → 一个都不撤
    _, out = _post(f"/api/v1/projects/{ready}/orders/expire",
                   {"at": "2026-10-02T11:00:00+08:00", "reason": "validity"})
    assert out["expired"] == 0
    assert _get(f"/api/v1/projects/{ready}/cash")["frozen"] == "1005.0100"

    # 过了有效期 → 撤掉并解冻
    _, out = _post(f"/api/v1/projects/{ready}/orders/expire",
                   {"at": "2026-10-02T14:30:00+08:00", "reason": "validity"})
    assert out["expired"] == 1
    assert _get(f"/api/v1/projects/{ready}/cash")["frozen"] == "0.0000"


def test_order_past_valid_until_is_rejected(ready):
    """委托到达时已经过了有效期 → 拒绝，不补单（`01方案 §11.2` 的「信号过期」）。"""
    status, rec = _post("/api/v1/orders", order_body(
        ready, code=CODE, valid_until=uniq_date(ready) + "T00:00:00+08:00"))
    assert status == 200 and rec["status"] == "rejected", rec
    assert "有效期" in rec["decline_reason"]
    assert _get(f"/api/v1/projects/{ready}/trades")["items"] == []
    assert _get(f"/api/v1/projects/{ready}/cash")["frozen"] == "0.0000"


def test_cancel_order_unfreezes(ready, pg):
    prime_quote(pg, ready, salt=7, price="10.50", prev_close="10.50")
    _, rec = _post("/api/v1/orders", order_body(ready, code=CODE, price="10.00"))
    order_id = rec["order_id"]

    status, out = _post(f"/api/v1/orders/{order_id}/cancel", {"memo": "改主意了"})
    assert status == 200 and out["status"] == "cancelled" and out["changed"] is True
    assert out["unfrozen"] == "1005.0100"
    assert _get(f"/api/v1/projects/{ready}/cash")["frozen"] == "0.0000"

    # 再撤一次是幂等的（已终态，不报错、不再解冻）
    _, again = _post(f"/api/v1/orders/{order_id}/cancel", {})
    assert again["changed"] is False


def test_sell_commitment_blocks_double_promise(ready, pg):
    """同一批股不能被两张挂单各卖一次（卖方占用由未成交卖单推导）。"""
    _post("/api/v1/orders", order_body(ready, code=CODE))                    # 买 100
    from app import db, ledger

    with db.cursor(commit=True) as cur:
        ledger.confirm_t1(cur, ready)

    prime_quote(pg, ready, salt=8, price="9.00", prev_close="10.00")
    _, first = _post("/api/v1/orders", order_body(ready, code=CODE, side="sell", qty=100, price="10.00"))
    assert first["status"] == "pending", first

    _, second = _post("/api/v1/orders", order_body(ready, code=CODE, side="sell", qty=100, price="10.00"))
    assert second["status"] == "rejected", second
    assert "t1" in second["failed_checks"]
