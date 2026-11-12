"""M6 验收种子数据 —— 为四个正文页构造一个**自洽**的模拟账本场景。

⚠️ 这不是产品功能，是**验收夹具**（与 M4 的 `prj_m4test`、M5 的 `prj_m5demo` 同性质）。
它往 `fin_*` 表写一个专用项目 `prj_m6demo`：7 笔成交、5 只持仓、1 张未成交限价买单、
14 个交易日的收盘估值、两个交易日的报告。

**它守的纪律**（仓内铁律「不许编数字」的落地形式）：

1. **每一行的算法都照抄 `apps/paper` 的实现** —— 费用公式取自 `paper/app/risk/fee.py`，
   现金流水的四条（`freeze` / `buy` / `fee` / `sell` / `tax`）语义取自
   `paper/app/ledger.py` 的 `settle_buy` / `settle_sell`，持仓加权成本取自
   `apply_position`。写完之后**必须过 paper 自己的对账**（`POST /projects/{id}/recon`，
   八项检查），对账不平就是夹具写错了。
2. **页面上的每个数字都是从这些行算出来的**，不是脚本先算好再写死 —— 脚本只负责
   把账本摆成「发生过这些事」的样子。
3. 快照的 `source` 写 `seed-m6`：**页面上的凭证会如实显示这个来源**，
   不会假装它是交易所实时行情。

用法（在 api 容器里跑，或本机带 DATABASE_URL）::

    cd apps/api && PYTHONPATH=. python scripts/seed_m6_demo.py

幂等：每次先清掉 `prj_m6demo` 自己的行再重建。
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

from app.services.fin import tiers  # noqa: E402

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://hunter:hunter@localhost:5432/hunter")
from app.services.market_time import market_tz
SH = market_tz("CN_A")

PROJECT_ID = "prj_m6demo"
# 单用户模式下的本机账户（`/auth/local-session` 按 email_lower 找它）。**预置固定 UUID**，
# 这样浏览器登录（不需要密码）拿到的就是拥有这份账本的那个用户。
USER_ID = "22222222-3333-4444-8555-666666666666"
USER_EMAIL = "local@hunter.local"
TIER = "operate"                 # ③ 资产运营 · 本金 100 万（与原型页同一档）
Q = Decimal("0.0001")

# 14 个交易日（2026-09-11 ~ 09-30，跳过周末；10-01 起国庆休市）
DAYS = ["2026-09-11", "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17",
        "2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24",
        "2026-09-25", "2026-09-28", "2026-09-29", "2026-09-30"]

# 标的元数据（真实 A 股代码与简称；板块用于页面显示）
INSTRUMENTS = [
    ("600519", "贵州茅台", "SH", "main"),
    ("002594", "比亚迪", "SZ", "main"),
    ("300750", "宁德时代", "SZ", "chinext"),
    ("600036", "招商银行", "SH", "main"),
    ("601318", "中国平安", "SH", "main"),
    ("300308", "中际旭创", "SZ", "chinext"),
    ("600900", "长江电力", "SH", "main"),
]

# 价格路径：每个代码一个起点与终点，中间走一条确定性的平滑路径（不含随机数）。
# 终点 = 最新一份快照价 = 页面上的「现价」。
PATHS: dict[str, tuple[float, float]] = {
    "600036": (34.80, 38.15),
    "601318": (46.00, 49.05),
    "600519": (1640.00, 1724.50),
    "002594": (255.00, 275.60),
    "300750": (190.00, 178.90),
    "300308": (400.00, 440.20),
}

# 成交计划：(日索引, 代码, 方向, 股数, 时刻)
TRADES = [
    (2,  "300308", "buy",  300,  "09:30"),
    (9,  "600036", "buy",  3000, "10:05"),
    (10, "300750", "buy",  600,  "09:40"),
    (10, "601318", "buy",  2000, "10:20"),
    (11, "600519", "buy",  100,  "09:30"),
    (12, "002594", "buy",  300,  "11:02"),
    (13, "300308", "sell", 300,  "10:15"),
]

# 一张挂着没成交的限价买单（页面「冻结资金」的来源）。冻的是 限价 × 数量。
PENDING = ("600900", "buy", 1000, "18.60", 13, "14:40")


def _money(v) -> Decimal:
    return Decimal(str(v)).quantize(Q, rounding=ROUND_HALF_UP)


def _price_path(start: float, end: float) -> list[Decimal]:
    """确定性路径：线性插值 + 一个 ±0.6% 的小波动，**终点严格等于 end**。"""
    n = len(DAYS)
    out = []
    for i in range(n):
        t = i / (n - 1)
        base = start + (end - start) * t
        wig = 0.006 * (((i * 7) % 5) - 2)
        out.append(_money(round(base * (1 + wig), 2)))
    out[-1] = _money(end)
    return out


def _fee(side: str, amount: Decimal) -> dict:
    """照抄 `paper/app/risk/fee.py` 的 A 股口径。"""
    commission = max(_money(5.00), _money(amount * Decimal("0.00025")))
    stamp = _money(amount * Decimal("0.0005")) if side == "sell" else Decimal("0.0000")
    transfer = _money(amount * Decimal("0.00001"))
    return {"commission": commission, "stamp_tax": stamp, "transfer_fee": transfer,
            "total": _money(commission + stamp + transfer)}


def _dt(day: str, hm: str) -> datetime:
    hh, mm = (int(x) for x in hm.split(":"))
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hh, mm, tzinfo=SH)


def seed(conn) -> None:
    tpl = tiers.build_template(TIER)
    capital = _money(tpl["initial_capital"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # ── 清掉上一次（只动本夹具自己的行；外键顺序：子表在前）──────────────
    cur.execute("DELETE FROM hunter_artifacts.published_artifact "
                "WHERE source_message_id LIKE %s", (f"fin-report:{PROJECT_ID}:%",))
    for table in ("fin_report_fact", "fin_publish_receipt"):
        cur.execute(f"DELETE FROM {table} WHERE report_id IN "
                    "(SELECT report_id FROM fin_report WHERE project_id=%s)", (PROJECT_ID,))
    # 删除顺序 = 外键依赖的逆序：cash_ledger 引用 trade 与 order，trade 引用 order。
    for table in ("fin_report", "fin_cash_ledger", "fin_trade", "fin_valuation",
                  "fin_recon_log", "fin_position", "fin_order", "fin_param",
                  "fin_param_change_log"):
        cur.execute(f"DELETE FROM {table} WHERE project_id = %s", (PROJECT_ID,))
    cur.execute("DELETE FROM fin_project WHERE project_id = %s", (PROJECT_ID,))
    cur.execute("DELETE FROM fin_account WHERE account_id = 'acct_m6demo'")
    # 该用户可能被向导开过别的项目 —— 一并清掉，否则撞 `fin_project_one_active`
    # （这是验收机上的夹具账户，不存在真实数据；见文件头「这不是产品功能」）。
    cur.execute("DELETE FROM fin_project WHERE user_id = %s", (USER_ID,))
    cur.execute("DELETE FROM fin_param WHERE project_id NOT IN (SELECT project_id FROM fin_project)")
    cur.execute("DELETE FROM fin_account WHERE user_id = %s", (USER_ID,))
    # 快照是全局的（按代码），重复跑时覆盖价格而不是报冲突 —— 但本夹具自己写的那些先清掉。
    cur.execute("DELETE FROM fin_trade WHERE snapshot_id IN "
                "(SELECT snapshot_id FROM fin_snapshot WHERE source = 'seed-m6')")
    cur.execute("DELETE FROM fin_snapshot WHERE source = 'seed-m6'")

    # ── 用户（单用户模式的本机账户）──────────────────────────────────────
    cur.execute(
        "INSERT INTO users (id, email, email_lower, pw_hash, role, status, display_name) "
        "VALUES (%s,%s,%s,'x-placeholder','admin','active','本机用户') "
        "ON CONFLICT (id) DO UPDATE SET email_lower = EXCLUDED.email_lower",
        (USER_ID, USER_EMAIL, USER_EMAIL))

    cur.execute("INSERT INTO fin_account (account_id, user_id, locked_at) VALUES (%s,%s,now())",
                ("acct_m6demo", USER_ID))
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

    # ── 标的元数据 ───────────────────────────────────────────────────────
    for code, name, exch, board in INSTRUMENTS:
        limit = Decimal("0.20") if board == "chinext" else Decimal("0.10")
        cur.execute(
            """
            INSERT INTO fin_instrument (code, name, exchange, board, is_st,
                   limit_up_pct, limit_down_pct, lot_size, is_active, source)
            VALUES (%s,%s,%s,%s,false,%s,%s,100,true,'seed-m6')
            ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name,
                   exchange = EXCLUDED.exchange, board = EXCLUDED.board
            """, (code, name, exch, board, limit, limit))

    # ── 现金流水（按时间顺序，available/frozen 逐笔推）───────────────────
    avail = capital
    frozen = Decimal("0.0000")
    # 每条流水带一个「属于第几个交易日」的下标 —— 每日收盘估值要用它挑出当天结束时的
    # 现金状态（成交当天就生效，不是次日）。
    ledger: list[tuple] = [
        ("deposit", capital, _money(avail), _money(frozen), None, None, "开户入金", 0),
    ]

    def add(kind, amount, oid, tid, memo, day_idx):
        nonlocal avail, frozen
        ledger.append((kind, _money(amount), _money(avail), _money(frozen), oid, tid, memo, day_idx))

    paths = {code: _price_path(*PATHS[code]) for code in PATHS}
    # 每只票「哪一天第一次有价」——买入日之前不产生快照。
    first_day: dict[str, int] = {}
    for idx, code, _side, _qty, _hm in TRADES:
        first_day[code] = min(first_day.get(code, idx), idx)

    trade_rows: list[tuple] = []
    qty_at: dict[str, int] = {}
    cost_at: dict[str, Decimal] = {}
    seq = 0

    for idx, code, side, qty, hm in TRADES:
        day = DAYS[idx]
        price = paths[code][idx]
        amount = _money(price * qty)
        fee = _fee(side, amount)
        at = _dt(day, hm)
        seq += 1
        order_id = f"ord_m6_{seq}"
        trade_id = f"trd_m6_{seq}"
        snap_id = f"SNAP-{day.replace('-','')}-{hm.replace(':','')}00-{code}"

        # 快照：成交时刻那一份（成交价的凭证）
        cur.execute(
            "INSERT INTO fin_snapshot (snapshot_id, code, snapshot_time, source, last_price,"
            " prev_close, quality, missing_flag) VALUES (%s,%s,%s,'seed-m6',%s,%s,'ok',false)"
            " ON CONFLICT (snapshot_id) DO UPDATE SET last_price = EXCLUDED.last_price",
            (snap_id, code, at, price, paths[code][max(0, idx - 1)]))

        if side == "buy":
            need = _money(amount + fee["total"])
            # 受理挂单：可用 → 冻结（纸面口径见 paper/app/ledger.py 的 freeze_cash）。
            # 成交再按快照价从冻结里扣：`amount` 与费用两条各扣一次，冻结正好回到原值。
            # 漏掉减 `avail` 这一步的话，账面上「现金总额」不变而持仓凭空多出来，
            # 对账第 1/2 项会当场不平。
            avail -= need
            frozen += need
            add("freeze", Decimal("0.0000"), order_id, None, f"受理买单冻结 {code}", idx)
            frozen -= amount
            add("buy", -amount, order_id, trade_id, "买入成交", idx)
            frozen -= fee["total"]
            add("fee", -fee["total"], order_id, trade_id, "买入费用", idx)
            status, filled = "filled", qty
            qty_at[code] = qty_at.get(code, 0) + qty
            cost_at[code] = price
        else:
            # 卖出：成交额先进可用（paper/app/ledger.py 的 settle_sell），费用再从可用扣。
            avail += amount
            add("sell", amount, order_id, trade_id, "卖出成交", idx)
            avail -= _money(fee["commission"] + fee["transfer_fee"])
            add("fee", -_money(fee["commission"] + fee["transfer_fee"]), order_id, trade_id,
                "卖出费用", idx)
            avail -= fee["stamp_tax"]
            add("tax", -fee["stamp_tax"], order_id, trade_id, "卖出印花税", idx)
            status, filled = "filled", qty
            qty_at[code] = qty_at.get(code, 0) - qty

        cur.execute(
            "INSERT INTO fin_order (order_id, project_id, code, side, qty, price_type, status,"
            " filled_qty, source, actor, decision_ref, valid_until, frozen_amount, created_at, updated_at)"
            " VALUES (%s,%s,%s,%s,%s,'market',%s,%s,'ai','system',%s,%s,0,%s,%s)",
            (order_id, PROJECT_ID, code, side, qty, status, filled, f"sample-fixed:{day}:{hm}", at, at, at))
        cur.execute(
            "INSERT INTO fin_trade (trade_id, order_id, project_id, code, side, qty, price, amount,"
            " commission, stamp_tax, transfer_fee, total_fee, snapshot_id, source,"
            " fee_model_version, traded_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ai','fee-cn-a-v1',%s)",
            (trade_id, order_id, PROJECT_ID, code, side, qty, price, amount,
             fee["commission"], fee["stamp_tax"], fee["transfer_fee"], fee["total"], snap_id, at))
        trade_rows.append((idx, code, side, qty, price, at))

    # ── 持仓 ─────────────────────────────────────────────────────────────
    first_buy_day: dict[str, int] = {}
    for idx, code, side, _q, _hm in TRADES:
        if side == "buy":
            first_buy_day[code] = min(first_buy_day.get(code, idx), idx)
    for code in paths:
        first_buy_day.setdefault(code, 0)
    all_codes = sorted(set(qty_at) | set(paths))
    for code in all_codes:
        q = qty_at.get(code, 0)
        cost = cost_at.get(code, paths[code][0])
        opened = _dt(DAYS[first_buy_day.get(code, 0)], "09:30")
        cur.execute(
            "INSERT INTO fin_position (project_id, code, qty, sellable_qty, avg_cost, opened_at)"
            " VALUES (%s,%s,%s,%s,%s,%s)", (PROJECT_ID, code, q, q, cost, opened))

    # ── 挂着没成交的限价买单（冻结资金）──────────────────────────────────
    p_code, p_side, p_qty, p_price, p_idx, p_hm = PENDING
    p_price = Decimal(p_price)
    p_need = _money(p_price * p_qty)
    p_at = _dt(DAYS[p_idx], p_hm)
    avail -= p_need
    frozen += p_need
    add("freeze", Decimal("0.0000"), "ord_m6_pending", None, f"受理限价买单冻结 {p_code}", p_idx)
    cur.execute(
        "INSERT INTO fin_order (order_id, project_id, code, side, qty, price_type, limit_price,"
        " status, filled_qty, source, actor, valid_until, frozen_amount, created_at, updated_at)"
        " VALUES ('ord_m6_pending',%s,%s,'buy',%s,'limit',%s,'accepted',0,'ai','system',%s,%s,%s,%s)",
        (PROJECT_ID, p_code, p_qty, p_price, _dt(DAYS[p_idx], "15:00"), p_need, p_at, p_at))

    # ── 现金流水落库 ─────────────────────────────────────────────────────
    for kind, amount, av, fr, oid, tid, memo, day_idx in ledger:
        cur.execute(
            "INSERT INTO fin_cash_ledger (project_id, kind, amount, available_after, frozen_after,"
            " order_id, trade_id, memo, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (PROJECT_ID, kind, amount, av, fr, oid, tid, memo,
             _dt(DAYS[day_idx], "15:00")))

    # ── 每日收盘估值 ─────────────────────────────────────────────────────
    # 从前一天起逐日推：某天收盘时的持仓 × 当天价格 + 当天收盘时的现金。
    # 用「逐日重算」而不是写死一条曲线 —— 曲线是算出来的，不是编出来的。
    cash_by_day = _cash_by_day(ledger, capital)
    held: dict[str, int] = {}
    for i, day in enumerate(DAYS):
        for idx, code, side, qty, _price, _at in trade_rows:
            if idx != i:
                continue
            held[code] = held.get(code, 0) + (qty if side == "buy" else -qty)
        held = {c: q for c, q in held.items() if q != 0}
        mv = sum((paths[c][i] * q for c, q in held.items()), Decimal("0.0000"))
        av, fr = cash_by_day[i]
        total = _money(av + fr + mv)
        nav = (total / capital).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        cur.execute(
            "INSERT INTO fin_valuation (project_id, as_of, cash_available, cash_frozen,"
            " market_value, total_assets, nav, price_source, quality, missing_flag)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,'fin_snapshot','ok',false)",
            (PROJECT_ID, _dt(day, "15:30"), _money(av), _money(fr), _money(mv), total, nav))

    # 每个代码每天 15:30 的收盘快照（最新一份 = 页面上的「现价」）
    for code, path in paths.items():
        for i, day in enumerate(DAYS):
            if i < first_day.get(code, 0):
                continue
            sid = f"SNAP-{day.replace('-','')}-153000-{code}"
            cur.execute(
                "INSERT INTO fin_snapshot (snapshot_id, code, snapshot_time, source, last_price,"
                " prev_close, quality, missing_flag) VALUES (%s,%s,%s,'seed-m6',%s,%s,'ok',false)"
                " ON CONFLICT (snapshot_id) DO UPDATE SET last_price = EXCLUDED.last_price",
                (sid, code, _dt(day, "15:30"), path[i], path[max(0, i - 1)]))

    # ── 版本号 = 成交笔数（对账第 6 项）──────────────────────────────────
    cur.execute("UPDATE fin_project SET version = %s WHERE project_id = %s",
                (len(trade_rows), PROJECT_ID))
    cur.close()
    conn.commit()
    print(f"已写入 {PROJECT_ID}（{TIER} · 本金 {capital} · {len(trade_rows)} 笔成交 · "
          f"{len(paths)} 只标的 · {len(DAYS)} 天估值）")
    print(f"  可用 {_money(avail)} · 冻结 {_money(frozen)}")


def _cash_by_day(ledger, capital) -> list[tuple[Decimal, Decimal]]:
    """每个交易日**收盘时**的 (可用, 冻结)。

    流水按时间顺序生成、每条都带它属于第几个交易日，所以这里只是「取当天最后一条
    的状态」——当天没有流水的日子沿用前一天（现金不会自己变）。
    """
    out: list[tuple[Decimal, Decimal]] = []
    av, fr = capital, Decimal("0.0000")
    by_day: dict[int, tuple[Decimal, Decimal]] = {}
    for kind, _amount, a, f, _oid, _tid, _memo, day_idx in ledger:
        by_day[day_idx] = (a, f)
    for i in range(len(DAYS)):
        if i in by_day:
            av, fr = by_day[i]
        out.append((av, fr))
    return out


def main() -> int:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        seed(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
