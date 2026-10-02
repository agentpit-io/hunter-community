"""风控第 5 条 · 费用（成本模型版本化，**按市场**取行）。

费率来自 `fin_fee_model` 的一行（N1 已给它加 `market` / `currency`），**版本号随成交
落库**（`fin_trade.fee_model_version`）—— 报告里要能说清「这笔用的是哪一版费率」。

**印花税单边 / 双边由表决定，不写死在代码里**（设计 §3.3 第 1 条）：

| 市场 | `stamp_side` | 含义 |
|---|---|---|
| `CN_A` | `sell` | 印花税**仅卖出**收（一期口径，`0023`/`0025` 已成事实） |
| `HK` | `both` | 港股印花税**买卖双边** |
| `US` | `none` | 美股无印花税（SEC 规费等落在别的费用项上） |

三个费用项的**计算方式**仍是「金额 × 费率」，费率由表给；`stamp_side` 只决定这一项
在哪个方向收。A 股行为与改前逐位一致（`stamp_side` 缺省即 `sell`）。

金额全程 `Decimal`，四舍五入到 4 位小数（`NUMERIC(18,4)`）；用 `ROUND_HALF_UP`。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from app.risk.result import RiskResult

NAME = "fee"
_Q = Decimal("0.0001")

# 与 `fin_fee_model.stamp_side` 的 CHECK 同口径。
STAMP_SIDES = ("buy", "sell", "both", "none")
DEFAULT_STAMP_SIDE = "sell"       # 缺省 = 一期 A 股口径（仅卖出）


def _money(value: Decimal) -> Decimal:
    return value.quantize(_Q, rounding=ROUND_HALF_UP)


def _stamp_applies(stamp_side: str, side: str) -> bool:
    if stamp_side == "both":
        return True
    if stamp_side == "none":
        return False
    return stamp_side == side          # 'buy' / 'sell'


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
    stamp_side = str(model.get("stamp_side") or DEFAULT_STAMP_SIDE).lower()
    stamp = (_money(amount * Decimal(str(model["stamp_tax_pct"])))
             if _stamp_applies(stamp_side, side) else Decimal("0.0000"))
    transfer = _money(amount * Decimal(str(model["transfer_fee_pct"])))
    return FeeBreakdown(commission=commission, stamp_tax=stamp, transfer_fee=transfer)


def check_fee_model(model: Optional[dict]) -> RiskResult:
    if not model:
        return RiskResult.reject(NAME, "没有可用的费用模型（fin_fee_model 无记录），拒绝委托")
    for key in ("commission_pct", "commission_min", "stamp_tax_pct", "transfer_fee_pct", "version"):
        if model.get(key) is None:
            return RiskResult.reject(NAME, f"费用模型缺字段 {key}，拒绝委托")
    stamp_side = model.get("stamp_side")
    if stamp_side is not None and str(stamp_side).lower() not in STAMP_SIDES:
        # 不认识的印花税方向：**不放行**（猜错方向 = 少收/多收印花税，是编数据）。
        return RiskResult.reject(
            NAME, f"费用模型的印花税方向非法（stamp_side={stamp_side!r}），拒绝委托"
        )
    return RiskResult.pass_(NAME, version=model["version"],
                            market=model.get("market"), currency=model.get("currency"))
