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

一期成交即成交（撮合四步链路在 M3）：买入走 `freeze → buy → fee` 三条，冻结在同一个
事务里归零；卖出走 `sell → fee → tax` 三条（券的冻结一期不建模，没有挂单）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import psycopg2.extras

from app.risk import RiskInputs, evaluate

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
               source, actor, decline_reason, created_at
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


# ── 写：一笔委托（风控 → 成交 → 记账）────────────────────────────────────

def place_order(cur, req: dict) -> dict:
    """提交一笔委托。

    **不论过不过，都会落一条 `fin_order`**：过的记成 `filled` 并产生成交与流水，
    不过的记成 `rejected` 并留下 `decline_reason`（`05 §3.2` M-11：为二期的
    `decline_reason` 复用同一条路径）。没有任何分支读 `source`。
    """
    project_id = req["project_id"]
    project = get_project(cur, project_id)
    if not project:
        raise LookupError(f"项目不存在：{project_id}")
    if project["status"] != "active":
        raise ValueError(f"项目不在进行中（status={project['status']}），不接受新委托")

    code = req["code"]
    side = req["side"]
    qty = int(req["qty"])
    source = req.get("source") or "ai"
    actor = req.get("actor") or "system"
    price_type = req.get("price_type") or "limit"

    snap = req["snapshot"]
    snap_time = _as_dt(snap["snapshot_time"])
    prev_close = snap.get("prev_close")
    last_price = snap.get("last_price")
    if last_price is None:
        raise ValueError("快照缺少 last_price，无法确定成交价")

    instrument = get_instrument(cur, code)
    calendar = get_calendar(cur, snap_time.astimezone(CST).date())
    fee_model = get_fee_model(cur)
    available, frozen = cash_balance(cur, project_id)
    position = get_position(cur, project_id, code) or {"qty": 0, "sellable_qty": 0}

    # 委托价：限价单用委托价（风控据此判涨跌停），市价单用快照价。
    risk_price = req.get("limit_price") if price_type == "limit" and req.get("limit_price") else last_price

    inputs = RiskInputs(
        at=snap_time,
        side=side,
        qty=qty,
        price=Decimal(str(risk_price)),
        calendar=calendar,
        instrument=instrument,
        fee_model=fee_model,
        prev_close=None if prev_close is None else Decimal(str(prev_close)),
        available=available,
        position_qty=int(position["qty"]),
        sellable_qty=int(position["sellable_qty"]),
        lot_size=int(instrument["lot_size"]) if instrument else 100,
    )
    outcome = evaluate(inputs)

    order_id = new_id("ord")
    if not outcome.passed:
        _insert_order(
            cur, order_id, project_id, code, side, qty, price_type, req.get("limit_price"),
            status="rejected", filled_qty=0, source=source, actor=actor,
            decline_reason=outcome.decline_reason, intent_ref=req.get("intent_ref"),
            decision_ref=req.get("decision_ref"), valid_until=req.get("valid_until"),
        )
        return {
            "order_id": order_id,
            "status": "rejected",
            "decline_reason": outcome.decline_reason,
            "failed_checks": outcome.failed_names,
            "risk": _risk_view(outcome),
        }

    # ── 过了风控：落委托 → 快照 → 成交 → 流水 → 持仓 → 版本 ─────────
    # 委托必须先落：`fin_trade.order_id` 是 FK（09 §二「账本内部表之间加 FK」）。
    # 整个请求一个事务，任何一步失败都会连这条委托一起回滚。
    _insert_order(
        cur, order_id, project_id, code, side, qty, price_type, req.get("limit_price"),
        status="filled", filled_qty=qty, source=source, actor=actor, decline_reason=None,
        intent_ref=req.get("intent_ref"), decision_ref=req.get("decision_ref"),
        valid_until=req.get("valid_until"),
    )

    snapshot_id = _ensure_snapshot(cur, snap, code)
    trade_id = new_id("trd")
    price = Decimal(str(last_price))          # 成交价 = 快照价（M3 换成四步撮合）
    amount = _money(Decimal(qty) * price)
    fee = outcome.fee

    _insert_trade(
        cur, trade_id, order_id, project_id, code, side, qty, price, amount, fee,
        snapshot_id, source, fee_model["version"], snap_time,
    )
    _apply_cash(cur, project_id, side, amount, fee, order_id, trade_id)
    _apply_position(cur, project_id, code, side, qty, price, snap_time)
    _bump_version(cur, project_id)
    _lock_params(cur, project_id)

    return {
        "order_id": order_id,
        "trade_id": trade_id,
        "status": "filled",
        "snapshot_id": snapshot_id,
        "filled_qty": qty,
        "price": price,
        "amount": amount,
        "fee": {
            "commission": fee.commission,
            "stamp_tax": fee.stamp_tax,
            "transfer_fee": fee.transfer_fee,
            "total": fee.total,
        },
        "decline_reason": None,
        "risk": _risk_view(outcome),
    }


# ── 内部：落库的四种写 ───────────────────────────────────────────────────

def _risk_view(outcome) -> dict:
    return {
        r.name: {"ok": r.ok, "reason": r.reason, "detail": r.detail}
        for r in outcome.results
    }


def _insert_order(cur, order_id, project_id, code, side, qty, price_type, limit_price,
                  status, filled_qty, source, actor, decline_reason, intent_ref,
                  decision_ref, valid_until) -> None:
    cur.execute(
        """
        INSERT INTO fin_order
          (order_id, project_id, code, side, qty, price_type, limit_price, status,
           filled_qty, source, actor, intent_ref, decline_reason, decision_ref, valid_until)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            order_id, project_id, code, side, qty, price_type,
            None if limit_price is None else _money(limit_price),
            status, filled_qty, source, actor,
            None if intent_ref is None else psycopg2.extras.Json(intent_ref),
            decline_reason, decision_ref, valid_until,
        ),
    )


def _ensure_snapshot(cur, snap: dict, code: str) -> str:
    """M2：允许占位快照（M3 起必须是采集到的真快照）。已存在则原样用。"""
    cur.execute(
        """
        INSERT INTO fin_snapshot
          (snapshot_id, code, snapshot_time, source, last_price, prev_close,
           bid1_price, ask1_price, quality, missing_flag)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (snapshot_id) DO NOTHING
        """,
        (
            snap["snapshot_id"], code, _as_dt(snap["snapshot_time"]), snap.get("source") or "placeholder",
            _money(snap["last_price"]),
            None if snap.get("prev_close") is None else _money(snap["prev_close"]),
            None if snap.get("bid1_price") is None else _money(snap["bid1_price"]),
            None if snap.get("ask1_price") is None else _money(snap["ask1_price"]),
            snap.get("quality") or "ok", bool(snap.get("missing_flag", False)),
        ),
    )
    return snap["snapshot_id"]


def _insert_trade(cur, trade_id, order_id, project_id, code, side, qty, price, amount,
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


def _apply_cash(cur, project_id, side, amount, fee, order_id, trade_id) -> None:
    available, frozen = cash_balance(cur, project_id)
    if side == "buy":
        need = _money(amount + fee.total)
        # freeze：可用 → 冻结（现金总额不变，amount = 0）
        available_after = _money(available - need)
        frozen_after = _money(frozen + need)
        _insert_cash(cur, project_id, "freeze", Decimal("0.0000"),
                     available_after, frozen_after, order_id, None, "委托冻结")
        # buy：从冻结里付出成交金额
        frozen_after = _money(frozen_after - amount)
        _insert_cash(cur, project_id, "buy", _money(-amount),
                     available_after, frozen_after, order_id, trade_id, "买入成交")
        # fee：从冻结里付出费用（冻结归零）
        frozen_after = _money(frozen_after - fee.total)
        _insert_cash(cur, project_id, "fee", _money(-fee.total),
                     available_after, frozen_after, order_id, trade_id, "买入费用")
    else:
        # sell：成交金额入可用
        available_after = _money(available + amount)
        _insert_cash(cur, project_id, "sell", amount, available_after, frozen,
                     order_id, trade_id, "卖出成交")
        # 佣金 + 过户费
        fee_only = _money(fee.commission + fee.transfer_fee)
        available_after = _money(available_after - fee_only)
        _insert_cash(cur, project_id, "fee", _money(-fee_only), available_after, frozen,
                     order_id, trade_id, "卖出费用")
        # 印花税（仅卖出）
        available_after = _money(available_after - fee.stamp_tax)
        _insert_cash(cur, project_id, "tax", _money(-fee.stamp_tax), available_after, frozen,
                     order_id, trade_id, "卖出印花税")


def _apply_position(cur, project_id, code, side, qty, price, at) -> None:
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


def _bump_version(cur, project_id) -> None:
    cur.execute(
        "UPDATE fin_project SET version = version + 1 WHERE project_id = %s", (project_id,)
    )


def _lock_params(cur, project_id) -> None:
    """首次成交 → 参数锁定时刻（M1 遗留 #6）。"""
    cur.execute(
        "UPDATE fin_param SET params_locked_at = COALESCE(params_locked_at, now()) "
        "WHERE project_id = %s",
        (project_id,),
    )


def confirm_t1(cur, project_id: str, trade_date=None) -> int:
    """把某日之前买入的持仓转为可卖（T+1 的日切）。返回受影响行数。

    M2 只提供这个原语；何时调用（每个交易日开盘前）由 M4 的时点工作流决定。
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

def _as_dt(value) -> datetime:
    """把请求里的时间（ISO 串或 datetime）统一成**带时区**的 datetime。"""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("时间必须带时区（TIMESTAMPTZ），不接受裸本地时间")
    return dt
