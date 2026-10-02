"""风控第 3 条 · 整手。

A 股**买入恒为 100 股整数倍**（`09 §二` 数量约定 + `§六-7`）。
卖出允许零股：清仓一只被送股 / 拆股弄出零股的持仓时，`qty` 不可能仍是 100 的倍数，
硬卡整手会让用户永远清不掉这一手。所以规则是**分方向的**：

  · 买入 `qty % lot_size == 0` 且 `qty > 0`；
  · 卖出 `qty > 0` 即可（不要求整手）。

`lot_size` 从 `fin_instrument.lot_size` 取（默认 100），不是写死的常量。
"""

from __future__ import annotations

from app.risk.result import RiskResult

NAME = "lot"
DEFAULT_LOT = 100


def check_lot(side: str, qty: int, lot_size: int = DEFAULT_LOT) -> RiskResult:
    if qty is None or qty <= 0:
        return RiskResult.reject(NAME, f"委托数量必须大于 0（收到 {qty}）")
    if side == "buy":
        if lot_size <= 0:
            return RiskResult.reject(NAME, f"标的一手股数非法（{lot_size}），拒绝委托")
        if qty % lot_size != 0:
            return RiskResult.reject(
                NAME,
                f"买入数量必须是 {lot_size} 股的整数倍（收到 {qty} 股）",
                qty=qty,
                lot_size=lot_size,
            )
    elif side != "sell":
        return RiskResult.reject(NAME, f"无法识别的买卖方向 {side!r}")
    return RiskResult.pass_(NAME, qty=qty, lot_size=lot_size)
