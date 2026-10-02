"""风控第 5 条 · 费用（成本模型版本化）。

费率来自 `fin_fee_model` 的一行，**版本号随成交落库**（`fin_trade.fee_model_version`）——
报告里要能说清「这笔用的是哪一版费率」（`09 §4.3`）。

A 股口径（`05 §3.2` M-11 逐字）：
  · 佣金 = `金额 × 万分之 2.5`，**最低 5 元**；
  · 印花税 = `金额 × 千分之 0.5`，**仅卖出**；
  · 过户费 = `金额 × 万分之 0.1`，买卖双边。

金额全程 `Decimal`，四舍五入到 4 位小数（`NUMERIC(18,4)`）；用 `ROUND_HALF_UP`。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from app.risk.result import RiskResult

NAME = "fee"
_Q = Decimal("0.0001")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_Q, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class FeeBreakdown:
    commission: Decimal
    stamp_tax: Decimal
    transfer_fee: Decimal

    @property
    def total(self) -> Decimal:
        return _money(self.commission + self.stamp_tax + self.transfer_fee)


def compute_fee(side: str, amount: Decimal, model: dict) -> FeeBreakdown:
    amount = Decimal(str(amount))
    commission = _money(amount * Decimal(str(model["commission_pct"])))
    floor = Decimal(str(model["commission_min"]))
    if commission < floor:
        commission = _money(floor)
    stamp = _money(amount * Decimal(str(model["stamp_tax_pct"]))) if side == "sell" else Decimal("0.0000")
    transfer = _money(amount * Decimal(str(model["transfer_fee_pct"])))
    return FeeBreakdown(commission=commission, stamp_tax=stamp, transfer_fee=transfer)


def check_fee_model(model: Optional[dict]) -> RiskResult:
    if not model:
        return RiskResult.reject(NAME, "没有可用的费用模型（fin_fee_model 无记录），拒绝委托")
    for key in ("commission_pct", "commission_min", "stamp_tax_pct", "transfer_fee_pct", "version"):
        if model.get(key) is None:
            return RiskResult.reject(NAME, f"费用模型缺字段 {key}，拒绝委托")
    return RiskResult.pass_(NAME, version=model["version"])
