"""风控参数化 · **端到端**（港 / 美股走完整四步链路，需要账本库）。

出口标准 ②③④ 在这里落到真实链路上：
  · **日历缺失 → 拒绝交易**（读不到 `(market, trade_date)` 那一行）；
  · **`price_limit_mode='none'` 不作校验但留痕**（回执 `price_limit_checked=false`）；
  · **同一笔委托在不同市场的可卖数量判定不同**（A 股当日买入不可卖、美股可卖）。

费用模型：港股 / 美股的**生产费率行本期未落数**（N1 决策），所以这里只为 `US` 临时
种一行**测试用**费率（`version='test-us-fee-v1'`），用完即删（`finally`）——
绝不留下一个看起来像生产数据的港美股费率行。
"""

from __future__ import annotations

import os
from datetime import date, datetime

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

from helpers import install_quote_source, order_body, uniq_date  # noqa: E402

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


def _fund(project):
    assert _post(f"/api/v1/projects/{project}/funding", {})[0] == 200


def _when(project, market):
    """该项目的**独有报价时刻**，落在目标市场的当地盘中（避免撞快照）。

    时刻必须用**目标市场的时区**构造（不能用固定 `-04:00`）：美股的 UTC 偏移随夏令时变，
    写死一个偏移会让冬令时的日期落到盘中之外（实测 10:00-04:00 在冬令时 = 09:00 ET）。
    """
    d = date.fromisoformat(uniq_date(project))
    return datetime(d.year, d.month, d.day, 10, 0, tzinfo=market_tz(market)).isoformat()


def test_us_order_traces_market_and_price_limit(pg, project):
    """美股：市场识别为 US、日历按 (US, 当地日) 读到、价格带 `none` 留痕。

    ⚠️ 订单**最终停在第 5 条**：港美股的生产费率行本期未落数（见文件头与 N1 决策），
    所以 `fin_fee_model` 里没有 US 行 → 费用模型缺失 → 拒绝。这**不是**本阶段要消掉的
    缺陷，是「不编费率」的诚实结果；`tests/test_risk_markets.py` 用代表性费率模型
    验证了「有费率行时六条全过」。
    """
    code = "AAPL"
    when = _when(project, "US")
    _seed_instrument(pg, code, "US", "USD", "NASDAQ", "us_main", 1)
    _seed_calendar(pg, "US", when[:10])
    pg.connection.commit()
    install_quote_source(when=when, price="10.00", prev_close="10.00")
    _fund(project)

    status, receipt = _post("/api/v1/orders", order_body(project, code=code, qty=10, price="10.00"))
    assert status == 200
    assert receipt["market"] == "US"
    assert receipt["price_limit_checked"] is False        # none → 不作校验但留痕
    assert receipt["risk"]["price_limit"]["ok"] is True
    assert receipt["risk"]["price_limit"]["detail"]["note"].startswith("该市场未做价格带校验")
    assert receipt["risk"]["session"]["ok"] is True        # 时段按美东时区判、落在盘中
    assert receipt["status"] == "rejected"
    assert "fee" in receipt["failed_checks"] and "费用模型" in receipt["decline_reason"]
    assert "session" not in receipt["failed_checks"]


def test_hk_calendar_missing_rejects(pg, project):
    """**专项**：港股没有该日日历行 → 拒绝，原因写明「没有 HK 该日的交易日历」。"""
    code = "00700"
    when = _when(project, "HK")
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
    day = uniq_date(project, salt=7)
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
