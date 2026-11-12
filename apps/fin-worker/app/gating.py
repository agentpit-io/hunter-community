"""把「日历读数 → 走不走」抽成一个纯函数，方便单测与复用。

`05 §3.2` M-14 的验收项之一：「**交易日历正确（非交易日不触发）**」。
最容易写错的地方是「日历缺数据」这一种 —— 把它当成交易日（默认放行）会让
节假日照常下单，而且**不报错**。这里把它显式拆成三种，缺数据走 `unknown` 分支。
"""

from __future__ import annotations

PROCEED = "proceed"
SKIP_NON_TRADING = "non_trading_day"
SKIP_UNKNOWN = "calendar_unknown"


def gate(cal: dict) -> tuple[str, str | None]:
    """返回 `(动作, 说明)`。`动作 != PROCEED` 时**不得**触发任何交易。

    | 输入 | 动作 |
    |---|---|
    | `known=True, trading=True` | `proceed` |
    | `known=True, trading=False` | `non_trading_day`（跳过，不告警） |
    | `known=False`（没有这一天的日历） | `calendar_unknown`（跳过 + **告警**） |
    """
    if not cal.get("known"):
        return SKIP_UNKNOWN, "交易日历缺失，按「未知 ≠ 交易日」处理，未触发"
    if not cal.get("trading"):
        return SKIP_NON_TRADING, cal.get("note") or "非交易日"
    return PROCEED, None
