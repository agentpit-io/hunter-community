"""风控第 6 条 · 资金与持仓校验（按市场的子账户与本币）。

  · 买入：`该市场子账户可用资金 ≥ 买入金额 + 费用`（**金额与币种一起取**）；
  · 卖出：`持仓数量 ≥ 卖出数量`（可卖量在第 2 条按市场规则单独判）。

「冻结 / 解冻与持仓变动必须成对」是记账层的**不变量**，由 `ledger` 保证、
由对账（`recon.py`）每日复核——这里只判「够不够」，不负责记账。

**币种**：金额一律是该市场的本币（`fin_market_rule.currency`）。报错文案按币种出
（A 股沿用一期原话「元」，港 / 美股用 `HKD` / `USD`）—— 不写死人民币，
但**也不做任何折算**（账本内永不换算，设计 §3.1 A 方案）。

> 「该项目 → 该市场子账户」的一挂多在 N4 落地（`fin_account.market` 已在 N1 加好）。
> 本模块只保证**校验用的是该市场的资金与币种**；`available` 由调用方按市场取好后传入。
"""

from __future__ import annotations

from decimal import Decimal

from app.risk.result import RiskResult

NAME = "funds"


def money_label(currency) -> str:
    """币种 → 文案单位。人民币（或缺省）沿用一期「元」，其余用币种代码。"""
    code = str(currency or "").strip().upper()
    if not code or code == "CNY":
        return "元"
    return code


def check_funds(
    side: str,
    amount: Decimal,
    fee_total: Decimal,
    available: Decimal,
    position_qty: int = 0,
    qty: int = 0,
    *,
    currency=None,
) -> RiskResult:
    if side == "buy":
        required = Decimal(str(amount)) + Decimal(str(fee_total))
        if Decimal(str(available)) < required:
            unit = money_label(currency)
            return RiskResult.reject(
                NAME,
                f"可用资金不足：需要 {required} {unit}（含费用），可用 {available} {unit}",
                required=str(required),
                available=str(available),
                currency=str(currency or "CNY"),
            )
        return RiskResult.pass_(NAME, required=str(required), currency=str(currency or "CNY"))

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
