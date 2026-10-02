# -*- coding: utf-8 -*-
"""二期 N1 · 「打开市场维度」专项用例（真库；`TEST_DATABASE_URL` 未设时整体 skip）。

跑法（指向一个**已跑过 0023–0031 迁移的库**，不要指向在用的库）::

    # 完整校验（库里有一期 A 股历史行）：13 条全过
    TEST_DATABASE_URL=postgresql://hunter:hunter@127.0.0.1:5598/hunter \
      cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_market_scope.py -q

    # 空库（只有 0029–0031 的结构，没有历史行）：9 passed, 4 skipped
    #   —— 4 条依赖「历史行」的断言按设计 skip，不误报失败

依赖历史行的四条：`fin_market_calendar` / `fin_fee_model` 有 A 股行、
`fin_instrument` 有 A 股标的、五张账本表有行 —— 空库上无行可验，跳过。

证明 N1 出口标准 §三.3 的四件事：

  ① 迁移 `0029`–`0031` **可重复执行**（同一份 SQL 连跑两遍不报错）；
  ② `fin_market_rule` 三行齐备（`CN_A` / `HK` / `US`），且 `CN_A` 行**与现状语义等价**；
  ③ `fin_instrument` 现在能插入港股 / 美股标的（`exchange` / `board` 的 CHECK 已放开）；
  ④ 账本五表（`fin_order` / `fin_trade` / `fin_cash_ledger` / `fin_position` /
     `fin_valuation`）的 `currency` 列存在，且历史行已回填 `'CNY'`。

为什么要有这一条：这三个迁移**只加列、只放松约束**，出问题的方式全是「静默」——
CHECK 没真的放开（插不进去，或在旧库上其实没放开）、回填漏了某张表、
主键没换成 `(market, trade_date)`（三个市场日历经不起）。
单看 SQL 看不出来，只能对着真库跑一遍。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# ── sys.path 归一（同 test_fin_project_router.py 的说明）─────────────────
_API_ROOT = Path(__file__).resolve().parents[1]
for _p in ("", "/", str(_API_ROOT)):
    while _p in sys.path:
        sys.path.remove(_p)
sys.path.insert(0, str(_API_ROOT))
_bad_app = sys.modules.get("app")
if _bad_app is not None and Path(getattr(_bad_app, "__file__", "") or "").parent == _API_ROOT:
    del sys.modules["app"]

psycopg2 = pytest.importorskip("psycopg2")
import psycopg2.extras  # noqa: E402
from psycopg2 import errors as pg_errors  # noqa: E402

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="需要 TEST_DATABASE_URL 指向一个已跑过 0023 迁移的 postgres（见 test_migrate.py）",
)

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"

N1_MIGRATIONS = (
    "0029_market_scope.sql",
    "0030_market_rule.sql",
    "0031_ledger_currency.sql",
)

LEDGER_TABLES = ("fin_order", "fin_trade", "fin_cash_ledger", "fin_position", "fin_valuation")

# 现状语义（0029/0030 必须与之一致）：A 股时段与六时点。
# 时段来自 fin_market_calendar.sessions 现状；时点来自 fin-worker/app/points.py。
CN_A_SESSIONS = [{"open": "09:30", "close": "11:30"}, {"open": "13:00", "close": "15:00"}]
CN_A_POINTS = ["09:15", "09:30", "11:30", "13:00", "14:55", "15:30"]


# ════════════════════════════════════════════════════════════════════════
# 夹具
# ════════════════════════════════════════════════════════════════════════
@pytest.fixture
def conn():
    c = psycopg2.connect(TEST_DATABASE_URL)
    c.autocommit = True
    try:
        yield c
    finally:
        c.close()


def _q(conn, sql, args=None):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, args)
        if cur.description:
            return cur.fetchall()
        return None


# ════════════════════════════════════════════════════════════════════════
# ① 迁移可重复执行
# ════════════════════════════════════════════════════════════════════════
def test_迁移文件齐备():
    for name in N1_MIGRATIONS:
        path = MIGRATIONS_DIR / name
        assert path.is_file(), f"缺迁移文件 {path}"
        assert path.stat().st_size > 0


def test_三个迁移连跑两遍都不报错(conn):
    """同一份 SQL 连跑两遍 —— 第二遍必须是空操作、不报错（N1 出口标准 §三.1）。"""
    for name in N1_MIGRATIONS:
        sql = (MIGRATIONS_DIR / name).read_text(encoding="utf-8")
        for round_no in (1, 2):
            with conn.cursor() as cur:
                cur.execute(sql)  # 整段一次性执行（psycopg2 简单查询协议）
            # 跑到这里没抛异常即通过；第二遍的额外断言见各自的专项用例。


# ════════════════════════════════════════════════════════════════════════
# ② fin_market_rule 三行齐备 + CN_A 语义等价
# ════════════════════════════════════════════════════════════════════════
def test_市场规则表三行齐备(conn):
    rows = {r["market"]: r for r in _q(conn, "SELECT * FROM fin_market_rule")}
    assert set(rows) == {"CN_A", "HK", "US"}, f"应恰有 CN_A/HK/US 三行，实为 {sorted(rows)}"
    assert rows["CN_A"]["currency"] == "CNY"
    assert rows["HK"]["currency"] == "HKD"
    assert rows["US"]["currency"] == "USD"
    assert rows["HK"]["timezone"] == "Asia/Hong_Kong"
    assert rows["US"]["timezone"] == "America/New_York"
    # 港美股：无涨跌停 → price_limit_mode='none'；每手/US=1、HK 按标的
    assert rows["HK"]["price_limit_mode"] == "none"
    assert rows["US"]["price_limit_mode"] == "none"
    assert rows["HK"]["lot_rule"] == "per_instrument"
    assert rows["US"]["lot_rule"] == "one" and rows["US"]["lot_fixed"] == 1
    # 每个市场的 source 都必须非空（红线：参数要能溯源）
    for m, r in rows.items():
        assert r["source"] and len(r["source"]) > 30, f"{m} 的 source 太短，说不清来源"


def test_CN_A行与现状语义等价(conn):
    """`CN_A` 一行必须与一期现状逐项等价 —— 「A 股一行都不能坏」的落点。"""
    row = _q(conn, "SELECT * FROM fin_market_rule WHERE market = 'CN_A'")[0]
    # 0030 的硬要求：T+1 / 100 股整手 / pct 涨跌停
    assert row["currency"] == "CNY"
    assert row["timezone"] == "Asia/Shanghai"
    assert row["sellable_rule"] == "t_plus_n"
    assert row["sellable_days"] == 1
    assert row["lot_rule"] == "fixed"
    assert row["lot_fixed"] == 100
    assert row["price_limit_mode"] == "pct"
    assert row["fee_model_version"] == "fee-cn-a-v1"  # 指向 0023/0025 落的那行
    assert row["sessions"] == CN_A_SESSIONS
    assert row["points"] == CN_A_POINTS


def test_CN_A规则与库里A股日历与费率逐条对齐(conn):
    """拿库里真实的 A 股日历行与费率行，反证规则行不是另写一套。

    空库上没有 A 股历史行可对账（`fin_market_calendar` / `fin_fee_model` 为空）
    → 按设计跳过；**对着有历史数据的库（如本机 dev 库）跑才是完整校验**。
    """
    cal = _q(
        conn,
        "SELECT sessions FROM fin_market_calendar "
        "WHERE market = 'CN_A' AND is_trading ORDER BY trade_date DESC LIMIT 1",
    )
    if not cal:
        pytest.skip("库里没有 A 股交易日历行（空库），无历史行可对账")
    assert cal[0]["sessions"] == CN_A_SESSIONS

    fee = _q(conn, "SELECT market, currency FROM fin_fee_model WHERE version = 'fee-cn-a-v1'")
    assert fee, "库里没有 fee-cn-a-v1 费率行"
    assert (fee[0]["market"], fee[0]["currency"]) == ("CN_A", "CNY")


# ════════════════════════════════════════════════════════════════════════
# ③ fin_instrument 能插入港美股标的
# ════════════════════════════════════════════════════════════════════════
def test_能插入港股美股标的(conn):
    """`exchange` / `board` 的 CHECK 已放开；港美股无涨跌停 → limit_* 允许 NULL。

    全程在一个事务里，最后 **rollback**，不往库里留测试数据。
    """
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("DELETE FROM fin_instrument WHERE code IN ('00700','AAPL')")

            cur.execute(
                """
                INSERT INTO fin_instrument
                  (code, name, exchange, board, is_st, limit_up_pct, limit_down_pct,
                   lot_size, market, currency, source)
                VALUES ('00700','腾讯控股','HKEX','hk_main',false,NULL,NULL,100,'HK','HKD','n1-test')
                RETURNING code, exchange, board, market, currency, limit_up_pct, lot_size
                """
            )
            hk = cur.fetchone()
            assert (hk["exchange"], hk["board"], hk["market"], hk["currency"]) == \
                ("HKEX", "hk_main", "HK", "HKD")
            assert hk["limit_up_pct"] is None and hk["lot_size"] == 100

            cur.execute(
                """
                INSERT INTO fin_instrument
                  (code, name, exchange, board, is_st, limit_up_pct, limit_down_pct,
                   lot_size, market, currency, source)
                VALUES ('AAPL','Apple Inc.','NASDAQ','us_main',false,NULL,NULL,1,'US','USD','n1-test')
                RETURNING code, exchange, board, market, currency, lot_size
                """
            )
            us = cur.fetchone()
            assert (us["exchange"], us["board"], us["market"], us["currency"]) == \
                ("NASDAQ", "us_main", "US", "USD")
            assert us["lot_size"] == 1

            # 负例：没放开的交易所仍被 CHECK 拒绝（放宽不等于取消）
            with pytest.raises(pg_errors.CheckViolation):
                cur.execute(
                    """
                    INSERT INTO fin_instrument
                      (code, name, exchange, board, is_st, limit_up_pct, limit_down_pct,
                       lot_size, market, currency, source)
                    VALUES ('TSTLSE','Bad','LSE','us_main',false,NULL,NULL,1,'US','USD','n1-test')
                    """
                )
        conn.rollback()
    finally:
        conn.rollback()
        conn.autocommit = True


def test_原有A股标的一行未坏(conn):
    """迁移后 A 股标的仍在、值未变、market/currency 已补齐（空库按设计跳过）。"""
    rows = _q(conn, "SELECT code, exchange, board, limit_up_pct, market, currency "
                    "FROM fin_instrument WHERE exchange IN ('SH','SZ','BJ')")
    if not rows:
        pytest.skip("库里没有 A 股标的历史行（空库）")
    for r in rows:
        assert r["market"] == "CN_A"
        assert r["currency"] == "CNY"
        assert r["limit_up_pct"] is not None, f"{r['code']} 的涨跌停值应保留"


# ════════════════════════════════════════════════════════════════════════
# ④ 账本五表币种列存在 + 历史行回填 'CNY'
# ════════════════════════════════════════════════════════════════════════
def test_账本五表有currency列(conn):
    for table in LEDGER_TABLES:
        cols = _q(
            conn,
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s AND column_name='currency'",
            (table,),
        )
        assert cols, f"{table} 缺 currency 列"
        assert cols[0]["is_nullable"] == "NO", f"{table}.currency 应是 NOT NULL"


def test_账本行币种与市场一致_无NULL(conn):
    """五张账本表：`currency` **不许为空**，且必须等于该行 `market` 的本币。

    原来的断言是「历史行全须是 CNY」——那是 N1/N2 回填时的口径，当时库里只有
    A 股。N4 起港美股子账户会真的落 HKD / USD 行（本机已跑出真实成交），
    「全 CNY」不再成立；**换成更强的口径**：每一行的币种与其市场一致
    （CN_A→CNY / HK→HKD / US→USD），既保住「回填不留 NULL」，又验到
    「本币记账、币种与市场一致」这条 N4 出口标准。
    """
    seen_rows = 0
    for table in LEDGER_TABLES:
        rows = _q(
            conn,
            f"SELECT count(*) AS total, "
            f"count(*) FILTER (WHERE currency IS NULL) AS nulls, "
            f"count(*) FILTER (WHERE market IS NULL) AS null_market, "
            f"count(*) FILTER (WHERE currency <> "
            f"  CASE market WHEN 'CN_A' THEN 'CNY' WHEN 'HK' THEN 'HKD' "
            f"               WHEN 'US' THEN 'USD' ELSE NULL END) AS mismatched "
            f"FROM {table}",
        )
        r = rows[0]
        assert r["nulls"] == 0, f"{table} 还有 {r['nulls']} 行 currency 为空"
        assert r["null_market"] == 0, f"{table} 还有 {r['null_market']} 行 market 为空"
        assert r["mismatched"] == 0, (
            f"{table} 有 {r['mismatched']} 行币种与市场不一致"
            f"（CN_A→CNY / HK→HKD / US→USD）：total={r['total']}"
        )
        seen_rows += r["total"]
    if seen_rows == 0:
        pytest.skip("五张账本表都是空的（空库），没有历史行可验回填")


def test_账本没有fx_rate列(conn):
    """汇率不进账本（设计文档 §3.1 A 方案）——加列时别顺手加 fx_rate。"""
    for table in LEDGER_TABLES:
        cols = _q(
            conn,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s AND column_name = 'fx_rate'",
            (table,),
        )
        assert not cols, f"{table} 不该有 fx_rate 列"


# ════════════════════════════════════════════════════════════════════════
# 附：市场维度已铺开的其它表 + 日历主键
# ════════════════════════════════════════════════════════════════════════
def test_market列已加到相关表(conn):
    expected = ("fin_account", "fin_instrument", "fin_market_calendar", "fin_snapshot", "fin_fee_model")
    for table in expected:
        cols = _q(
            conn,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s AND column_name='market'",
            (table,),
        )
        assert cols, f"{table} 缺 market 列"
    # fin_project / fin_account 的 CHECK 已放开
    proj = _q(conn, "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
                    "WHERE conname = 'fin_project_currency_check'")
    assert proj and "'HKD'" in proj[0]["def"] and "'USD'" in proj[0]["def"]


def test_日历主键是market_trade_date(conn):
    cols = _q(
        conn,
        "SELECT a.attname FROM pg_index i "
        "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
        "WHERE i.indrelid = 'fin_market_calendar'::regclass AND i.indisprimary",
    )
    names = sorted(r["attname"] for r in cols)
    assert names == ["market", "trade_date"], f"日历主键应为 (market, trade_date)，实为 {names}"


def test_日历A股行已回填market与source(conn):
    """N1 的回填：历史 A 股日历行（`calendar_source='akshare'`）必须是 `market='CN_A'`。

    ⚠️ N2 起库里还有 HK / US 的日历行（`hkex_official` / `nyse_official`），
    所以**不能再假设整张表都是 A 股**（原断言 `cn_a == total` 已不成立）——
    改为「akshare 行都属于 CN_A」+「没有未回填 market 的行」。
    """
    r = _q(
        conn,
        "SELECT count(*) AS ak, count(*) FILTER (WHERE market = 'CN_A') AS ak_cn "
        "FROM fin_market_calendar WHERE calendar_source = 'akshare'",
    )[0]
    if r["ak"] == 0:
        pytest.skip("库里没有 A 股日历历史行（空库）")
    assert r["ak_cn"] == r["ak"], "akshare 日历行未全部回填 market='CN_A'"
    nomarket = _q(
        conn, "SELECT count(*) AS n FROM fin_market_calendar WHERE market IS NULL"
    )[0]["n"]
    assert nomarket == 0, "日历行 market 有 NULL（未回填）"
