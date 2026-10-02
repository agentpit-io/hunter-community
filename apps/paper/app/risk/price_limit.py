"""风控第 4 条 · 涨跌停。

幅度来自 `fin_instrument`（`limit_up_pct` / `limit_down_pct`），**不猜**：
  · `fin_instrument` 里没有该标的 → 拒绝委托（`总控规则 §八`）；
  · 没有前收盘价 → 拒绝（算不出涨跌停价，也就判定不了）。

涨跌停价 = `前收盘 × (1 ± 幅度)`，按**四舍五入到分**（A 股是这么定的；
用 `ROUND_HALF_UP`，不能用 Python 默认的银行家舍入，否则 `10.125 → 10.12`）。
买入价 ≤ 涨停价、卖出价 ≥ 跌停价。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from app.risk.result import RiskResult

NAME = "price_limit"
_CENT = Decimal("0.01")


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)


def limit_prices(prev_close, limit_up_pct, limit_down_pct) -> tuple[Decimal, Decimal]:
    prev = Decimal(str(prev_close))
    up = _money(prev * (Decimal("1") + Decimal(str(limit_up_pct))))
    down = _money(prev * (Decimal("1") - Decimal(str(limit_down_pct))))
    return up, down


def check_price_limit(
    instrument: Optional[dict],
    side: str,
    price,
    prev_close,
) -> RiskResult:
    if instrument is None:
        return RiskResult.reject(
            NAME,
            "账本里没有该标的的元数据（fin_instrument 缺失），拒绝委托——绝不猜涨跌幅",
        )
    if prev_close is None:
        return RiskResult.reject(NAME, "没有前收盘价，无法计算涨跌停价，拒绝委托")

    up, down = limit_prices(prev_close, instrument["limit_up_pct"], instrument["limit_down_pct"])
    px = Decimal(str(price))
    if side == "buy" and px > up:
        return RiskResult.reject(
            NAME, f"委托价 {px} 高于涨停价 {up}", price=str(px), limit_up=str(up)
        )
    if side == "sell" and px < down:
        return RiskResult.reject(
            NAME, f"委托价 {px} 低于跌停价 {down}", price=str(px), limit_down=str(down)
        )
    return RiskResult.pass_(NAME, limit_up=str(up), limit_down=str(down))
