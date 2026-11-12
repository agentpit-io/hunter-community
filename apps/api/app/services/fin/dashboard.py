"""智能炒股 · 四个正文页的读模型（M6）。

**只读**：全部 `SELECT`，一个 `INSERT`/`UPDATE` 都没有（写操作在 `control.py`）。
读的是账本表本身（`fin_valuation` / `fin_position` / `fin_cash_ledger` /
`fin_order` / `fin_trade` / `fin_snapshot`），与 M5 的 `report.py` 同一路数 ——
不另存一份「页面用的数」。

**每一个数字都要能指回它的来源**（`05 §3.2` M-17 的口径从报告页推广到全部页面）：

| 页面数字 | 来源 |
|---|---|
| 总资产 / 可用 / 冻结 / 持仓市值 / 净值 | `fin_valuation` 最后一行（paper 收盘时写下的） |
| 今日盈亏 | 最后两行 `fin_valuation` 之差（**不是**用当日均价之类的近似） |
| 持仓明细的现价 | `fin_snapshot` 里该代码最新一份快照 |
| 成交价 | `fin_trade.price`，并带 `snapshot_id` 作为凭证 |

**算不出就是 `None`**（前端渲染 `—`），绝不兜底成 0：净值曲线只有一行、没有上一行
时，`today` 整块返回 `None` 而不是 0.00% —— 那会读成一个结论。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

import psycopg2.extras

from app.services.market_time import market_tz, tz_name

SHANGHAI = market_tz("CN_A")
_SH_TZ = tz_name("CN_A")

# 持仓只数上限（页面「持仓概览」一屏放得下的量）。超过就截断并在返回体里说明，
# 不做无声截断（`05` 的口径：截了要说）。
POSITIONS_LIMIT = 50
RECENT_ACTIONS_LIMIT = 20


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _d(row: Optional[dict]) -> Optional[dict[str, Any]]:
    return {k: _jsonable(v) for k, v in row.items()} if row else None


# ── 项目 ──────────────────────────────────────────────────────────────────

def project(cur, project_id: str) -> Optional[dict]:
    cur.execute(
        """
        SELECT project_id, user_id, tier, status, initial_capital, currency,
               market_scope, version, run_mode, opened_at, closed_at, close_reason
          FROM fin_project WHERE project_id = %s
        """, (project_id,))
    return _d(cur.fetchone())


def param(cur, project_id: str) -> Optional[dict]:
    cur.execute(
        """
        SELECT project_id, board_flags, sector_prefs, liquidity_min_amount,
               liquidity_max_participation, strategies, hold_days_max,
               stop_loss_pct, take_profit_pct, max_positions, max_position_pct,
               min_order_amount, daily_max_new, daily_max_orders,
               daily_loss_halt_pct, account_drawdown_halt_pct,
               params_locked_at, updated_at, auto_enabled, risk_tier
          FROM fin_param WHERE project_id = %s
        """, (project_id,))
    return _d(cur.fetchone())


# ── 估值（总资产 / 净值 / 今日盈亏的唯一来源）──────────────────────────────

def valuations(cur, project_id: str, limit: int = 90, market: Optional[str] = None) -> list[dict]:
    """按时间倒序取估值行（最新的在前）。最后一行的 `total_assets` 是收盘口径的总资产。

    `market` 给了就只取该子账户（二期分市场）；不给取全部（一期语义）。
    """
    mf = " AND market = %s" if market else ""
    cur.execute(
        f"""
        SELECT as_of, cash_available, cash_frozen, market_value, total_assets,
               nav, price_source, quality, missing_flag, market, currency
          FROM fin_valuation WHERE project_id = %s{mf}
         ORDER BY as_of DESC LIMIT %s
        """, (project_id, *((market,) if market else ()), limit))
    return [_d(r) for r in cur.fetchall()]


def account_from_valuation(rows: list[dict]) -> dict[str, Any]:
    """把估值序列折成「账目 + 今日盈亏」。

    `equation.ok` 是**我们重新加一遍**的结果，不是信 `fin_valuation.total_assets`
    那一列 —— 页面上的「持仓市值 + 可用 + 冻结 = 总资产」要是能被自己验一遍，
    它才是证据；把库里存的总数直接显示出来只是搬运。
    """
    if not rows:
        return {
            "available": None, "frozen": None, "market_value": None,
            "total_assets": None, "nav": None, "as_of": None, "price_source": None,
            "quality": None, "market": None, "currency": None,
            "equation": None, "today": None, "series": [],
        }
    last = rows[0]
    available = last["cash_available"]
    frozen = last["cash_frozen"]
    market = last["market_value"]
    total = last["total_assets"]
    s = None
    if None not in (available, frozen, market):
        s = Decimal(str(available)) + Decimal(str(frozen)) + Decimal(str(market))
    equation = {
        "available": available, "frozen": frozen, "market_value": market,
        "sum": float(s) if s is not None else None,
        "total_assets": total,
        "ok": (s is not None and total is not None and s == Decimal(str(total))),
    }

    today = None
    if len(rows) >= 2:
        prev = rows[1]
        if prev["total_assets"] is not None and total is not None:
            pnl = Decimal(str(total)) - Decimal(str(prev["total_assets"]))
            base = Decimal(str(prev["total_assets"]))
            today = {
                "date": last["as_of"][:10],
                "prev_date": prev["as_of"][:10],
                "pnl": float(pnl),
                "pnl_pct": float(pnl / base) if base else None,
                "nav": last["nav"], "prev_nav": prev["nav"],
            }
    series = [{"date": r["as_of"][:10], "nav": r["nav"], "total_assets": r["total_assets"]}
              for r in reversed(rows)]
    return {
        "available": available, "frozen": frozen, "market_value": market,
        "total_assets": total, "nav": last["nav"], "as_of": last["as_of"],
        "price_source": last["price_source"], "quality": last["quality"],
        "market": last.get("market"), "currency": last.get("currency"),
        "equation": equation, "today": today, "series": series,
    }


# ── 持仓 ──────────────────────────────────────────────────────────────────

def positions_with_price(cur, project_id: str, market: Optional[str] = None) -> list[dict]:
    """持仓 + 每个代码最新一份快照价。

    现价取不到（没有快照）时 `last_price` / `market_value` / `pnl` 一律 `None` ——
    用成本价顶替会让「浮动盈亏 0」看起来像一个结论。

    `market` 给了就只取该子账户（二期分市场）；不给取全部（一期语义）。
    """
    mf = " AND p.market = %s" if market else ""
    cur.execute(
        f"""
        SELECT p.code, p.qty, p.sellable_qty, p.avg_cost, p.custody, p.opened_at,
               p.market, p.currency,
               i.name AS name, i.board AS board,
               s.last_price, s.snapshot_id, s.snapshot_time
          FROM fin_position p
          LEFT JOIN fin_instrument i ON i.code = p.code
          LEFT JOIN LATERAL (
                SELECT last_price, snapshot_id, snapshot_time
                  FROM fin_snapshot WHERE code = p.code
                 ORDER BY snapshot_time DESC LIMIT 1
          ) s ON true
         WHERE p.project_id = %s AND p.qty > 0{mf}
         ORDER BY p.code
        """, (project_id, *((market,) if market else ())))
    out = []
    for r in cur.fetchall():
        row = _d(r)
        qty = Decimal(str(row["qty"]))
        cost = Decimal(str(row["avg_cost"])) if row["avg_cost"] is not None else None
        price = Decimal(str(row["last_price"])) if row["last_price"] is not None else None
        mv = (qty * price) if price is not None else None
        pnl = ((price - cost) * qty) if (price is not None and cost is not None) else None
        pnl_pct = ((price - cost) / cost) if (price is not None and cost not in (None, Decimal(0))) else None
        row.update({
            "qty": int(qty),
            "market_value": float(mv) if mv is not None else None,
            "pnl": float(pnl) if pnl is not None else None,
            "pnl_pct": float(pnl_pct) if pnl_pct is not None else None,
        })
        out.append(row)
    return out


# ── 动作（委托与成交）─────────────────────────────────────────────────────

def recent_orders(cur, project_id: str, limit: int = RECENT_ACTIONS_LIMIT,
                  market: Optional[str] = None) -> list[dict]:
    mf = " AND o.market = %s" if market else ""
    cur.execute(
        f"""
        SELECT o.order_id, o.code, o.side, o.qty, o.price_type, o.limit_price,
               o.status, o.filled_qty, o.decline_reason, o.decision_ref,
               o.intent_ref, o.created_at, o.updated_at, o.market, o.currency,
               i.name AS name,
               t.trade_id, t.price AS trade_price, t.total_fee, t.snapshot_id,
               t.traded_at
          FROM fin_order o
          LEFT JOIN fin_instrument i ON i.code = o.code
          LEFT JOIN fin_trade t ON t.order_id = o.order_id
         WHERE o.project_id = %s{mf}
         ORDER BY o.created_at DESC, t.traded_at DESC
         LIMIT %s
        """, (project_id, *((market,) if market else ()), limit))
    return [_d(r) for r in cur.fetchall()]


def orders_on_date(cur, project_id: str, day: str, market: Optional[str] = None) -> list[dict]:
    """某一天（上海时间）产生的动作 —— 「它今天做了什么」。`market` 给了就只取该子账户。"""
    mf = " AND o.market = %s" if market else ""
    cur.execute(
        f"""
        SELECT o.order_id, o.code, o.side, o.qty, o.price_type, o.status,
               o.filled_qty, o.decline_reason, o.decision_ref, o.intent_ref,
               o.created_at, o.market, o.currency, o.limit_price,
               i.name AS name,
               t.trade_id, t.price AS trade_price, t.total_fee, t.snapshot_id
          FROM fin_order o
          LEFT JOIN fin_instrument i ON i.code = o.code
          LEFT JOIN fin_trade t ON t.order_id = o.order_id
         WHERE o.project_id = %s
           AND (o.created_at AT TIME ZONE '{_SH_TZ}')::date = %s{mf}
         ORDER BY o.created_at
        """, (project_id, day, *((market,) if market else ())))
    return [_d(r) for r in cur.fetchall()]


def trades(cur, project_id: str, limit: int = 50, market: Optional[str] = None) -> list[dict]:
    mf = " AND t.market = %s" if market else ""
    cur.execute(
        f"""
        SELECT t.trade_id, t.order_id, t.code, t.side, t.qty, t.price, t.amount,
               t.commission, t.stamp_tax, t.transfer_fee, t.total_fee,
               t.snapshot_id, t.source, t.fee_model_version, t.traded_at,
               t.market, t.currency,
               i.name AS name,
               s.snapshot_time, s.source AS snapshot_source, s.last_price AS snapshot_price,
               s.bid1_price, s.ask1_price, s.quality AS snapshot_quality
          FROM fin_trade t
          LEFT JOIN fin_instrument i ON i.code = t.code
          LEFT JOIN fin_snapshot s ON s.snapshot_id = t.snapshot_id
         WHERE t.project_id = %s{mf}
         ORDER BY t.traded_at DESC
         LIMIT %s
        """, (project_id, *((market,) if market else ()), limit))
    return [_d(r) for r in cur.fetchall()]


def snapshot(cur, snapshot_id: str) -> Optional[dict]:
    """快照凭证（成交价依据）。成交里的 `snapshot_id` 直接查它，能对上就是证据。"""
    cur.execute(
        """
        SELECT snapshot_id, code, snapshot_time, source, last_price,
               bid1_price, bid1_volume, ask1_price, ask1_volume, prev_close,
               quality, missing_flag, raw_ref, created_at
          FROM fin_snapshot WHERE snapshot_id = %s
        """, (snapshot_id,))
    return _d(cur.fetchone())


def used_snapshot_ids(cur, project_id: str) -> dict[str, int]:
    """这个项目用过的快照编号 → 用了几次（成交条数）。凭证页据此标注「本账户用过」。"""
    cur.execute(
        """
        SELECT snapshot_id, count(*) AS n FROM fin_trade
         WHERE project_id = %s GROUP BY snapshot_id
        """, (project_id,))
    return {r["snapshot_id"]: int(r["n"]) for r in cur.fetchall()}


def cash_entries(cur, project_id: str, limit: int = 50, market: Optional[str] = None) -> list[dict]:
    mf = " AND market = %s" if market else ""
    cur.execute(
        f"""
        SELECT entry_id, kind, amount, available_after, frozen_after, memo, created_at,
               market, currency
          FROM fin_cash_ledger WHERE project_id = %s{mf}
         ORDER BY entry_id DESC LIMIT %s
        """, (project_id, *((market,) if market else ()), limit))
    return [_d(r) for r in cur.fetchall()]


def param_change_log(cur, project_id: str, limit: int = 50) -> list[dict]:
    """参数变更日志（谁、何时、改前改后）。页面与验收都用它作为「改过什么」的证据。"""
    cur.execute(
        """
        SELECT id, actor, field, old_value, new_value, changed_at
          FROM fin_param_change_log WHERE project_id = %s
         ORDER BY changed_at DESC, id DESC LIMIT %s
        """, (project_id, limit))
    out = []
    for r in cur.fetchall():
        row = _d(r)
        for k in ("old_value", "new_value"):
            if isinstance(row.get(k), dict) and set(row[k]) == {"v"}:
                row[k] = row[k]["v"]      # psycopg2 的 Json 包了一层
        out.append(row)
    return out


def recon_latest(cur, project_id: str) -> Optional[dict]:
    cur.execute(
        """
        SELECT as_of, passed, checks FROM fin_recon_log
         WHERE project_id = %s ORDER BY as_of DESC LIMIT 1
        """, (project_id,))
    return _d(cur.fetchone())


def jobs(cur, project_id: str, limit: int = 20) -> list[dict]:
    """业务长任务（`fin_job`）—— 「自动运行到哪一步了」的权威记录。"""
    cur.execute(
        """
        SELECT job_id, type AS job_type, status, params, result_ref, checkpoint,
               created_at, updated_at
          FROM fin_job WHERE project_id = %s
         ORDER BY created_at DESC LIMIT %s
        """, (project_id, limit))
    return [_d(r) for r in cur.fetchall()]
