"""M5 验收种子数据 —— **构造一个账本场景**，好让「每日报告」有真账本可读。

⚠️ 这不是产品功能，是**验收夹具**（与 M4 的 `prj_m4test` 同一个性质）：
它往 `fin_*` 表写一个专用项目 `prj_m5demo` 的三天估值、两笔成交与对账记录，
供 `check_report_numbers.py` 与 M5 成果文档取证用。**幂等**，可反复跑。

账本口径：本金 10 万（manage 档）；三天净值 1.0000 → 1.1200 → 1.0500，
所以 2026-09-30 的报告里 `daily_return_pct = -6.25%`、`max_drawdown = -6.25%`。

用法::

    DATABASE_URL=postgresql://hunter:hunter@127.0.0.1:5598/hunter \
      cd apps/api && PYTHONPATH=. python scripts/seed_m5_demo.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

from app.services.fin import tiers  # noqa: E402

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://hunter:hunter@localhost:5432/hunter")
from app.services.market_time import market_tz
SH = market_tz("CN_A")

PROJECT_ID = "prj_m5demo"
# 固定 UUID：读接口要过 JWT 中间件的 `_user_exists`，所以这个用户必须真的在
# `users` 表里（`python -m app.migrate` 之后由本脚本补齐）。密码哈希是占位串 ——
# 验收走 `auth._sign_access()` 直接签 token，不走登录。
USER_ID = "11111111-2222-4333-8444-555555555555"
USER_EMAIL = "m5demo@example.com"
TIER = "manage"


def _dt(day: str, hh: int = 15, mm: int = 30) -> datetime:
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hh, mm, tzinfo=SH)


def seed(conn) -> None:
    tpl = tiers.build_template(TIER)
    capital = Decimal(str(tpl["initial_capital"]))
    cur = conn.cursor()

    # ── 清掉本夹具上一次的行（只动 prj_m5demo 自己的；子表在前，外键顺序）──
    # 报告产物（hunter_artifacts）一并清 —— 本夹具是这套产物的唯一来源。
    cur.execute("DELETE FROM hunter_artifacts.published_artifact "
                "WHERE source_message_id LIKE 'fin-report:%'")
    for table in ("fin_report_fact", "fin_publish_receipt"):
        cur.execute(f"DELETE FROM {table} WHERE report_id IN "
                    "(SELECT report_id FROM fin_report WHERE project_id=%s)", (PROJECT_ID,))
    for table in ("fin_report", "fin_trade", "fin_cash_ledger", "fin_valuation",
                  "fin_recon_log", "fin_position", "fin_order", "fin_param", "fin_project"):
        cur.execute(f"DELETE FROM {table} WHERE project_id = %s", (PROJECT_ID,))
    cur.execute("DELETE FROM fin_account WHERE account_id = 'acct_m5demo' OR user_id = %s",
                (USER_ID,))

    # ── 项目 / 账户 / 参数 ─────────────────────────────────────────────
    # 用户行（读接口要过 JWT 中间件的 `_user_exists`）。
    cur.execute(
        "INSERT INTO users (id, email, email_lower, pw_hash, role, status) "
        "VALUES (%s,%s,%s,'x-placeholder','user','active') ON CONFLICT (id) DO NOTHING",
        (USER_ID, USER_EMAIL, USER_EMAIL))
    cur.execute("INSERT INTO fin_account (account_id, user_id, locked_at) VALUES (%s,%s,now())",
                ("acct_m5demo", USER_ID))
    cur.execute("INSERT INTO fin_project (project_id, user_id, tier, initial_capital) "
                "VALUES (%s,%s,%s,%s)", (PROJECT_ID, USER_ID, TIER, capital))
    cur.execute(
        """
        INSERT INTO fin_param (
          project_id, board_flags, sector_prefs, liquidity_min_amount,
          liquidity_max_participation, strategies, hold_days_max, stop_loss_pct,
          take_profit_pct, max_positions, max_position_pct, min_order_amount,
          daily_max_new, daily_max_orders, daily_loss_halt_pct, account_drawdown_halt_pct)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (PROJECT_ID, psycopg2.extras.Json(tpl["board_flags"]),
         psycopg2.extras.Json(tpl["sector_prefs"]), tpl["liquidity_min_amount"],
         tpl["liquidity_max_participation"], psycopg2.extras.Json(tpl["strategies"]),
         tpl["hold_days_max"], tpl["stop_loss_pct"], tpl["take_profit_pct"],
         tpl["max_positions"], tpl["max_position_pct"], tpl["min_order_amount"],
         tpl["daily_max_new"], tpl["daily_max_orders"], tpl["daily_loss_halt_pct"],
         tpl["account_drawdown_halt_pct"]))

    # ── 入金 ───────────────────────────────────────────────────────────
    cur.execute(
        "INSERT INTO fin_cash_ledger (project_id, kind, amount, available_after, frozen_after, memo) "
        "VALUES (%s,'deposit',%s,%s,0,'M5 验收种子')", (PROJECT_ID, capital, capital))

    # ── 两笔成交（各自要一条快照 + 一条委托，外键齐全）─────────────────
    trades = [
        # (order, trade, code, day, price, fee_model)
        ("ord_m5_1", "trd_m5_1", "601398", "2026-09-29", Decimal("10.0000")),
        ("ord_m5_2", "trd_m5_2", "000001", "2026-09-30", Decimal("12.0000")),
    ]
    for order_id, trade_id, code, day, price in trades:
        snap_id = f"SNAP-{day.replace('-','')}-093000-{code}"
        qty = 100
        amount = (price * qty).quantize(Decimal("0.0001"))
        commission = max(Decimal("5.00"), (amount * Decimal("0.00025")).quantize(Decimal("0.0001")))
        transfer = (amount * Decimal("0.00001")).quantize(Decimal("0.0001"))
        total_fee = (commission + transfer).quantize(Decimal("0.0001"))
        at = _dt(day, 9, 30)
        # 快照是全局的（按代码），不属于某个项目 —— 重复跑时覆盖价格而不是报冲突。
        cur.execute("INSERT INTO fin_snapshot (snapshot_id, code, snapshot_time, source, "
                    "last_price, quality, missing_flag) VALUES (%s,%s,%s,'seed',%s,'ok',false) "
                    "ON CONFLICT (snapshot_id) DO UPDATE SET last_price = EXCLUDED.last_price",
                    (snap_id, code, at, price))
        cur.execute("INSERT INTO fin_order (order_id, project_id, code, side, qty, price_type, "
                    "status, filled_qty, created_at) VALUES (%s,%s,%s,'buy',%s,'market','filled',%s,%s)",
                    (order_id, PROJECT_ID, code, qty, qty, at))
        cur.execute(
            "INSERT INTO fin_trade (trade_id, order_id, project_id, code, side, qty, price, amount,"
            " commission, stamp_tax, transfer_fee, total_fee, snapshot_id, source,"
            " fee_model_version, traded_at) "
            "VALUES (%s,%s,%s,%s,'buy',%s,%s,%s,%s,0,%s,%s,%s,'ai','fee-cn-a-v1',%s)",
            (trade_id, order_id, PROJECT_ID, code, qty, price, amount, commission, transfer,
             total_fee, snap_id, at))
        cur.execute("INSERT INTO fin_position (project_id, code, qty, sellable_qty, avg_cost, opened_at) "
                    "VALUES (%s,%s,%s,0,%s,%s)", (PROJECT_ID, code, qty, price, at))

    # ── 三天收盘估值（净值 1.0000 → 1.1200 → 1.0500）──────────────────
    for day, nav, cash in (("2026-09-28", "1.000000", "100000"),
                           ("2026-09-29", "1.120000", "104990"),
                           ("2026-09-30", "1.050000", "103790")):
        total = (capital * Decimal(nav)).quantize(Decimal("0.0001"))
        market = (total - Decimal(cash)).quantize(Decimal("0.0001"))
        cur.execute(
            "INSERT INTO fin_valuation (project_id, as_of, cash_available, cash_frozen, market_value,"
            " total_assets, nav, price_source, quality, missing_flag) "
            "VALUES (%s,%s,%s,0,%s,%s,%s,'seed','ok',false)",
            (PROJECT_ID, _dt(day), Decimal(cash), market, total, Decimal(nav)))

    # ── 对账（09-30 通过）─────────────────────────────────────────────
    cur.execute(
        "INSERT INTO fin_recon_log (project_id, as_of, passed, checks) VALUES (%s,%s,true,%s)",
        (PROJECT_ID, _dt("2026-09-30"),
         psycopg2.extras.Json([{"name": "balance_equation", "expected": "105000",
                                "actual": "105000", "passed": True}])))
    cur.close()
    conn.commit()
    print(f"已写入种子项目 {PROJECT_ID}（{TIER} · 本金 {capital} · 2 笔成交 · 3 天估值）")


def main() -> int:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        seed(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
