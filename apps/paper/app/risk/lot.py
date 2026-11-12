"""风控第 3 条 · 整手（按市场取每手规则）。

每手股数**按市场**取（`fin_market_rule.lot_rule`）：

| `lot_rule` | 含义 | 每手来源 |
|---|---|---|
| `fixed` | 固定手数 | `lot_fixed`（A 股 = 100，`09 §二` 数量约定 + `§六-7`） |
| `per_instrument` | 按标的 | `fin_instrument.lot_size`（港股每只不同，港交所官方导出，见 N0 报告 S7） |
| `one` | 1 股起 | 恒为 1（美股，事实） |

卖出允许零股：清仓一只被送股 / 拆股弄出零股的持仓时，`qty` 不可能仍是整手的倍数，
硬卡整手会让用户永远清不掉这一手。所以规则是**分方向的**：

  · 买入 `qty % lot_size == 0` 且 `qty > 0`；
  · 卖出 `qty > 0` 即可（不要求整手）。

`lot_size` 由 `matching/engine.py` 用 `resolve_lot_size` 按上面那张表算好再传进来；
算不出（规则不认识 / `per_instrument` 但标的没手数）→ 传 `None`，本条**拒绝**，不猜。
"""

from __future__ import annotations

from typing import Optional

from app.risk.result import RiskResult

NAME = "lot"
DEFAULT_LOT = 100          # 兼容默认值（A 股权重的兜底，仅用于直接调用与老用例）

# 与 `fin_market_rule.lot_rule` 的 CHECK 同口径。
RULES = ("fixed", "per_instrument", "one")


def resolve_lot_size(rule: str, lot_fixed=None, instrument_lot=None) -> Optional[int]:
    """按市场规则算出「这一手多少股」。**算不出返回 `None`**（调用方据此拒绝，不猜）。

    · `fixed`          → `lot_fixed`（必须 > 0，否则 `None`）；
    · `per_instrument` → 标的手数（缺失 / ≤ 0 → `None`）；
    · `one`            → 1（美股，事实）；
    · 其它             → `None`（不认识的规则不顶替）。
    """
    if rule == "one":
        return 1
    if rule == "fixed":
        try:
            n = int(lot_fixed)
        except (TypeError, ValueError):
            return None
        return n if n > 0 else None
    if rule == "per_instrument":
        try:
            n = int(instrument_lot)
        except (TypeError, ValueError):
            return None
        return n if n > 0 else None
    return None


def check_lot(side: str, qty: int, lot_size: Optional[int] = DEFAULT_LOT) -> RiskResult:
    if qty is None or qty <= 0:
        return RiskResult.reject(NAME, f"委托数量必须大于 0（收到 {qty}）")
    if side == "buy":
        if lot_size is None:
            return RiskResult.reject(NAME, "标的一手股数未知（lot_rule=per_instrument 但无手数），拒绝委托")
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
