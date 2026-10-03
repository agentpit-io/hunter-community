"""影子臂的模拟撮合 —— **只算不记、绝不下单**（R7 · `plan/R7.md` §一.1）。

第四段的影子验证要给「未执行过的候选策略」算成绩。做这件事**只有一个正确的做法**：
拿**与真实账本同一套**的费率 / 滑点 / 停牌 / 撮合口径，在两个臂上各跑一遍 ——
口径重写一遍就等于让候选臂的成绩建立在一套**与真实账本不同**的假设上，那这轮验证白做
（红线 9「不许新增平行权威」）。所以本模块**复用** `ledger.py` / `matching/` / `risk/`
的既有实现，一行口径都不新写。

## 红线 12 的可执行定义：接口隔离

```text
  simulate_arms(cur, req, *, recorder)          ← 签名里只有 recorder
        │
        ├── recorder.record(event)              ← 只记不执行（DecisionRecorder）
        │
        └── ✗ 永远不 new / 不调用 EngineOrderExecutor.place_order()
```

- `DecisionRecorder` —— **只记不执行**的接口。影子路径**唯一**依赖它。
- `OrderExecutor` —— **真正下单**的接口（生产实现 `EngineOrderExecutor` 包着
  `matching.engine.execute`，即那条会写 `fin_trade` / `fin_order` 的路）。
  影子路径**从不** import 它的实例、也从不调用它。
- 集成测试（`tests/test_shadow_isolation.py`）把执行端换成**计数替身**，跑一次完整的
  候选臂运行，断言**零调用**；并直接 monkeypatch `matching.engine.execute` 让它一旦被
  调用就抛 —— 双保险。

## 无订单出口的可执行定义

影子成交**只进影子事件表**（`0043` 建的那张，由 api 落库，见下），
**绝不写** `fin_trade` / `fin_order`。本模块**一行写账本表的 SQL 都没有**：
它只读参考数据（标的 / 市场规则 / 费率 / 日历 / 执行模型）与写一张 `fin_snapshot`
（两张臂共用的那张真实报价 —— 它是有数据源时刻的真报价，不是编的）。

## 为什么落库不在这里

`0043`（照 `0041`）**刻意不给** `fin_paper_rw` 任何授权（本服务连的角色正是它）——
所以影子事件表由 **api**（库属主身份）写。本模块只**算出**每一行该长什么样，
经 HTTP 返回给调用方（`fin.shadow` 工作流 → api 的 `/shadow/record`）。
于是「撮合实现」与「影子账本」各自只有一份，谁也没多出一个权威。

## 状态从哪来

影子臂**没有自己的账本表**（红线：不新建账本表）。它这一步之前的现金 / 持仓，
由调用方从**该臂最近一条影子事件的 `position_ref`** 里取出、随请求带进来
（`由事件逐笔重建`）。两臂的**初始状态相同**（同本金、空仓）—— 红线 11 的「同初始现金 / 仓位」。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional, Protocol

from loguru import logger

from app import ledger, snapshot
from app.market_time import DEFAULT_MARKET, market_local_date, market_of
from app.matching.model import load_execution_model
from app.matching.pricing import FILLED, OrderSpec, match
from app.risk import RiskInputs, evaluate
from app.risk import fee as fee_mod
from app.risk import t1 as t1_mod

_Q4 = Decimal("0.0001")


def _dec(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(_Q4, rounding=ROUND_HALF_UP)


# ════════════════════════════════════════════════════════════════════════
# 接口隔离（红线 12）
# ════════════════════════════════════════════════════════════════════════

class DecisionRecorder(Protocol):
    """**只记不执行** —— 影子路径唯一依赖的接口。"""

    def record(self, event: dict[str, Any]) -> None:
        """记下一条影子事件（该臂该步的决策 / 模拟成交 / 持仓 / 估值快照）。"""
        ...


class OrderExecutor(Protocol):
    """**真正下单** —— 影子路径**永不**调用。生产实现见 `EngineOrderExecutor`。"""

    def place_order(self, cur, req: dict[str, Any]) -> dict[str, Any]:
        """把一笔委托提交给账本（会写 `fin_order` / `fin_trade`）。"""
        ...


class EngineOrderExecutor:
    """`OrderExecutor` 的生产实现：把 `matching.engine.execute`（真实下单那条路）包起来。

    ⚠️ 本类**只被真实交易路径（`routers/orders.py`）使用**；影子路径不 import 它、
    不 new 它、不调它。它存在这里是为了让「执行端」有一个**具名的替身靶子**供集成测试打桩。
    """

    def place_order(self, cur, req: dict[str, Any]) -> dict[str, Any]:
        from app.matching.engine import execute
        return execute(cur, req)


class CollectingRecorder:
    """`DecisionRecorder` 的生产实现：**只收集**，返回给调用方去落库。"""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, event: dict[str, Any]) -> None:
        self.events.append(event)


# ════════════════════════════════════════════════════════════════════════
# 影子状态（现金 / 持仓）—— 由调用方从事件重建后带进来
# ════════════════════════════════════════════════════════════════════════

def _state_view(state: Optional[dict]) -> dict[str, Any]:
    return {
        "cash_available": _dec((state or {}).get("cash_available")) or Decimal("0"),
        "cash_frozen": _dec((state or {}).get("cash_frozen")) or Decimal("0"),
        "positions": {str(p["code"]): dict(p) for p in ((state or {}).get("positions") or [])},
    }


def _position(state: dict, code: str) -> dict[str, Any]:
    pos = state["positions"].get(code)
    if pos is None:
        pos = {"code": code, "qty": 0, "avg_cost": "0", "sellable_qty": 0, "price": None}
        state["positions"][code] = pos
    return pos


def _positions_list(state: dict) -> list[dict[str, Any]]:
    """只列**有持仓**的（qty > 0）。

    `_position()` 会在「过规则要算持仓」时给没有持仓的标的建一行占位（qty=0、无价）——
    那一行不该进快照：带着 `price=None` 的零仓行会让复算方无从下手（也不代表任何事实）。
    """
    out: list[dict[str, Any]] = []
    for code in sorted(state["positions"]):
        pos = state["positions"][code]
        if int(pos.get("qty") or 0) > 0:
            out.append(pos)
    return out


def _valuation(state: dict, *, initial_capital: Decimal, as_of_price,
               as_of: Any) -> dict[str, Any]:
    """估值口径（与 `app/valuation.py` **同一句**）：`总资产 = 可用 + 冻结 + Σ(股数 × 价)`。

    每个持仓的价 = 该持仓的最近价（交易标的本步用**本步快照价**）。
    **缺价就不出估值**（和 `valuation.py` 的 `MissingPrice` 同一立场：不拿成本价顶替市值）——
    这里返回 `None`，由调用方据此把该行标成 `inconclusive` 依据（未完成持仓）。
    """
    mv = Decimal("0")
    missing: list[str] = []
    for code in sorted(state["positions"]):
        pos = state["positions"][code]
        qty = int(pos.get("qty") or 0)
        if qty <= 0:
            continue
        price = _dec(pos.get("price"))
        if price is None:
            missing.append(code)
            continue
        mv += price * qty
    if missing:
        return {"missing_prices": missing, "total_assets": None, "market_value": None,
                "nav": None, "cash_available": str(_money(state["cash_available"])),
                "cash_frozen": str(_money(state["cash_frozen"])),
                "initial_capital": str(initial_capital), "as_of_price": as_of_price,
                "as_of": as_of}
    mv = _money(mv)
    total = _money(state["cash_available"] + state["cash_frozen"] + mv)
    nav = (total / initial_capital) if initial_capital > 0 else None
    return {
        "cash_available": str(_money(state["cash_available"])),
        "cash_frozen": str(_money(state["cash_frozen"])),
        "market_value": str(mv),
        "total_assets": str(total),
        "nav": None if nav is None else format(nav.quantize(Decimal("0.000001"),
                                                           rounding=ROUND_HALF_UP), "f"),
        "initial_capital": str(initial_capital),
        "as_of_price": as_of_price,
        "as_of": as_of,
    }


# ════════════════════════════════════════════════════════════════════════
# 主入口：一次请求里两臂同快照各跑一遍
# ════════════════════════════════════════════════════════════════════════

def simulate_arms(cur, req: dict[str, Any], *, recorder: DecisionRecorder) -> dict[str, Any]:
    """在一个行情快照上，对**每个臂**各跑一次「过规则 → 撮合 → 记账（只记）」。

    **同一行情快照**（红线 11）：整次调用只 `capture` 一次，两臂共用同一张 `fin_snapshot`
    （同一个 `snapshot_id` / `snapshot_time`）—— 于是两臂的 `quote_as_of` 必然完全相同。

    参数里的 `recorder` 是**唯一**的副作用出口；执行端 `OrderExecutor` 从头到尾没出现。
    """
    project_id = req["project_id"]
    market = str(req.get("market") or DEFAULT_MARKET)
    symbol = str(req["symbol"])
    point = str(req.get("point") or "")
    trade_date = str(req.get("trade_date") or "")
    initial_capital = _dec(req.get("initial_capital")) or Decimal("0")

    # ── ① 取快照（**一次**，两臂共用）──────────────────────────────────────
    snap = snapshot.capture(cur, symbol)
    if snap is None:
        # 行情未接通 / 断流：两臂都算「行情缺口」——**不编价、不成交**。这是 `inconclusive`
        # 的依据之一（`03 §4-C`：行情缺口一律 inconclusive），不是错误。
        logger.info("[shadow] {} 无可用快照（行情缺口）· market={} point={}",
                    symbol, market, point)
        results = [
            _arm_event(arm=arm, symbol=symbol, point=point, trade_date=trade_date,
                       snapshot=None, reason="行情缺口：没有可用快照（行情未接通或断流），"
                                             "两臂都不成交", filled=False, recorder=recorder,
                       state=_state_view(arm.get("state")), initial_capital=initial_capital,
                       side=arm.get("side"), qty=arm.get("qty"), price_type=arm.get("price_type"),
                       limit_price=arm.get("limit_price"))
            for arm in (req.get("arms") or [])
        ]
        return {"quote_as_of": None, "snapshot_id": None, "snapshot_tradable": False,
                "gap": True, "results": results}

    # ── ② 参考数据（与真实撮合**同一批函数**）─────────────────────────────
    instrument = ledger.get_instrument(cur, symbol)
    market = (instrument or {}).get("market") or market_of(symbol) or market or DEFAULT_MARKET
    market_rule = ledger.get_market_rule(cur, market)
    currency = ((market_rule or {}).get("currency")
                or ledger.MARKET_CURRENCY.get(market) or "CNY")
    fee_version = (market_rule or {}).get("fee_model_version")
    fee_model = ledger.get_fee_model(cur, version=fee_version, market=market)
    model = load_execution_model(cur)
    local_date = market_local_date(snap["snapshot_time"], market)
    calendar = ledger.get_calendar(cur, local_date, market=market)

    results: list[dict[str, Any]] = []
    for arm in (req.get("arms") or []):
        results.append(_simulate_one(
            arm=arm, snap=snap, symbol=symbol, market=market, currency=currency,
            market_rule=market_rule, instrument=instrument, fee_model=fee_model,
            model=model, calendar=calendar, trade_date=trade_date, point=point,
            initial_capital=initial_capital, recorder=recorder,
        ))
    return {
        "quote_as_of": snap["snapshot_time"].isoformat(),
        "snapshot_id": snap["snapshot_id"],
        "snapshot_tradable": bool(snap.get("tradable")),
        "market": market, "currency": currency,
        "gap": False,
        "results": results,
    }


def _simulate_one(*, arm, snap, symbol, market, currency, market_rule, instrument,
                  fee_model, model, calendar, trade_date, point, initial_capital,
                  recorder) -> dict[str, Any]:
    """一个臂在**已取好的那张快照**上的一次模拟。"""
    state = _state_view(arm.get("state"))
    side = str(arm.get("side") or "buy")
    qty = int(arm.get("qty") or 0)
    price_type = str(arm.get("price_type") or "market")
    limit_price = _dec(arm.get("limit_price"))

    if model is None:
        return _arm_event(arm=arm, symbol=symbol, point=point, trade_date=trade_date,
                          snapshot=snap, reason="账本里没有执行模型（fin_execution_model 为空）",
                          filled=False, recorder=recorder, state=state,
                          initial_capital=initial_capital, side=side, qty=qty,
                          price_type=price_type, limit_price=limit_price)

    spec = OrderSpec(side=side, qty=qty, price_type=price_type, limit_price=limit_price)
    trial = match(spec, snap, model)

    # ── 过规则（**同一套六条风控**，用该臂自己的现金 / 持仓）─────────────────
    price_for_risk = limit_price if (price_type == "limit" and limit_price is not None) else (
        trial.price if trial.price is not None else _dec(snap.get("last_price")))
    position = _position(state, symbol)
    position_qty = int(position.get("qty") or 0)
    sellable_rule = (market_rule or {}).get("sellable_rule") or "t_plus_n"
    effective_sellable = t1_mod.effective_sellable(
        sellable_rule, position_qty, int(position.get("sellable_qty") or 0))
    inputs = RiskInputs(
        at=snap["snapshot_time"], side=side, qty=qty, price=price_for_risk,
        calendar=calendar, instrument=instrument, fee_model=fee_model,
        prev_close=_dec(snap.get("prev_close")), available=state["cash_available"],
        position_qty=position_qty, sellable_qty=max(0, effective_sellable),
        market=market, market_rule=market_rule,
    )
    outcome = evaluate(inputs)
    if not outcome.passed:
        return _arm_event(
            arm=arm, symbol=symbol, point=point, trade_date=trade_date, snapshot=snap,
            reason="风控拒绝：" + (outcome.decline_reason or ""), filled=False,
            recorder=recorder, state=state, initial_capital=initial_capital, side=side,
            qty=qty, price_type=price_type, limit_price=limit_price,
            failed=outcome.failed_names)

    if not trial.filled:
        return _arm_event(arm=arm, symbol=symbol, point=point, trade_date=trade_date,
                          snapshot=snap, reason=trial.pending_reason or "未成交", filled=False,
                          recorder=recorder, state=state, initial_capital=initial_capital,
                          side=side, qty=qty, price_type=price_type, limit_price=limit_price)

    # ── 成交：按**实际成交额**重算费用（与 engine._recompute_fee 同一口径）───
    fill_price = Decimal(str(trial.price))
    fill_amount = _money(Decimal(qty) * fill_price)
    fill_fee = fee_mod.compute_fee(side, fill_amount, fee_model)
    slippage_total = (_money(model.slippage * qty)
                      if "slippage" in str(trial.basis) else Decimal("0.0000"))
    realized = _apply_fill(state, symbol=symbol, side=side, qty=qty, price=fill_price,
                           fee_total=fill_fee.total)
    position = _position(state, symbol)
    position["price"] = format(fill_price, "f")

    return _arm_event(
        arm=arm, symbol=symbol, point=point, trade_date=trade_date, snapshot=snap,
        reason=None, filled=True, recorder=recorder, state=state,
        initial_capital=initial_capital, side=side, qty=qty, price_type=price_type,
        limit_price=limit_price, fill_price=fill_price, fill_amount=fill_amount,
        fill_basis=trial.basis, fee=fill_fee.total, slippage=slippage_total,
        realized=realized, currency=currency, market=market,
    )


def _apply_fill(state: dict, *, symbol: str, side: str, qty: int, price: Decimal,
                fee_total: Decimal) -> Optional[Decimal]:
    """把一次成交**记进影子状态**（算法与 `ledger.settle_buy/settle_sell/apply_position` 同）。

    买入：可用 -= (成交额 + 费用)，持仓加权平均成本（**不含费**，与 `apply_position` 一致）。
    卖出：可用 += (成交额 − 费用)，持仓减，返回**该笔已实现净损益**（卖出才有；买入返回 None）。
    """
    pos = _position(state, symbol)
    amount = _money(Decimal(qty) * price)
    if side == "buy":
        state["cash_available"] = _money(state["cash_available"] - amount - fee_total)
        old_qty = int(pos.get("qty") or 0)
        new_qty = old_qty + qty
        old_cost = _dec(pos.get("avg_cost")) or Decimal("0")
        new_cost = (((old_cost * old_qty) + (price * qty)) / new_qty) if new_qty > 0 else Decimal("0")
        pos["qty"] = new_qty
        pos["avg_cost"] = format(_money(new_cost), "f")
        # 影子臂不做 T+1 日切（它没有 preopen 那一步）——可卖量 = 总持仓，
        # 与港美股口径一致，A 股的 T+1 差异在本轮**如实记为已简化**（见成果文档）。
        pos["sellable_qty"] = new_qty
        return None
    # sell
    state["cash_available"] = _money(state["cash_available"] + amount - fee_total)
    old_qty = int(pos.get("qty") or 0)
    cost = _dec(pos.get("avg_cost")) or Decimal("0")
    realized = _money(amount - fee_total - cost * qty)
    pos["qty"] = max(0, old_qty - qty)
    pos["sellable_qty"] = max(0, int(pos.get("sellable_qty") or 0) - qty)
    return realized


def _arm_event(*, arm, symbol, point, trade_date, snapshot, reason, filled, recorder,
               state, initial_capital, side, qty, price_type, limit_price,
               fill_price=None, fill_amount=None, fill_basis=None, fee=None,
               slippage=None, realized=None, currency=None, market=None,
               failed=None) -> dict[str, Any]:
    """拼一条影子事件、交给 `recorder`，并原样返回（供调用方汇总）。

    `signal` = 该臂在这一点**是否真的出了决策**（`decided`）。没出决策（如总开关关闭 /
    闸门命中）时 `signal=None` —— 于是它**不计入可比样本**（红线 11：两臂都有可比机会才算）。
    出了决策但没成交（风控拒 / 限价没到 / 行情缺口）时 `signal` 仍在、`filled=false`。
    """
    decided = bool(arm.get("decided", True))
    snap_view = None
    if snapshot is not None:
        snap_view = {
            "snapshot_id": snapshot.get("snapshot_id"),
            "last_price": (None if snapshot.get("last_price") is None
                           else format(_dec(snapshot.get("last_price")), "f")),
            "quality": snapshot.get("quality"),
            "tradable": bool(snapshot.get("tradable")),
        }
    signal = None
    if decided:
        signal = {
            "side": side, "qty": int(qty or 0), "price_type": price_type,
            "limit_price": None if limit_price is None else format(limit_price, "f"),
            "fill": (None if not filled else {
                "price": format(fill_price, "f"),
                "amount": format(_money(fill_amount), "f"),
                "basis": fill_basis,
            }),
            "realized_pnl": None if realized is None else format(_money(realized), "f"),
            "failed_checks": failed or None,
            "market": market, "currency": currency,
        }
    valuation = _valuation(
        state, initial_capital=initial_capital, as_of_price=snap_view,
        as_of=(None if snapshot is None else snapshot["snapshot_time"].isoformat()),
    )
    event: dict[str, Any] = {
        "arm": arm.get("arm"),
        "trade_date": trade_date,
        "point": point,
        "symbol": symbol,
        "quote_as_of": None if snapshot is None else snapshot["snapshot_time"].isoformat(),
        "signal": signal,
        "filled": bool(filled),
        "reject_reason": None if filled else (reason or "未成交"),
        "fee": None if fee is None else format(_money(fee), "f"),
        "slippage": None if slippage is None else format(_money(slippage), "f"),
        "position": {
            "cash_available": format(_money(state["cash_available"]), "f"),
            "cash_frozen": format(_money(state["cash_frozen"]), "f"),
            "positions": _positions_list(state),
        },
        "valuation": valuation,
    }
    recorder.record(event)
    return event
