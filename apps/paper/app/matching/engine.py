"""四步链路的编排（`05 §3.2` M-12）。

```text
① 取快照（唯一编号 + 数据源时间戳）
② 过规则（M2 的六条风控 + 有效期检查）
③ 按快照撮合（限价不劣才成 / 市价按对手价+滑点 / 不可成交则挂单 / 收盘撤单解冻）
④ 记账并绑快照编号（fin_trade.snapshot_id）
```

**成交价 = 委托到达时刻的快照价**：不用当日均价、不用收盘价、不回填。
唯一的价格来源是 `fin_snapshot` 里那一行，而那一行的 `snapshot_time` 来自数据源。

四步的**顺序有一个反直觉的地方**：撮合被算了两次。

第一次是**试算**（纯函数，没有副作用），只为给风控一个价 —— 市价单的资金校验
需要知道「按对手价加滑点大概要花多少钱」，而那个价只有撮合算得出来。
第二次是把试算结果**落账**。两次之间隔着的只有风控；风控不过就整笔拒掉，
试算的那个价一个字都不会进账本。这样六条风控一条不少，且都在记账之前跑完。

拒与挂单的分界：

| 情形 | 结果 |
|---|---|
| 风控不过 / 过了有效期 / 没有可用快照 | `rejected`（留 `decline_reason`） |
| 快照有、但不可成交（断流 / 限价没到） | `pending`（买入冻结资金） |
| 能成交 | `filled`（写成交、绑快照、改持仓、版本 +1） |

「没有可用快照」是 `rejected` 而不是 `pending`：一张连涨跌停都算不出来的委托
挂进队列，等于把风控绕过延后到了成交那一刻。挂单的前提是**我们已经见过这只票
的有效报价**（有一个 `prev_close` 可以用来判涨跌停）。行情完全拿不到时，
行情未接通就是未接通 —— 拒绝，并且理由里说清楚。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from loguru import logger

from app import idempotency, ledger, snapshot
from app.market_time import DEFAULT_MARKET, market_local_date, market_of
from app.matching.model import load_execution_model
from app.matching.pricing import FILLED, MatchResult, OrderSpec, match
from app.risk import RiskInputs, evaluate
from app.risk import t1 as t1_mod


class NoSnapshot(Exception):
    """行情未接通 / 断流到连一张快照都落不下来。调用方转成 `rejected` 委托。"""


# ── ① 取快照 ──────────────────────────────────────────────────────────────

def take_snapshot(cur, code: str, *, now: Optional[datetime] = None) -> dict:
    """取一张快照并落库。拿不到 → 抛 `NoSnapshot`（**不落行**，也不编时间戳）。"""
    snap = snapshot.capture(cur, code, now=now)
    if snap is None:
        raise NoSnapshot(
            f"{code} 没有可用快照（行情未接通或数据源未给出时间戳），"
            "无法校验涨跌停与定价，拒绝委托"
        )
    return snap


# ── ④ 记账 ────────────────────────────────────────────────────────────────

def _book_fill(cur, project, order_id, req, snap, match, fee, amount, source, fee_version,
               *, market="CN_A", currency="CNY") -> str:
    """成交记账：委托 → 成交（绑快照）→ 现金 → 持仓 → 版本。返回 `trade_id`。

    **按该市场子账户落账**：成交 / 流水 / 持仓都带 `market` 与 `currency`（本币原值，
    不做任何跨币种换算）。A 股订单的 `market='CN_A' / currency='CNY'`，与改前逐位一致。
    """
    project_id = project["project_id"]
    side = req["side"]
    code = req["code"]
    qty = int(req["qty"])

    ledger.update_order_filled(cur, order_id, "filled", qty)

    trade_id = ledger.new_id("trd")
    ledger.insert_trade(
        cur, trade_id, order_id, project_id, code, side, qty, match.price, amount,
        fee, snap["snapshot_id"], source, fee_version, snap["snapshot_time"],
        market=market, currency=currency,
    )
    if side == "buy":
        order = ledger.get_order(cur, order_id)
        ledger.settle_buy(cur, project_id, order_id, trade_id, amount, fee,
                          order["frozen_amount"], market=market, currency=currency)
    else:
        ledger.settle_sell(cur, project_id, order_id, trade_id, amount, fee,
                           market=market, currency=currency)

    ledger.apply_position(cur, project_id, code, side, qty, match.price,
                          snap["snapshot_time"], market=market, currency=currency)
    ledger.bump_version(cur, project_id)
    ledger.lock_params(cur, project_id)
    return trade_id


def _park(cur, project, order_id, req, snap, match, fee, source, frozen_amount) -> None:
    """挂单：把状态改成 `pending`。

    买入的资金在**受理时**（`insert_order` 之后）就已经冻上了，见 `execute` ——
    受理即成交与挂单两条路都要先冻再结算，这样「成交时从冻结里付」这句话
    在两条路上是同一条。卖出没有现金冻结（券的占用由 `fin_order` 推导）。
    """
    ledger.update_order_filled(cur, order_id, "pending", 0, match.pending_reason)


def _reject(order_id: str, reason: str, **extra) -> dict:
    """被拒回执。委托行由调用方**先**用 `status='rejected'` 落好（留痕）；
    这里只拼回执，不再动账本。"""
    return {
        "order_id": order_id,
        "status": "rejected",
        "decline_reason": reason,
        "filled_qty": 0,
        "trade_id": None,
        "snapshot_id": extra.get("snapshot_id"),
        "snapshot_time": extra.get("snapshot_time"),
        "snapshot_quality": extra.get("snapshot_quality"),
        "snapshot_missing_flag": extra.get("snapshot_missing_flag"),
        "price": None,
        "amount": None,
        "fee": None,
        "pending_reason": None,
        "market": extra.get("market"),
        "currency": extra.get("currency"),
        # 价格带留痕（N2）：`None` = 这一笔还没走到第 4 条（如快照都拿不到）；
        # `False` = 该市场 `price_limit_mode='none'`，**没做价格带校验**（如实在回执写明）。
        "price_limit_checked": extra.get("price_limit_checked"),
        "snapshot_market": extra.get("snapshot_market"),
        "snapshot_quote_quality": extra.get("snapshot_quote_quality"),
        "risk": extra.get("risk"),
    }


def _risk_view(outcome) -> dict:
    return {r.name: {"ok": r.ok, "reason": r.reason, "detail": r.detail}
            for r in outcome.results}


def _price_limit_checked(outcome) -> Optional[bool]:
    """第 4 条的留痕字段：`True` 做了校验 / `False` 该市场模式为 `none` 未做 / `None` 没跑到。"""
    r = outcome.price_limit
    if r is None:
        return None
    return bool(r.detail.get("price_limit_checked", r.ok))


def _market_order_supported(market_rule: Optional[dict], market: str) -> bool:
    """该市场是否支持市价单（参数来自 `fin_market_rule.market_order_supported`，0037）。

    列缺失（迁移 0037 之前的库）→ 退回旧口径「只有 A 股支持市价单」，
    这样**老库上的行为与改动前逐字节一致**（A 股照旧、HK/US 照旧拒绝）。
    """
    if not market_rule:
        return False
    flag = market_rule.get("market_order_supported")
    if flag is None:
        return market == "CN_A"
    return bool(flag)


def _snapshot_view(snap: Optional[dict]) -> dict:
    if not snap:
        return {"snapshot_id": None, "snapshot_time": None,
                "snapshot_quality": None, "snapshot_missing_flag": None,
                "snapshot_market": None, "snapshot_quote_quality": None}
    return {
        "snapshot_id": snap["snapshot_id"],
        "snapshot_time": snap["snapshot_time"],
        "snapshot_quality": snap["quality"],
        "snapshot_missing_flag": bool(snap["missing_flag"]),
        # N3：快照归属哪个市场、盘口质量（回执上就能核对「这笔用的报价是哪来的」）。
        "snapshot_market": snap.get("market"),
        "snapshot_quote_quality": snap.get("quote_quality"),
    }


# ── 主入口 ────────────────────────────────────────────────────────────────

def execute(cur, req: dict, *, now: Optional[datetime] = None) -> dict:
    """走完四步链路。**一个请求一个事务**，任何一步失败整笔回滚。"""
    project_id = req["project_id"]
    # **单写者闸门**（`09 §六-4`）：进事务先按 project 拿锁，第二个并发请求排到第一个
    # 提交之后 —— 否则两个 Worker 会双双读到同一个版本、双双成交（M7 实测）。
    ledger.lock_project(cur, project_id)
    project = ledger.get_project(cur, project_id)
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
    key = req.get("idempotency_key")
    req_hash = idempotency.request_hash(req)

    # ── 幂等：**先**返回原回执，**再**校验账户版本（`01方案 §11.1` 末两条）──
    # 顺序写反的后果：一次「已成功、只是回执丢在路上」的重试会被版本校验拒掉，
    # 用户看到「账户版本冲突」而他只是重试了一次已经成功的下单。
    replayed = idempotency.replay_or_conflict(cur, key, req_hash)
    if replayed is not None:
        return replayed

    # ── 账户版本（乐观锁，`09 §六-4`）────────────────────────────────────
    expected = req.get("expected_version")
    if expected is not None and int(expected) != int(project["version"]):
        return _version_conflict(cur, project, req, key, req_hash, expected)

    order_id = ledger.new_id("ord")

    # ── ②-预 市场与币种（**先定市场**：之后每一处账本写入都要按子账户与本币）──
    # 市场以 `fin_instrument.market` 为准（N1 加的列）；标的缺失时按代码形态兜一层。
    instrument = ledger.get_instrument(cur, code)
    market = (instrument or {}).get("market") or market_of(code) or DEFAULT_MARKET
    market_rule = ledger.get_market_rule(cur, market)
    # 币种 = 该市场本币（CNY / HKD / USD）。账本内**永不折算**（设计 §3.1 A 方案）。
    currency = ((market_rule or {}).get("currency")
                or ledger.MARKET_CURRENCY.get(market) or "CNY")

    # ── ① 取快照 ─────────────────────────────────────────────────────────
    try:
        snap = take_snapshot(cur, code, now=now)
    except NoSnapshot as exc:
        ledger.insert_order(
            cur, order_id, project_id, code, side, qty, price_type, req.get("limit_price"),
            status="rejected", filled_qty=0, source=source, actor=actor,
            decline_reason=str(exc), intent_ref=req.get("intent_ref"),
            decision_ref=req.get("decision_ref"), valid_until=req.get("valid_until"),
            market=market, currency=currency,
        )
        receipt = _reject(order_id, str(exc), market=market, currency=currency)
        _remember(cur, key, req_hash, project_id, receipt, order_id)
        return receipt

    # ── ②-预 有效期（`fin_order.valid_until`：过期不补单）────────────────
    if req.get("valid_until") is not None:
        valid_until = ledger.as_dt(req["valid_until"])
        if snap["snapshot_time"] > valid_until:
            reason = (f"委托已过有效期（有效期至 {valid_until.isoformat()}，"
                      f"快照时刻 {snap['snapshot_time'].isoformat()}）")
            ledger.insert_order(
                cur, order_id, project_id, code, side, qty, price_type, req.get("limit_price"),
                status="rejected", filled_qty=0, source=source, actor=actor,
                decline_reason=reason, intent_ref=req.get("intent_ref"),
                decision_ref=req.get("decision_ref"), valid_until=req.get("valid_until"),
                market=market, currency=currency,
            )
            receipt = _reject(order_id, reason, market=market, currency=currency,
                              **_snapshot_view(snap))
            _remember(cur, key, req_hash, project_id, receipt, order_id)
            return receipt

    # ── ③-试算：撮合（纯函数，没有副作用）──────────────────────────────
    model = load_execution_model(cur)
    if model is None:
        raise ValueError("账本里没有执行模型（fin_execution_model 为空），拒绝撮合")

    spec = OrderSpec(
        side=side, qty=qty, price_type=price_type,
        limit_price=None if req.get("limit_price") is None else Decimal(str(req["limit_price"])),
    )
    trial = match(spec, snap, model)

    # 市价单能力**按市场取表**（`fin_market_rule.market_order_supported`，0037 落列）——
    # 不是散在这里的 `market != "CN_A"`（P2 任务书 §一.4：按 A 股写死的要参数化）。
    # HK / US 本期只接限价单（拍板 §四）：数据源没有盘口（`quote_quality='last_only'`），
    # 市价单只能拿最新价加滑点撮合 —— 那不是盘口价。明确拒绝并留痕，
    # **不拿最新价冒充买一/卖一**。A 股有盘口，市价单照旧。
    if price_type == "market" and not _market_order_supported(market_rule, market):
        reason = (f"{market} 没有盘口数据，本期只接限价单"
                  "（fin_market_rule.market_order_supported=false，市价单按最新价撮合会失真）；"
                  "请改用限价委托")
        ledger.insert_order(
            cur, order_id, project_id, code, side, qty, price_type, req.get("limit_price"),
            status="rejected", filled_qty=0, source=source, actor=actor,
            decline_reason=reason, intent_ref=req.get("intent_ref"),
            decision_ref=req.get("decision_ref"), valid_until=req.get("valid_until"),
            market=market, currency=currency,
        )
        receipt = _reject(order_id, reason, market=market, currency=currency,
                          **_snapshot_view(snap))
        receipt["failed_checks"] = ["price_type"]
        _remember(cur, key, req_hash, project_id, receipt, order_id)
        return receipt

    risk_price = _risk_price(req, snap, trial)
    # 日历按 **(市场, 当地日期)** 读：日期必须用该市场的时区切，不能用上海时间
    # （美股 16:00 ET 收盘时，上海已是次日 —— 用上海日期会取错日历行）。
    local_date = market_local_date(snap["snapshot_time"], market)
    calendar = ledger.get_calendar(cur, local_date, market=market)

    # 费用模型按市场取：市场规则给了版本号就按版本取，否则取该市场最新一行。
    fee_version = (market_rule or {}).get("fee_model_version")
    fee_model = ledger.get_fee_model(cur, version=fee_version, market=market)

    available, _frozen = ledger.cash_balance(cur, project_id, market)
    position = ledger.get_position(cur, project_id, code) or {"qty": 0, "sellable_qty": 0}
    committed = ledger.open_sell_committed(cur, project_id, code, market)

    # 可卖量按市场规则算：`same_day`（港/美股）用总持仓，`t_plus_n`（A 股）用日切后的可卖量；
    # 再统一扣掉未成交卖单占用的股数（否则同一批股能被两张挂单各卖一次）。
    position_qty = int(position["qty"])
    sellable_rule = (market_rule or {}).get("sellable_rule") or "t_plus_n"
    effective_sellable = t1_mod.effective_sellable(
        sellable_rule, position_qty, int(position["sellable_qty"])
    )
    sellable_qty = max(0, effective_sellable - committed)

    inputs = RiskInputs(
        at=snap["snapshot_time"],
        side=side,
        qty=qty,
        price=risk_price,
        calendar=calendar,
        instrument=instrument,
        fee_model=fee_model,
        prev_close=None if snap["prev_close"] is None else Decimal(str(snap["prev_close"])),
        available=available,
        position_qty=position_qty,
        sellable_qty=sellable_qty,
        market=market,
        market_rule=market_rule,
    )
    outcome = evaluate(inputs)

    if not outcome.passed:
        ledger.insert_order(
            cur, order_id, project_id, code, side, qty, price_type, req.get("limit_price"),
            status="rejected", filled_qty=0, source=source, actor=actor,
            decline_reason=outcome.decline_reason, intent_ref=req.get("intent_ref"),
            decision_ref=req.get("decision_ref"), valid_until=req.get("valid_until"),
            market=market, currency=currency,
        )
        receipt = _reject(order_id, outcome.decline_reason, market=market, currency=currency,
                          risk=_risk_view(outcome), **_snapshot_view(snap))
        receipt["failed_checks"] = outcome.failed_names
        receipt["market"] = market
        receipt["price_limit_checked"] = _price_limit_checked(outcome)
        _remember(cur, key, req_hash, project_id, receipt, order_id)
        return receipt

    amount = (Decimal(qty) * risk_price).quantize(Decimal("0.0001"))
    fee = outcome.fee

    # ── 落委托（成交 / 挂单都在这一步之前先把委托写下来：fin_trade 有 FK）──
    fill_amount = None
    if trial.filled:
        fill_amount = (Decimal(qty) * Decimal(str(trial.price))).quantize(Decimal("0.0001"))
        fill_fee = _recompute_fee(side, fill_amount, fee_model)
    else:
        fill_fee = fee

    frozen_amount = (amount + fee.total).quantize(Decimal("0.0001")) if side == "buy" else Decimal("0.0000")
    # 受理时按**限价**冻（限价单），市价单按试算价冻 —— 两者都是「当时知道的最坏价」
    ledger.insert_order(
        cur, order_id, project_id, code, side, qty, price_type, req.get("limit_price"),
        status="pending", filled_qty=0, source=source, actor=actor,
        decline_reason=None, intent_ref=req.get("intent_ref"),
        decision_ref=req.get("decision_ref"), valid_until=req.get("valid_until"),
        frozen_amount=frozen_amount, market=market, currency=currency,
    )
    # 买入**先冻再结算**：受理即成交与挂单两条路都先把钱搬到冻结，成交时再从冻结里付。
    # 不这么做的话，受理即成交那一路会直接去动一个空的冻结额（`frozen_after` 变负）。
    if side == "buy" and frozen_amount > 0:
        ledger.freeze_cash(cur, project_id, order_id, frozen_amount, "委托受理冻结",
                           market=market, currency=currency)

    # ── ④ 记账 ───────────────────────────────────────────────────────────
    if trial.filled:
        trade_id = _book_fill(cur, project, order_id, req, snap,
                              MatchResult(FILLED, Decimal(str(trial.price)), trial.basis),
                              fill_fee, fill_amount, source, fee_model["version"],
                              market=market, currency=currency)
        receipt = {
            "order_id": order_id,
            "status": "filled",
            "trade_id": trade_id,
            "filled_qty": qty,
            "price": Decimal(str(trial.price)),
            "amount": fill_amount,
            "fee": _fee_view(fill_fee),
            "decline_reason": None,
            "pending_reason": None,
            "price_basis": trial.basis,
            "market": market,
            "currency": currency,
            "price_limit_checked": _price_limit_checked(outcome),
            "risk": _risk_view(outcome),
            **_snapshot_view(snap),
        }
    else:
        _park(cur, project, order_id, req, snap, trial, fill_fee, source, frozen_amount)
        receipt = {
            "order_id": order_id,
            "status": "pending",
            "trade_id": None,
            "filled_qty": 0,
            "price": None,
            "amount": None,
            "fee": None,
            "decline_reason": None,
            "pending_reason": trial.pending_reason,
            "price_basis": trial.basis,
            "frozen_amount": frozen_amount,
            "market": market,
            "currency": currency,
            "price_limit_checked": _price_limit_checked(outcome),
            "risk": _risk_view(outcome),
            **_snapshot_view(snap),
        }

    _remember(cur, key, req_hash, project_id, receipt, receipt.get("trade_id") or order_id)
    return receipt


def _risk_price(req, snap, trial) -> Decimal:
    """风控用的价。限价单用**限价**（那是委托的边界），市价单用撮合出来的价
    （对手价 ± 滑点）—— 市价单没有别的价能代表「最多花多少」。"""
    if (req.get("price_type") or "limit") == "limit" and req.get("limit_price") is not None:
        return Decimal(str(req["limit_price"]))
    if trial.price is not None:
        return Decimal(str(trial.price))
    return Decimal(str(snap["last_price"]))


def _recompute_fee(side: str, amount: Decimal, fee_model: dict):
    """按**实际成交额**重算费用。

    试算价（限价单用限价）与实际成交价（快照价）不同时，费用必须按实际成交额算 ——
    否则佣金会按一个没发生的金额收（限价 10.50 成交在 10.00 上，佣金按 10.50 收）。
    """
    from app.risk import fee as fee_mod

    return fee_mod.compute_fee(side, amount, fee_model)


def _fee_view(fee) -> dict:
    return {
        "commission": fee.commission,
        "stamp_tax": fee.stamp_tax,
        "transfer_fee": fee.transfer_fee,
        "total": fee.total,
    }


def _remember(cur, key, req_hash, project_id, receipt, response_ref) -> None:
    """把回执与幂等键**在同一个事务里**记下（`01方案 §11.1` 最后一条）。"""
    if not key:
        return
    idempotency.record(cur, key, req_hash, project_id, receipt, response_ref)


def _version_conflict(cur, project, req, key, req_hash, expected) -> dict:
    """账户版本对不上：**拒绝并留痕**（一条 `rejected` 委托）。

    留痕用委托表而不是日志：二期人工委托会复用同一条拒绝路径，
    界面上能直接看到「这笔为什么没成」。
    """
    project_id = project["project_id"]
    actual = int(project["version"])
    reason = (f"账户版本冲突：委托基于 version={int(expected)}，当前 version={actual}。"
              "同一账户的并发命令里，后到者被版本校验拦下")
    order_id = ledger.new_id("ord")
    instrument = ledger.get_instrument(cur, req["code"])
    vmarket = (instrument or {}).get("market") or market_of(req["code"]) or DEFAULT_MARKET
    vrule = ledger.get_market_rule(cur, vmarket)
    vcurrency = ((vrule or {}).get("currency")
                 or ledger.MARKET_CURRENCY.get(vmarket) or "CNY")
    ledger.insert_order(
        cur, order_id, project_id, req["code"], req["side"], int(req["qty"]),
        req.get("price_type") or "limit", req.get("limit_price"),
        status="rejected", filled_qty=0, source=req.get("source") or "ai",
        actor=req.get("actor") or "system", decline_reason=reason,
        intent_ref=req.get("intent_ref"), decision_ref=req.get("decision_ref"),
        valid_until=req.get("valid_until"), market=vmarket, currency=vcurrency,
    )
    logger.warning("[paper.matching] 版本冲突 · project={} 期望={} 实际={}",
                   project_id, expected, actual)
    receipt = {
        "order_id": order_id,
        "status": "rejected",
        "decline_reason": reason,
        "failed_checks": ["project_version"],
        "filled_qty": 0,
        "trade_id": None,
        "price": None,
        "amount": None,
        "fee": None,
        "pending_reason": None,
        "expected_version": int(expected),
        "actual_version": actual,
        "snapshot_id": None,
        "snapshot_time": None,
        "risk": None,
    }
    _remember(cur, key, req_hash, project_id, receipt, order_id)
    return receipt


# ── 收盘撤单 / 解冻 ───────────────────────────────────────────────────────

def expire_open_orders(
    cur, project_id: str, at: datetime, *, reason: str = "close",
    market: Optional[str] = None,
) -> dict:
    """撤掉未成交的挂单并**解冻**。

    `reason='close'`   —— 收盘撤单（当日未成交的全部撤掉）。
    `reason='validity'`—— 只撤已经越过 `valid_until` 的。

    解冻按 `fin_order.frozen_amount` 原样退回，不重算 —— 受理时冻了多少就退多少。

    **按市场**（P2）：多市场项目在**每个市场的收盘**各撤各的子账户 —— 不限定市场的话，
    先收盘的市场（港股 16:15）会把后开盘市场（美股）当天还没到收盘的挂单一起撤掉。
    `market` 不给（单市场项目）行为逐字不变。
    """
    ledger.lock_project(cur, project_id)
    orders = ledger.list_open_orders(cur, project_id, market=market)
    expired: list[dict] = []
    for order in orders:
        if reason == "validity":
            if order["valid_until"] is None or at <= order["valid_until"]:
                continue
        ledger.update_order_filled(cur, order["order_id"], "expired", 0,
                                   f"未成交撤单（{reason}），冻结资金已解冻")
        frozen = order.get("frozen_amount") or Decimal("0.0000")
        # 解冻在**该订单所属子账户**里做（`fin_order.market` / `currency`）。
        ledger.unfreeze_cash(cur, project_id, order["order_id"], frozen, "撤单解冻",
                             market=order.get("market") or "CN_A",
                             currency=order.get("currency") or "CNY")
        expired.append({
            "order_id": order["order_id"],
            "code": order["code"],
            "side": order["side"],
            "market": order.get("market"),
            "unfrozen": Decimal(str(frozen)),
        })
    return {"expired": len(expired), "orders": expired}


def cancel_order(cur, project_id: str, order_id: str, memo: str = "人工撤单") -> dict:
    """撤一张挂单（`cancelled`）。已成交 / 已终态的单返回原样，不报错。"""
    ledger.lock_project(cur, project_id)
    order = ledger.get_order(cur, order_id)
    if not order or order["project_id"] != project_id:
        raise LookupError(f"委托不存在：{order_id}")
    if order["status"] not in ("pending", "accepted", "partially_filled"):
        return {"order_id": order_id, "status": order["status"], "changed": False,
                "unfrozen": Decimal("0.0000")}
    ledger.update_order_filled(cur, order_id, "cancelled", 0, f"{memo}，冻结资金已解冻")
    frozen = order.get("frozen_amount") or Decimal("0.0000")
    ledger.unfreeze_cash(cur, project_id, order_id, frozen, "撤单解冻",
                         market=order.get("market") or "CN_A",
                         currency=order.get("currency") or "CNY")
    return {"order_id": order_id, "status": "cancelled", "changed": True,
            "unfrozen": Decimal(str(frozen))}


# ── 挂单再撮合 ────────────────────────────────────────────────────────────

def match_open_orders(cur, project_id: str, *, now: Optional[datetime] = None,
                      market: Optional[str] = None) -> dict:
    """拿新快照再撮一遍挂单（M4 的时点工作流按时刻调它）。

    不再跑六条风控：受理时已经跑过，而且
      · 买入的钱冻着（`used ≤ frozen_amount` 由「成交价 ≤ 限价」保证）；
      · 卖出的股由 `open_sell_committed` 占着，别的挂单抢不走。
    这两条让「再撮合」是安全的：它只可能把一张已经通过风控的单变成成交。

    **按市场**（P2）：只撮该子账户的挂单 —— 否则 A 股的挂单会在美股时点拿一张
    隔夜的 A 股快照撮合（价格是旧的）。`market` 不给（单市场项目）行为逐字不变。
    """
    ledger.lock_project(cur, project_id)
    orders = ledger.list_open_orders(cur, project_id, market=market)
    model = load_execution_model(cur)
    if model is None:
        raise ValueError("账本里没有执行模型（fin_execution_model 为空），拒绝撮合")

    filled: list[dict] = []
    for order in orders:
        snap = snapshot.capture(cur, order["code"], now=now)
        if snap is None:
            continue
        spec = OrderSpec(
            side=order["side"], qty=int(order["qty"]), price_type=order["price_type"],
            limit_price=order["limit_price"],
        )
        result = match(spec, snap, model)
        if not result.filled:
            continue
        project = ledger.get_project(cur, project_id)
        # 费用模型**按该委托所属市场**取（挂单再撮合时按实际成交额重算费用）——
        # 拿 A 股费率给港美股挂单算费就是编数字。市场以标的元数据为准，缺失时按代码形态。
        instrument = ledger.get_instrument(cur, order["code"])
        market = (instrument or {}).get("market") or market_of(order["code"]) or DEFAULT_MARKET
        market_rule = ledger.get_market_rule(cur, market) or {}
        fee_model = ledger.get_fee_model(
            cur, version=market_rule.get("fee_model_version"), market=market
        )
        if fee_model is None:
            raise ValueError(f"{market} 没有可用的费用模型（fin_fee_model 无该市场行），拒绝撮合挂单")
        qty = int(order["qty"])
        fill_amount = (Decimal(qty) * Decimal(str(result.price))).quantize(Decimal("0.0001"))
        fee = _recompute_fee(order["side"], fill_amount, fee_model)
        currency = ((market_rule.get("currency")
                     if isinstance(market_rule, dict) else None)
                    or fee_model.get("currency")
                    or ledger.MARKET_CURRENCY.get(market) or "CNY")
        req = {"side": order["side"], "code": order["code"], "qty": qty}
        trade_id = _book_fill(cur, project, order["order_id"], req, snap,
                              MatchResult(FILLED, Decimal(str(result.price)),
                                                  result.basis),
                              fee, fill_amount, order["source"], fee_model["version"],
                              market=market, currency=currency)
        filled.append({"order_id": order["order_id"], "trade_id": trade_id,
                       "price": Decimal(str(result.price)), "qty": qty,
                       "market": market,
                       "snapshot_id": snap["snapshot_id"]})
    return {"matched": len(filled), "fills": filled}
