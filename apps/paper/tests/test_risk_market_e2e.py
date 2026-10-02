"""风控参数化 · **端到端**（港 / 美股走完整四步链路，需要账本库）。

出口标准 ②③④ 在这里落到真实链路上：
  · **日历缺失 → 拒绝交易**（读不到 `(market, trade_date)` 那一行）；
  · **`price_limit_mode='none'` 不作校验但留痕**（回执 `price_limit_checked=false`）；
  · **同一笔委托在不同市场的可卖数量判定不同**（A 股当日买入不可卖、美股可卖）。

费用模型：**N4 起港美股的生产费率行已落表**（`0034` 的 `fee-hk-v1` / `fee-us-v1`），
所以下面这些用例不再需要临时种费率行；费用模型缺失那条路径由
`tests/test_risk_markets.py` 用「无费率行」的代表性输入单独覆盖。

隔离（N4）：账本按**市场子账户**分（`(project_id, market)`）。这里新增两条
「真下单 → 真成交 → 本币入账」用例，并断言**在 US 子账户买入不动 CN_A 子账户的可用资金**
（串账反例）。
"""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal

import pytest

os.environ.setdefault("HUNTER_INTERNAL_KEY", "test-internal-key")
os.environ.setdefault("PAPER_MODE", "PAPER")

pytestmark = pytest.mark.db

pytest.importorskip("psycopg2")
_DSN = os.getenv("PAPER_TEST_DSN", "").strip()
if not _DSN:
    pytest.skip("未设置 PAPER_TEST_DSN，跳过港美股端到端用例", allow_module_level=True)

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from helpers import ABSENT_DATE, install_quote_source, order_body, uniq_date  # noqa: E402

from app.main import app  # noqa: E402
from app.market_time import market_tz  # noqa: E402

client = TestClient(app)
H = {"X-Hunter-Internal-Key": "test-internal-key"}

SESSIONS = {"HK": [{"open": "09:30", "close": "12:00"}, {"open": "13:00", "close": "16:00"}],
            "US": [{"open": "09:30", "close": "16:00"}]}


def _post(path, body):
    r = client.post(path, json=body, headers=H)
    return r.status_code, (r.json() if r.content else None)


def _seed_instrument(pg, code, market, currency, exchange, board, lot_size):
    # 幅度：A 股 10%（pct 模式要读它）；港美股 NULL（none 模式不读）。
    up = 0.10 if market == "CN_A" else None
    pg.execute(
        """
        INSERT INTO fin_instrument
          (code, name, exchange, board, is_st, limit_up_pct, limit_down_pct, lot_size,
           market, currency, source)
        VALUES (%s, %s, %s, %s, false, %s, %s, %s, %s, %s, 'n2-test')
        ON CONFLICT (code) DO UPDATE SET market = EXCLUDED.market, currency = EXCLUDED.currency,
          lot_size = EXCLUDED.lot_size, exchange = EXCLUDED.exchange, board = EXCLUDED.board,
          limit_up_pct = EXCLUDED.limit_up_pct, limit_down_pct = EXCLUDED.limit_down_pct
        """,
        (code, code, exchange, board, up, up, lot_size, market, currency),
    )


def _seed_calendar(pg, market, trade_date):
    pg.execute(
        """
        INSERT INTO fin_market_calendar (market, trade_date, is_trading, sessions, calendar_source)
        VALUES (%s, %s, true, %s, 'n2-test')
        ON CONFLICT (market, trade_date) DO UPDATE SET is_trading = true,
          sessions = EXCLUDED.sessions, calendar_source = EXCLUDED.calendar_source
        """,
        (market, trade_date, psycopg2.extras.Json(SESSIONS[market])),
    )


def _fund(project, market=None):
    q = f"?market={market}" if market else ""
    assert _post(f"/api/v1/projects/{project}/funding{q}", {})[0] == 200


def _cash(project, market=None):
    q = f"?market={market}" if market else ""
    r = client.get(f"/api/v1/projects/{project}/cash{q}", headers=H)
    assert r.status_code == 200
    return r.json()


def _when(project, market, when_day=None):
    """该项目的**独有报价时刻**，落在目标市场的当地盘中（避免撞快照）。

    时刻必须用**目标市场的时区**构造（不能用固定 `-04:00`）：美股的 UTC 偏移随夏令时变，
    写死一个偏移会让冬令时的日期落到盘中之外（实测 10:00-04:00 在冬令时 = 09:00 ET）。

    `when_day` 给定一个固定日期（`ABSENT_DATE`）时用它 —— 「日历缺失」用例要一个
    **别的用例不会种日历**的日期（日历表全库共享、跨用例累积）。
    """
    d = date.fromisoformat(when_day or uniq_date(project))
    return datetime(d.year, d.month, d.day, 10, 0, tzinfo=market_tz(market)).isoformat()


def test_us_order_traces_market_and_price_limit(pg, project):
    """美股：市场识别为 US、日历按 (US, 当地日) 读到、价格带 `none` 留痕。

    N4 起港美股的生产费率行已落表（`fee-us-v1`），所以第 5 条**通过**；
    这里的项目**只给 CN_A 子账户入金**（`_fund(project)` 走项目 `market_scope`），
    于是这笔美股委托停在**第 6 条（US 子账户没有资金）** —— 恰好证明
    「美股委托只动 US 子账户，不会花 CN_A 的钱」。
    """
    code = "AAPL"
    when = _when(project, "US")
    _seed_instrument(pg, code, "US", "USD", "NASDAQ", "us_main", 1)
    _seed_calendar(pg, "US", when[:10])
    pg.connection.commit()
    install_quote_source(when=when, price="10.00", prev_close="10.00")
    _fund(project)   # 只入金 CN_A 子账户

    status, receipt = _post("/api/v1/orders", order_body(project, code=code, qty=10, price="10.00"))
    assert status == 200
    assert receipt["market"] == "US"
    assert receipt["currency"] == "USD"
    assert receipt["price_limit_checked"] is False        # none → 不作校验但留痕
    assert receipt["risk"]["price_limit"]["ok"] is True
    assert receipt["risk"]["price_limit"]["detail"]["note"].startswith("该市场未做价格带校验")
    assert receipt["risk"]["session"]["ok"] is True        # 时段按美东时区判、落在盘中
    assert receipt["risk"]["fee"]["ok"] is True            # N4：费率行已落表
    assert receipt["status"] == "rejected"
    assert receipt["failed_checks"] == ["funds"]           # 只差 US 子账户没钱
    assert "session" not in receipt["failed_checks"]


def _fill_in_market(pg, project, code, market, currency, exchange, board, lot_size, qty):
    """在 `market` 子账户里真买一笔，返回回执。"""
    when = _when(project, market)
    _seed_instrument(pg, code, market, currency, exchange, board, lot_size)
    _seed_calendar(pg, market, when[:10])
    pg.connection.commit()
    install_quote_source(when=when, price="10.00", prev_close="10.00")
    _fund(project, "CN_A")
    _fund(project, market)
    status, receipt = _post("/api/v1/orders",
                            order_body(project, code=code, qty=qty, price="10.00"))
    assert status == 200, receipt
    return receipt


def test_us_fill_is_usd_and_does_not_touch_cn_a_subaccount(pg, project):
    """**串账反例（N4）**：美股子账户买入 → 只动 US 子账户的可用资金，CN_A 一分不变。"""
    # 先给 CN_A 子账户入金（幂等），**再**读基准 —— 否则读到的是入金前的 0，
    # 而 `_fill_in_market` 里的入金会让它变成 10000，看起来像「串账」。
    _fund(project, "CN_A")
    cn_before = _cash(project, "CN_A")["available"]
    receipt = _fill_in_market(pg, project, "AAPL", "US", "USD", "NASDAQ", "us_main", 1, 10)
    assert receipt["status"] == "filled"
    assert receipt["currency"] == "USD"

    cn_after = _cash(project, "CN_A")["available"]
    us_after = _cash(project, "US")["available"]
    assert str(cn_after) == str(cn_before), "US 子账户买入不该动 CN_A 子账户的可用资金"
    # 金额是字符串（JSON），必须按**数值**比 —— '9899.99' < '10000' 按字符串比是 False。
    assert Decimal(us_after) < Decimal(cn_after)   # US 子账户扣了成交额 + 费用

    # 成交 / 流水 / 持仓都带本币与市场
    cur = pg.connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT market, currency, amount FROM fin_trade WHERE project_id = %s", (project,))
    trades = cur.fetchall()
    assert trades and all(t["market"] == "US" and t["currency"] == "USD" for t in trades)
    cur.execute("SELECT market, currency FROM fin_position WHERE project_id = %s", (project,))
    pos = cur.fetchall()
    assert pos and all(p["market"] == "US" and p["currency"] == "USD" for p in pos)


def test_hk_fill_is_hkd_and_does_not_touch_cn_a_subaccount(pg, project):
    """**串账反例（N4）**：港股子账户买入 → 只动 HK 子账户的可用资金，CN_A 一分不变。"""
    _fund(project, "CN_A")   # 幂等；先入金再读基准（见上一条用例的说明）
    cn_before = _cash(project, "CN_A")["available"]
    receipt = _fill_in_market(pg, project, "00700", "HK", "HKD", "HKEX", "hk_main", 100, 100)
    assert receipt["status"] == "filled"
    assert receipt["currency"] == "HKD"

    assert str(_cash(project, "CN_A")["available"]) == str(cn_before)
    assert Decimal(_cash(project, "HK")["available"]) < Decimal(cn_before)

    cur = pg.connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT market, currency FROM fin_trade WHERE project_id = %s", (project,))
    assert all(t["market"] == "HK" and t["currency"] == "HKD" for t in cur.fetchall())


def test_hk_calendar_missing_rejects(pg, project):
    """**专项**：港股没有该日日历行 → 拒绝，原因写明「没有 HK 该日的交易日历」。"""
    code = "00700"
    when = _when(project, "HK", when_day=ABSENT_DATE)   # 池子外的固定日期（见 A 股那条）
    # 故意**不**种 (HK, 该日) 的日历行
    _seed_instrument(pg, code, "HK", "HKD", "HKEX", "hk_main", 100)
    pg.connection.commit()
    install_quote_source(when=when, price="10.00", prev_close="10.00")
    _fund(project)

    status, receipt = _post("/api/v1/orders", order_body(project, code=code, qty=100, price="10.00"))
    assert status == 200
    assert receipt["status"] == "rejected"
    assert "没有 HK 该日的交易日历" in receipt["decline_reason"]
    assert "session" in receipt["failed_checks"]


def test_cn_a_calendar_missing_rejects(pg, project):
    """同一条判据对 A 股一样：没有该日日历行 → 拒绝（不退化成一至五即交易日）。"""
    code = "600123"
    day = ABSENT_DATE        # 用池子外的固定日期：日历表跨用例累积，随机日期会撞
    _seed_instrument(pg, code, "CN_A", "CNY", "SH", "main", 100)   # 不种日历
    pg.connection.commit()
    install_quote_source(when=f"{day}T10:00:00+08:00", price="10.00", prev_close="10.00")
    _fund(project)
    status, receipt = _post("/api/v1/orders", order_body(project, code=code, qty=100, price="10.00"))
    assert status == 200 and receipt["status"] == "rejected"
    assert "交易日历" in receipt["decline_reason"]


def test_hk_non_trading_day_rejects(pg, project):
    """港股有日历但标了非交易日 → 拒绝（与缺失区分开）。"""
    code = "00740"
    when = _when(project, "HK")
    pg.execute(
        """
        INSERT INTO fin_market_calendar (market, trade_date, is_trading, sessions, calendar_source)
        VALUES ('HK', %s, false, '[]'::jsonb, 'n2-test')
        ON CONFLICT (market, trade_date) DO UPDATE SET is_trading = false, sessions = '[]'::jsonb
        """,
        (when[:10],),
    )
    _seed_instrument(pg, code, "HK", "HKD", "HKEX", "hk_main", 100)
    pg.connection.commit()
    install_quote_source(when=when, price="10.00", prev_close="10.00")
    _fund(project)
    status, receipt = _post("/api/v1/orders", order_body(project, code=code, qty=100, price="10.00"))
    assert status == 200 and receipt["status"] == "rejected"
    assert "非交易日" in receipt["decline_reason"]
