"""美股手续费(阶梯式,按月交易量分级)—— 用户 2026-09-12 指定的口径。

用户给的原话是两档:「≤30 万股约 0.0035 美元/股,更高量级逐步降至 0.0005 美元/股;
每笔最低约 0.35 美元,最高为交易价值的 1%」。中间几档按 IBKR Tiered(美股)的公开档位补全 ——
那正是这套口径的出处,首档和末档都对得上。**档位是假设,不是用户给的**,
要改成别家券商的表就只动 TIERS 这一处。

三条口径,顺序不能乱:

1. **档位按「当月已成交股数」定**,不是按当笔大小。当月累计跨过 30 万股之后,
   之后的成交才按下一档算 —— 已经成交过的不回头重算(IBKR 的 Tiered 就是这样)。
2. **最低每笔 0.35 美元**。几股的小单实际付的是这个数,不是 shares × 费率。
3. **上限是成交金额的 1%,且上限压过下限**。一笔 10 美元的成交,上限 0.1 美元 < 最低 0.35,
   最后收 0.1。写成 min(max(fee, 0.35), 1%) 而不是 max(min(...), 0.35),差别就在这种小单上。

买和卖各算一次 —— 一个持仓周期有几条腿就收几次,不是一笔一次。
"""

# (当月累计股数上限, 每股费率)。None = 最后一档,没有上限。
TIERS: tuple[tuple[int | None, float], ...] = (
    (300_000, 0.0035),
    (3_000_000, 0.0020),
    (20_000_000, 0.0015),
    (100_000_000, 0.0010),
    (None, 0.0005),
)
MIN_PER_ORDER = 0.35          # 每笔最低
MAX_PCT_OF_VALUE = 0.01       # 每笔最高 = 成交金额的 1%


def rate_for(month_shares: float) -> float:
    """当月已成交 month_shares 股之后,下一笔按哪一档的费率算。"""
    for cap, rate in TIERS:
        if cap is None or month_shares < cap:
            return rate
    return TIERS[-1][1]


def fee_for(shares: float, price: float, month_shares_before: float = 0.0) -> float:
    """一笔成交的手续费。month_shares_before = 这笔之前当月已成交的股数。"""
    if not shares or not price or shares <= 0 or price <= 0:
        return 0.0
    value = shares * price
    fee = shares * rate_for(month_shares_before)
    fee = max(fee, MIN_PER_ORDER)
    fee = min(fee, value * MAX_PCT_OF_VALUE)      # 上限压过下限,见模块开头第 3 条
    return round(fee, 4)


def fees_by_month(fills: list) -> dict:
    """一串成交 → 每笔的手续费。

    fills  [(key, 年月, 股数, 价格), …] 必须**按成交时间升序**,
           因为档位取决于当月在这笔之前已经成交了多少股。
    → {key: 手续费}
    """
    used: dict = {}
    out: dict = {}
    for key, ym, shares, price in fills:
        before = used.get(ym, 0.0)
        out[key] = fee_for(shares, price, before)
        used[ym] = before + (shares or 0)
    return out
