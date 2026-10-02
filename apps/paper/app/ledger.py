"""唯一账本的记账层：只 `INSERT`，从不 `UPDATE` 历史。

`fin_cash_ledger` 的字段语义（这是全模块的契约，改之前先读完）：

| kind | amount | 可用 | 冻结 |
|---|---|---|---|
| `deposit`  | **+** 入金 | +amount | — |
| `freeze`   | **0**（钱在可用↔冻结之间搬，现金总额不变） | −need | +need |
| `unfreeze` | **0** | +need | −need |
| `buy`      | **−** 成交金额 | — | −amount |
| `sell`     | **+** 成交金额 | +amount | — |
| `fee`      | **−** 费用 | 卖出时扣可用 / 买入时扣冻结 | — |
| `tax`      | **−** 印花税 | −tax | — |

两条由此成立、并被对账复核的不变量（`recon.py`）：

1. `Σ(amount) == 可用 + 冻结`（现金总额 = 流水金额之和）；
2. 同一 `trade_id` 的流水金额之和 == 那笔成交的现金影响
   （买 = `−(金额+费用)`；卖 = `+(金额−费用)`）。

**余额不存字段**：可用 / 冻结是流水**最后一行的 `*_after`**。「存一份余额」等于多一个
可能和流水不一致的权威，是账本不平的经典来源（`09 §4.5` 底注）。

M3 起有挂单，于是买入分两种走法：

| 情形 | 流水 |
|---|---|
| 受理即成交 | `freeze → buy → fee [→ unfreeze]`（`unfreeze` 只在成交价低于限价、有多冻时出现） |
| 受理后挂单 | `freeze` 一条，钱留在冻结里；成交时再走 `buy → fee [→ unfreeze]`，撤单时走 `unfreeze` |

卖出仍是 `sell → fee → tax` 三条 —— 券的占用**不建账本列**，它由未成交卖单
（`fin_order`）算出来，见 `open_sell_committed`。

四步链路（取快照 → 过规则 → 撮合 → 记账）在 `app/matching/engine.py`，
本模块只负责「怎么在账本里落下来」。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import psycopg2.extras

# 上海时间（中国无夏令时，固定 +08:00；与 apps/api/main.py 同口径）。
CST = timezone(timedelta(hours=8))


# ── 小工具 ────────────────────────────────────────────────────────────────

def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.0001"))


def _fetchone(cur, sql: str, params: tuple = ()) -> Optional[dict]:
    cur.execute(sql, params)
    return cur.fetchone()


# ── 读（七类实体）───────────────────────────────────────────────────────

def get_project(cur, project_id: str) -> Optional[dict]:
    return _fetchone(
        cur,
        """
        SELECT project_id, user_id, tier, status, initial_capital, currency,
               market_scope, version, run_mode, opened_at, closed_at, close_reason
          FROM fin_project WHERE project_id = %s
        """,
        (project_id,),
    )


def get_param(cur, project_id: str) -> Optional[dict]:
    return _fetchone(cur, "SELECT * FROM fin_param WHERE project_id = %s", (project_id,))


def cash_balance(cur, project_id: str) -> tuple[Decimal, Decimal]:
    """可用 / 冻结 = 流水最后一行。没有流水 → (0, 0)（不是「余额字段」）。"""
    row = _fetchone(
        cur,
        """
        SELECT available_after, frozen_after FROM fin_cash_ledger
         WHERE project_id = %s ORDER BY entry_id DESC LIMIT 1
        """,
        (project_id,),
    )
    if not row:
        return Decimal("0.0000"), Decimal("0.0000")
    return row["available_after"], row["frozen_after"]


def get_position(cur, project_id: str, code: str) -> Optional[dict]:
    return _fetchone(
        cur,
        "SELECT * FROM fin_position WHERE project_id = %s AND code = %s",
        (project_id, code),
    )


def list_positions(cur, project_id: str) -> list[dict]:
    cur.execute(
        "SELECT * FROM fin_position WHERE project_id = %s ORDER BY code", (project_id,)
    )
    return cur.fetchall()


def list_cash(cur, project_id: str, limit: int = 200) -> list[dict]:
    cur.execute(
        """
        SELECT entry_id, kind, amount, available_after, frozen_after, order_id, trade_id, memo, created_at
          FROM fin_cash_ledger WHERE project_id = %s ORDER BY entry_id DESC LIMIT %s
        """,
        (project_id, limit),
    )
    return cur.fetchall()


def list_orders(cur, project_id: str, limit: int = 200) -> list[dict]:
    cur.execute(
        """
        SELECT order_id, code, side, qty, price_type, limit_price, status, filled_qty,
               source, actor, decline_reason, valid_until, frozen_amount, created_at
          FROM fin_order WHERE project_id = %s ORDER BY created_at DESC LIMIT %s
        """,
        (project_id, limit),
    )
    return cur.fetchall()


def list_trades(cur, project_id: str, limit: int = 200) -> list[dict]:
    cur.execute(
        """
        SELECT trade_id, order_id, code, side, qty, price, amount, commission, stamp_tax,
               transfer_fee, total_fee, snapshot_id, source, fee_model_version, traded_at
          FROM fin_trade WHERE project_id = %s ORDER BY traded_at DESC LIMIT %s
        """,
        (project_id, limit),
    )
    return cur.fetchall()


# ── 参考数据 ──────────────────────────────────────────────────────────────

def get_calendar(cur, trade_date) -> Optional[dict]:
    return _fetchone(
        cur, "SELECT * FROM fin_market_calendar WHERE trade_date = %s", (trade_date,)
    )


def get_instrument(cur, code: str) -> Optional[dict]:
    return _fetchone(cur, "SELECT * FROM fin_instrument WHERE code = %s", (code,))


def get_fee_model(cur, version: Optional[str] = None) -> Optional[dict]:
    if version:
        return _fetchone(cur, "SELECT * FROM fin_fee_model WHERE version = %s", (version,))
    return _fetchone(
        cur,
        "SELECT * FROM fin_fee_model ORDER BY effective_from DESC, version DESC LIMIT 1",
    )


def latest_snapshot(cur, code: str) -> Optional[dict]:
    return _fetchone(
        cur,
        """
        SELECT snapshot_id, code, snapshot_time, source, last_price, prev_close, quality, missing_flag
          FROM fin_snapshot WHERE code = %s ORDER BY snapshot_time DESC LIMIT 1
        """,
        (code,),
    )


# ── 写：入金（幂等）───────────────────────────────────────────────────────

def seed_funding(cur, project_id: str) -> dict:
    """把项目本金记成一条 `deposit` 流水。**幂等**：已有 deposit 就原样返回。

    这是「账本从哪里开始」的显式一步 —— 不做隐式补记（隐藏的写会让对账说不清）。
    """
    project = get_project(cur, project_id)
    if not project:
        raise LookupError(f"项目不存在：{project_id}")
    existing = _fetchone(
        cur,
        "SELECT entry_id, amount FROM fin_cash_ledger WHERE project_id = %s AND kind = 'deposit' LIMIT 1",
        (project_id,),
    )
    if existing:
        return {"created": False, "entry_id": existing["entry_id"], "amount": existing["amount"]}

    capital = _money(project["initial_capital"])
    entry_id = _insert_cash(
        cur, project_id, "deposit", capital, capital, Decimal("0.0000"), memo="项目本金"
    )
    return {"created": True, "entry_id": entry_id, "amount": capital}


def _insert_cash(
    cur,
    project_id: str,
    kind: str,
    amount: Decimal,
    available_after: Decimal,
    frozen_after: Decimal,
    order_id: Optional[str] = None,
    trade_id: Optional[str] = None,
    memo: Optional[str] = None,
) -> int:
    cur.execute(
        """
        INSERT INTO fin_cash_ledger
          (project_id, kind, amount, available_after, frozen_after, order_id, trade_id, memo)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING entry_id
        """,
        (project_id, kind, _money(amount), _money(available_after), _money(frozen_after),
         order_id, trade_id, memo),
    )
    return cur.fetchone()["entry_id"]


# ── 写：委托、成交、现金、持仓（M3 起的四步链路的下游）─────────────────────
#
# 四步链路本身在 `app/matching/engine.py`（取快照 → 过规则 → 撮合 → 记账）。
# 本层只负责「怎么在账本里落下来」，且只管三种现金动作：
#
#   · `freeze_cash`   —— 受理挂单时把钱从可用搬到冻结（现金总额不变，amount = 0）；
#   · `settle_buy`    —— 成交：冻结里付出成交额与费用，多冻的部分退回可用；
#   · `unfreeze_cash` —— 撤单 / 过期：把冻着的钱原样搬回可用。
#
# `fin_order.frozen_amount` 记着「这笔委托受理时冻了多少」，解冻与退款都以它为准 ——
# 不靠「重算一遍费用模型」去推（费用模型改过版本就推不出来了）。

def get_order(cur, order_id: str) -> Optional[dict]:
    return _fetchone(cur, "SELECT * FROM fin_order WHERE order_id = %s", (order_id,))


def list_open_orders(cur, project_id: str, code: Optional[str] = None) -> list[dict]:
    sql = (
        "SELECT * FROM fin_order WHERE project_id = %s "
        "AND status IN ('pending','accepted','partially_filled')"
    )
    params: list = [project_id]
    if code:
        sql += " AND code = %s"
        params.append(code)
    sql += " ORDER BY created_at"
    cur.execute(sql, tuple(params))
    return cur.fetchall()


def open_sell_committed(cur, project_id: str, code: str) -> int:
    """该标的上**还没成交的卖单**占用的股数。

    一期的「券的冻结」不建账本列 —— 它可以从 `fin_order` 精确算出来，
    而 `fin_order` 本来就是权威。风控第 2 条（T+1）与第 6 条（持仓）要拿
    `可卖量 − 这个数` 去判，否则同一批股能被两张挂单各卖一次。
    """
    row = _fetchone(
        cur,
        """
        SELECT COALESCE(SUM(qty - filled_qty), 0) AS n FROM fin_order
         WHERE project_id = %s AND code = %s AND side = 'sell'
           AND status IN ('pending','accepted','partially_filled')
        """,
        (project_id, code),
    )
    return int(row["n"]) if row else 0


def open_buy_frozen(cur, project_id: str) -> Decimal:
    """还没成交的买单占用的冻结额之和。对账用它核「冻结 = 挂单占用」。"""
    row = _fetchone(
        cur,
        """
        SELECT COALESCE(SUM(frozen_amount), 0) AS s FROM fin_order
         WHERE project_id = %s AND side = 'buy'
           AND status IN ('pending','accepted','partially_filled')
        """,
        (project_id,),
    )
    return _money(row["s"]) if row else Decimal("0.0000")


def insert_order(
    cur, order_id, project_id, code, side, qty, price_type, limit_price,
    status, filled_qty, source, actor, decline_reason, intent_ref,
    decision_ref, valid_until, frozen_amount=Decimal("0.0000"),
) -> None:
    cur.execute(
        """
        INSERT INTO fin_order
          (order_id, project_id, code, side, qty, price_type, limit_price, status,
           filled_qty, source, actor, intent_ref, decline_reason, decision_ref,
           valid_until, frozen_amount)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            order_id, project_id, code, side, qty, price_type,
            None if limit_price is None else _money(limit_price),
            status, filled_qty, source, actor,
            None if intent_ref is None else psycopg2.extras.Json(intent_ref),
            decline_reason, decision_ref, valid_until, _money(frozen_amount),
        ),
    )


def update_order_filled(cur, order_id: str, status: str, filled_qty: int,
                        decline_reason: Optional[str] = None) -> None:
    """只动 `fin_order` 的**状态列**（0023 的 GRANT 注释：UPDATE 只用于状态列）。"""
    cur.execute(
        "UPDATE fin_order SET status = %s, filled_qty = %s, "
        "decline_reason = COALESCE(%s, decline_reason), updated_at = now() "
        "WHERE order_id = %s",
        (status, filled_qty, decline_reason, order_id),
    )


def insert_trade(cur, trade_id, order_id, project_id, code, side, qty, price, amount,
                 fee, snapshot_id, source, fee_version, traded_at) -> None:
    cur.execute(
        """
        INSERT INTO fin_trade
          (trade_id, order_id, project_id, code, side, qty, price, amount, commission,
           stamp_tax, transfer_fee, total_fee, snapshot_id, source, fee_model_version, traded_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            trade_id, order_id, project_id, code, side, qty, _money(price), amount,
            fee.commission, fee.stamp_tax, fee.transfer_fee, fee.total,
            snapshot_id, source, fee_version, traded_at,
        ),
    )


# ── 现金三动作 ────────────────────────────────────────────────────────────

def freeze_cash(cur, project_id: str, order_id: str, need: Decimal, memo: str) -> None:
    """可用 → 冻结。现金总额不变，所以 `amount = 0`（见文件头的流水语义表）。"""
    available, frozen = cash_balance(cur, project_id)
    _insert_cash(cur, project_id, "freeze", Decimal("0.0000"),
                 _money(available - need), _money(frozen + need), order_id, None, memo)


def unfreeze_cash(cur, project_id: str, order_id: str, amount: Decimal, memo: str) -> None:
    """冻结 → 可用。`amount ≤ 0` 时不动账（撤一张没冻过钱的单不该产生流水）。"""
    if amount is None or _money(amount) <= 0:
        return
    available, frozen = cash_balance(cur, project_id)
    _insert_cash(cur, project_id, "unfreeze", Decimal("0.0000"),
                 _money(available + amount), _money(frozen - amount), order_id, None, memo)


def settle_buy(cur, project_id, order_id, trade_id, amount, fee, frozen_amount) -> None:
    """买入成交：从冻结里付出成交额与费用，**多冻的部分退回可用**。

    多冻从哪来：受理挂单时按**限价**冻（`限价 × 数量 + 费用`），成交却发生在
    快照价上（限价买要求 `快照价 ≤ 限价`），于是必然有多冻。不退回去，
    那笔钱就永远躺在冻结里 —— 对账的 `frozen_zero_no_open` 会在收盘后报警。
    """
    available, frozen = cash_balance(cur, project_id)
    used = _money(Decimal(str(amount)) + fee.total)

    if used > _money(frozen_amount):
        # 兜底：费用模型在受理与成交之间改过版本，理论上到不了这里。
        # 补一笔冻结把差额搬进冻结，保证 frozen_after 不会变负。
        shortfall = _money(used - _money(frozen_amount))
        available = _money(available - shortfall)
        frozen = _money(frozen + shortfall)
        _insert_cash(cur, project_id, "freeze", Decimal("0.0000"), available, frozen,
                     order_id, None, "补冻（成交额+费用超过受理时冻结额）")
        frozen_amount = used

    frozen_after = _money(frozen - Decimal(str(amount)))
    _insert_cash(cur, project_id, "buy", _money(-Decimal(str(amount))),
                 available, frozen_after, order_id, trade_id, "买入成交")

    frozen_after = _money(frozen_after - fee.total)
    _insert_cash(cur, project_id, "fee", _money(-fee.total),
                 available, frozen_after, order_id, trade_id, "买入费用")

    refund = _money(_money(frozen_amount) - used)
    if refund > 0:
        _insert_cash(cur, project_id, "unfreeze", Decimal("0.0000"),
                     _money(available + refund), _money(frozen_after - refund),
                     order_id, trade_id, "成交后解冻多冻部分")


def settle_sell(cur, project_id, order_id, trade_id, amount, fee) -> None:
    """卖出成交：金额入可用，佣金 + 过户费 + 印花税从可用扣。"""
    available, frozen = cash_balance(cur, project_id)
    available_after = _money(available + Decimal(str(amount)))
    _insert_cash(cur, project_id, "sell", _money(amount), available_after,
                 frozen, order_id, trade_id, "卖出成交")

    fee_only = _money(fee.commission + fee.transfer_fee)
    available_after = _money(available_after - fee_only)
    _insert_cash(cur, project_id, "fee", _money(-fee_only), available_after,
                 frozen, order_id, trade_id, "卖出费用")

    available_after = _money(available_after - fee.stamp_tax)
    _insert_cash(cur, project_id, "tax", _money(-fee.stamp_tax), available_after,
                 frozen, order_id, trade_id, "卖出印花税")


# ── 持仓 / 版本 / 参数 ────────────────────────────────────────────────────

def apply_position(cur, project_id, code, side, qty, price, at) -> None:
    pos = get_position(cur, project_id, code)
    if pos is None:
        cur.execute(
            """
            INSERT INTO fin_position (project_id, code, qty, sellable_qty, avg_cost, opened_at, updated_at)
            VALUES (%s,%s,0,0,0,%s,now())
            ON CONFLICT (project_id, code) DO NOTHING
            """,
            (project_id, code, at),
        )
        pos = get_position(cur, project_id, code)

    old_qty = int(pos["qty"])
    if side == "buy":
        new_qty = old_qty + qty
        # 加权平均成本（不含费）；已清仓后再买，直接用本次价
        old_cost = Decimal(str(pos["avg_cost"])) if old_qty > 0 else Decimal("0")
        new_cost = ((old_cost * old_qty) + (Decimal(str(price)) * qty)) / new_qty
        # T+1：当日买入**不计入** sellable_qty
        cur.execute(
            """
            UPDATE fin_position
               SET qty = %s, avg_cost = %s, updated_at = now(),
                   opened_at = COALESCE(opened_at, %s)
             WHERE project_id = %s AND code = %s
            """,
            (new_qty, _money(new_cost), at, project_id, code),
        )
    else:
        new_qty = old_qty - qty
        new_sellable = max(0, int(pos["sellable_qty"]) - qty)
        cur.execute(
            "UPDATE fin_position SET qty = %s, sellable_qty = %s, updated_at = now() "
            "WHERE project_id = %s AND code = %s",
            (new_qty, new_sellable, project_id, code),
        )


def bump_version(cur, project_id) -> None:
    cur.execute(
        "UPDATE fin_project SET version = version + 1 WHERE project_id = %s", (project_id,)
    )


def lock_params(cur, project_id) -> None:
    """首次成交 → 参数锁定时刻（M1 遗留 #6）。"""
    cur.execute(
        "UPDATE fin_param SET params_locked_at = COALESCE(params_locked_at, now()) "
        "WHERE project_id = %s",
        (project_id,),
    )


def confirm_t1(cur, project_id: str, trade_date=None) -> int:
    """把某日之前买入的持仓转为可卖（T+1 的日切）。返回受影响行数。

    挂单占用的股数不在这里处理：占用是**算出来的**（`open_sell_committed`），
    风控在判「可卖量」时现场减掉。日切只负责「昨天的买入今天能卖了」。
    何时调用（每个交易日开盘前）由 M4 的时点工作流决定。
    """
    cur.execute(
        """
        UPDATE fin_position
           SET sellable_qty = qty, updated_at = now()
         WHERE project_id = %s AND sellable_qty < qty
        """,
        (project_id,),
    )
    return cur.rowcount


# ── 时间 ──────────────────────────────────────────────────────────────────

def as_dt(value) -> datetime:
    """把请求里的时间（ISO 串或 datetime）统一成**带时区**的 datetime。"""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("时间必须带时区（TIMESTAMPTZ），不接受裸本地时间")
    return dt
