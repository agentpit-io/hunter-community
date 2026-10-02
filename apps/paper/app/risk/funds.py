"""风控第 6 条 · 资金与持仓校验。

  · 买入：`可用资金 ≥ 买入金额 + 费用`；
  · 卖出：`持仓数量 ≥ 卖出数量`（T+1 的可卖量在第 2 条单独判）。

「冻结 / 解冻与持仓变动必须成对」是记账层的**不变量**，由 `ledger` 保证、
由对账（`recon.py`）每日复核——这里只判「够不够」，不负责记账。
"""

from __future__ import annotations

from decimal import Decimal

from app.risk.result import RiskResult

NAME = "funds"


def check_funds(
    side: str,
    amount: Decimal,
    fee_total: Decimal,
    available: Decimal,
    position_qty: int = 0,
    qty: int = 0,
) -> RiskResult:
    if side == "buy":
        required = Decimal(str(amount)) + Decimal(str(fee_total))
        if Decimal(str(available)) < required:
            return RiskResult.reject(
                NAME,
                f"可用资金不足：需要 {required} 元（含费用），可用 {available} 元",
                required=str(required),
                available=str(available),
            )
        return RiskResult.pass_(NAME, required=str(required))

    if side == "sell":
        if position_qty < qty:
            return RiskResult.reject(
                NAME,
                f"持仓不足：持有 {position_qty} 股，本次卖出 {qty} 股",
                position_qty=position_qty,
                qty=qty,
            )
        return RiskResult.pass_(NAME, position_qty=position_qty)

    return RiskResult.reject(NAME, f"无法识别的买卖方向 {side!r}")
