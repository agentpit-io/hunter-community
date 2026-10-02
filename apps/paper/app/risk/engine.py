"""风控引擎：把六条纯函数串成一次委托预检。

**按顺序跑完六条、收集全部不过的原因**（不是第一条不过就返回）——用户一次就能看到
「数量不整手 + 资金也不够」，而不是改一个报一个。`decline_reason` 是所有原因的拼接。

这里**没有任何按 `source` 分支的逻辑**（`09 §六-10`）：AI 的单和人下的单走同一套。
`source` 只是随委托落库、供二期统计归因，不参与任何判断。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from app.risk import fee as fee_mod
from app.risk import funds as funds_mod
from app.risk import lot as lot_mod
from app.risk import price_limit as limit_mod
from app.risk import session as session_mod
from app.risk import t1 as t1_mod
from app.risk.fee import FeeBreakdown
from app.risk.result import RiskResult


@dataclass
class RiskInputs:
    at: datetime                       # 委托到达时刻（带时区）
    side: str                          # 'buy' / 'sell'
    qty: int
    price: Decimal                     # 拟成交价（市价单 = 对手价 / 快照价）
    calendar: Optional[dict]           # fin_market_calendar 的一行
    instrument: Optional[dict]         # fin_instrument 的一行
    fee_model: Optional[dict]          # fin_fee_model 的一行
    prev_close: Optional[Decimal]      # 前收盘价（来自快照）
    available: Decimal                 # 当前可用资金（账本累计）
    position_qty: int = 0              # 当前总持仓
    sellable_qty: int = 0              # 当前可卖（T+1）
    lot_size: int = lot_mod.DEFAULT_LOT


@dataclass
class RiskOutcome:
    passed: bool
    results: list[RiskResult] = field(default_factory=list)
    fee: Optional[FeeBreakdown] = None
    amount: Optional[Decimal] = None

    @property
    def decline_reason(self) -> Optional[str]:
        failed = [r.reason for r in self.results if not r.ok and r.reason]
        return "；".join(failed) if failed else None

    @property
    def failed_names(self) -> list[str]:
        return [r.name for r in self.results if not r.ok]


def evaluate(inp: RiskInputs) -> RiskOutcome:
    results: list[RiskResult] = [
        session_mod.check_session(inp.calendar, inp.at),
        lot_mod.check_lot(inp.side, inp.qty, inp.lot_size),
        t1_mod.check_t1(inp.side, inp.qty, inp.sellable_qty),
        limit_mod.check_price_limit(inp.instrument, inp.side, inp.price, inp.prev_close),
    ]

    fee_result = fee_mod.check_fee_model(inp.fee_model)
    results.append(fee_result)

    amount: Optional[Decimal] = None
    breakdown: Optional[FeeBreakdown] = None
    if fee_result.ok:
        amount = (Decimal(inp.qty) * Decimal(str(inp.price))).quantize(Decimal("0.0001"))
        breakdown = fee_mod.compute_fee(inp.side, amount, inp.fee_model)
        results.append(
            funds_mod.check_funds(
                inp.side, amount, breakdown.total, inp.available, inp.position_qty, inp.qty
            )
        )

    return RiskOutcome(
        passed=all(r.ok for r in results),
        results=results,
        fee=breakdown,
        amount=amount,
    )
