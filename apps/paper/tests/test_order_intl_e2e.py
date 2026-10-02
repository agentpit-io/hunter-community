"""N3 · 港美股的市价单降级 + 「报价不可用不得成交」两条专项（连库，真走四步链路）。

出口标准 ③④ 落在这里：

3. **「报价时间戳只到日期 → 不得用于成交」**（对齐一期 M8 的坑）：报个只有日期的时刻，
   快照落不下来 → 委托被拒、**一笔成交都没有**；
4. **「取不到报价 → 挂单，而不是拿零价成交」**：断流（报价旧了 / 不可用）→ 委托转
   **挂单**，账本里没有成交；任何情况下**不许出现 0 价成交**。

外加一条 N3 的口径：**港美股本期只接限价单**（设计文档 §3.4）——
市价单直接拒绝并留痕，不拿最新价冒充买一/卖一；A 股有盘口，市价单照旧。

**为什么这几条不需要费率行**：市价单判据排在六条风控之前，`NoSnapshot` 判据更早；
而港美股的生产费率行本期**还没落数**（N1/N2 决策，见 N2 报告 §五）——拿它当前置条件
反而测不到这两条。港美股「拿真实报价走一遍撮合得到非空成交」的真跑证据在 N3 成果文档里
（一次性容器 + 临时费率行，跑完删掉；测试库的 `fin_paper_rw` 没有 DELETE 权限，
所以那段不放在 pytest 里）。
"""

from __future__ import annotations

import os
from datetime import datetime

import pytest

os.environ.setdefault("HUNTER_INTERNAL_KEY", "test-internal-key")
os.environ.setdefault("PAPER_MODE", "PAPER")

pytestmark = pytest.mark.db

pytest.importorskip("psycopg2")
_DSN = os.getenv("PAPER_TEST_DSN", "").strip()
if not _DSN:
    pytest.skip("未设置 PAPER_TEST_DSN，跳过港美股端到端用例", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from helpers import install_quote_source, order_body, prime_quote  # noqa: E402

from app.main import app  # noqa: E402
from app.market_time import market_tz  # noqa: E402

client = TestClient(app)
H = {"X-Hunter-Internal-Key": "test-internal-key"}

def _post(path, body):
    r = client.post(path, json=body, headers=H)
    return r.status_code, (r.json() if r.content else None)


def _seed_instrument(pg, code, market, currency, exchange, board, lot_size):
    pg.execute(
        """
        INSERT INTO fin_instrument
          (code, name, exchange, board, is_st, limit_up_pct, limit_down_pct, lot_size,
           market, currency, source)
        VALUES (%s, %s, %s, %s, false, NULL, NULL, %s, %s, %s, 'n3-test')
        ON CONFLICT (code) DO UPDATE SET market = EXCLUDED.market, currency = EXCLUDED.currency,
          lot_size = EXCLUDED.lot_size, exchange = EXCLUDED.exchange, board = EXCLUDED.board
        """,
        (code, code, exchange, board, lot_size, market, currency),
    )


def _fund(project):
    assert _post(f"/api/v1/projects/{project}/funding", {})[0] == 200


def _when(project, market, salt=0):
    """该项目在目标市场当地的盘中时刻（时区按市场取，不写死偏移）。"""
    from helpers import uniq_date

    d = datetime.fromisoformat(uniq_date(project, salt=salt)).date()
    return datetime(d.year, d.month, d.day, 10, 0, tzinfo=market_tz(market)).isoformat()


def _trades(pg, project):
    """**按项目**数成交（按代码数会被别的用例写下的同代码成交串上）。"""
    pg.execute("SELECT count(*) AS n FROM fin_trade WHERE project_id = %s", (project,))
    return pg.fetchone()["n"]


# ── ③ 港美股市价单：只接限价单 ──────────────────────────────────────────────

@pytest.mark.parametrize("market,code,exchange,board,currency", [
    ("HK", "00005", "HKEX", "hk_main", "HKD"),
    ("US", "ORCL", "NASDAQ", "us_main", "USD"),
])
def test_intl_market_order_is_rejected(pg, project, market, code, exchange, board, currency):
    """港美股**没有盘口** → 市价单直接拒绝（不拿最新价冒充买一/卖一）。

    这条判据排在六条风控**之前**，所以不需要日历 / 费率行 ——
    港美股的生产费率行本期还没落数（N1/N2 决策），拿它当前置条件反而测不到这条。
    """
    when = _when(project, market, salt=1)
    _seed_instrument(pg, code, market, currency, exchange, board, 100 if market == "HK" else 1)
    pg.connection.commit()
    install_quote_source(when=when, price="10.00", prev_close="10.00")
    _fund(project)

    status, receipt = _post("/api/v1/orders",
                            order_body(project, code=code, qty=100, price_type="market"))
    assert status == 200
    assert receipt["status"] == "rejected"
    assert receipt["failed_checks"] == ["price_type"]
    assert market in receipt["decline_reason"] and "只接限价单" in receipt["decline_reason"]
    assert receipt["trade_id"] is None
    assert _trades(pg, project) == 0
    pg.connection.rollback()


def test_cn_a_market_order_is_still_allowed(pg, project):
    """**A 股不得回归**：有盘口，市价单照旧按对手价 + 滑点成交。"""
    code = "601398"
    src = prime_quote(pg, project, code=code, salt=2,
                      price="10.00", prev_close="10.00", bid1="9.99", ask1="10.01")
    assert src is not None
    _fund(project)

    status, receipt = _post("/api/v1/orders",
                            order_body(project, code=code, qty=100, price_type="market"))
    assert status == 200
    assert receipt["status"] == "filled"
    assert receipt["price_basis"] == "ask1_price+slippage"
    from decimal import Decimal

    assert Decimal(str(receipt["price"])) == Decimal("10.02")   # 卖一 10.01 + 1 tick(0.01)
    assert _trades(pg, project) == 1
    pg.connection.rollback()


# ── ④ 专项：报价不可用 → 挂单，绝不拿零价/过期价成交 ────────────────────────

def test_stale_quote_parks_the_order_not_fills(pg, project):
    """断流（报价旧了）→ **挂单**，账本里一笔成交都没有。"""
    from helpers import relax_staleness, restore_staleness

    prev = relax_staleness(60)                       # 新鲜度上限收到 60 秒
    try:
        code = "600036"
        when = "1990-03-01T10:00:00+08:00"           # 36 年前 → 必判 stale
        prime_quote(pg, project, code=code, when=when, price="10.00", prev_close="10.00")
        _fund(project)
        status, receipt = _post("/api/v1/orders",
                                order_body(project, code=code, qty=100, price="10.00"))
    finally:
        restore_staleness(prev)

    assert status == 200
    assert receipt["status"] == "pending"            # 挂单，不是成交
    assert receipt["snapshot_quality"] == "stale" and receipt["snapshot_missing_flag"] is True
    assert "挂单" in receipt["pending_reason"]
    assert _trades(pg, project) == 0
    pg.connection.rollback()


def test_missing_quote_is_rejected_without_a_trade(pg, project):
    """**行情完全拿不到** → 拒绝（一期 M3 口径：没见过有效报价不挂单），**没有成交**。

    与上一条合起来就是「取不到报价 → 不成交」：断流挂单、完全拿不到拒绝，
    两条路都**不会**写出一个 0 价的成交。
    """
    from app.snapshot.source import NullQuoteSource, set_source

    code = "600030"
    set_source(NullQuoteSource())
    _fund(project)
    status, receipt = _post("/api/v1/orders",
                            order_body(project, code=code, qty=100, price="10.00"))
    assert status == 200
    assert receipt["status"] == "rejected"
    assert "没有可用快照" in receipt["decline_reason"]
    assert receipt["price"] is None and receipt["amount"] is None
    assert _trades(pg, project) == 0
    pg.connection.rollback()


def test_date_only_quote_cannot_fill(pg, project):
    """**专项（对齐 M8）**：报价时间戳只到日期 → 快照落不下来 → 绝不成交。"""
    code = "600000"
    prime_quote(pg, project, code=code, when="2026-10-02T00:00:00+08:00",
                price="10.00", prev_close="10.00")
    _fund(project)
    status, receipt = _post("/api/v1/orders",
                            order_body(project, code=code, qty=100, price="10.00"))
    assert status == 200
    assert receipt["status"] == "rejected"
    assert "没有可用快照" in receipt["decline_reason"]
    assert receipt["snapshot_id"] is None
    assert _trades(pg, project) == 0
    pg.connection.rollback()
