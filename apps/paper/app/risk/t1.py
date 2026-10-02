"""风控第 2 条 · T+1。

当日买入的股票不计入 `fin_position.sellable_qty`（由记账层维护），卖出量必须
≤ 可卖量。买入不受这条限制。**判断只看 `sellable_qty`，不看 `qty`**——
拿总持仓当可卖是 A 股最容易犯的错。
"""

from __future__ import annotations

from app.risk.result import RiskResult

NAME = "t1"


def check_t1(side: str, qty: int, sellable_qty: int) -> RiskResult:
    if side == "buy":
        return RiskResult.pass_(NAME, sellable_qty=sellable_qty)
    if side != "sell":
        return RiskResult.reject(NAME, f"无法识别的买卖方向 {side!r}")
    if qty > sellable_qty:
        return RiskResult.reject(
            NAME,
            f"可卖数量不足（T+1）：本次卖出 {qty} 股，可卖 {sellable_qty} 股",
            qty=qty,
            sellable_qty=sellable_qty,
        )
    return RiskResult.pass_(NAME, sellable_qty=sellable_qty)
