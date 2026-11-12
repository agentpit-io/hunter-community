"""风控第 4 条 · 价格带校验（按市场取「模式」）。

模式来自 `fin_market_rule.price_limit_mode`（设计 §3.3 第 2 条）：

| 模式 | 市场 | 行为 |
|---|---|---|
| `pct`  | `CN_A` | **沿用一期**：涨跌停幅度取 `fin_instrument.limit_up_pct/down_pct`，买入价 ≤ 涨停价、卖出价 ≥ 跌停价 |
| `none` | `HK` / `US` | **不做价格带校验**，但**必须留痕**（`checked=false`，文案写明「该市场未做价格带校验」）—— 不静默跳过 |
| `band` | （预留） | VCM / LULD，**本期不实现**：见到就**拒绝**，不做半成品（`总控规则 §六.10`：灰色按钮会骗人） |

`pct` 的口径一字不改（A 股不得回归）：

  · `fin_instrument` 里没有该标的 → 拒绝委托（`总控规则 §八`）；
  · 没有前收盘价 → 拒绝（算不出涨跌停价，也就判定不了）；
  · 涨跌停价 = `前收盘 × (1 ± 幅度)`，**四舍五入到分**（`ROUND_HALF_UP`，
    不能用 Python 默认的银行家舍入，否则 `10.125 → 10.12`）。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from app.risk.result import RiskResult

NAME = "price_limit"
_CENT = Decimal("0.01")

# 与 `fin_market_rule.price_limit_mode` 的 CHECK 同口径。
MODES = ("pct", "none", "band")


def _round_to(value, tick) -> Decimal:
    """对齐到最小变动价位（tick）。缺省 = 分（`_CENT`，A 股 tick）。

    `tick` 由 `app.tick.resolve_tick` 逐市场解析（`L05` 第 5 项）—— 涨跌停价
    与委托价一律落在该市场的合法价位上。A 股 tick=0.01 → 与本函数改造前逐位一致。
    """
    step = _CENT if tick is None else Decimal(str(tick))
    return Decimal(str(value)).quantize(step, rounding=ROUND_HALF_UP)


def limit_prices(prev_close, limit_up_pct, limit_down_pct, tick=None) -> tuple[Decimal, Decimal]:
    prev = Decimal(str(prev_close))
    up = _round_to(prev * (Decimal("1") + Decimal(str(limit_up_pct))), tick)
    down = _round_to(prev * (Decimal("1") - Decimal(str(limit_down_pct))), tick)
    return up, down


def check_price_limit(
    instrument: Optional[dict],
    side: str,
    price,
    prev_close,
    *,
    mode: str = "pct",
    tick=None,
) -> RiskResult:
    # ── 模式先判：`none` 的市场**根本不该去读** instrument / prev_close（它们可为空）──
    if mode == "none":
        return RiskResult.pass_(
            NAME,
            checked=False,
            price_limit_checked=False,
            mode=mode,
            note="该市场未做价格带校验（price_limit_mode=none：本地市场无每日涨跌幅限制）",
        )
    if mode == "band":
        return RiskResult.reject(
            NAME,
            "价格限制模式 band（VCM / LULD）本期尚未实现，拒绝委托",
            mode=mode, checked=False, price_limit_checked=False,
        )
    if mode != "pct":
        return RiskResult.reject(
            NAME, f"无法识别的价格限制模式 {mode!r}，拒绝委托",
            mode=mode, checked=False, price_limit_checked=False,
        )

    # ── mode == 'pct'：一期逻辑原样 ────────────────────────────────────────
    if instrument is None:
        return RiskResult.reject(
            NAME,
            "账本里没有该标的的元数据（fin_instrument 缺失），拒绝委托——绝不猜涨跌幅",
            mode=mode, checked=True, price_limit_checked=True,
        )
    if prev_close is None:
        return RiskResult.reject(
            NAME, "没有前收盘价，无法计算涨跌停价，拒绝委托",
            mode=mode, checked=True, price_limit_checked=True,
        )
    # 幅度可能为 NULL（0029 起港美股存 NULL 表示「无此值」）。`pct` 模式下遇到 NULL
    # = 配置与模式不一致（该标的按百分比限价但没给幅度）→ **拒绝**，不对 NULL 做算术
    # （拿 0 顶替会被读成「涨跌停 0%」，是编数字）。
    if instrument.get("limit_up_pct") is None or instrument.get("limit_down_pct") is None:
        return RiskResult.reject(
            NAME, "该标的没有涨跌停幅度（limit_up_pct / limit_down_pct 为空），拒绝委托",
            mode=mode, checked=False, price_limit_checked=False,
        )

    up, down = limit_prices(prev_close, instrument["limit_up_pct"],
                            instrument["limit_down_pct"], tick)
    px = Decimal(str(price))
    if side == "buy" and px > up:
        return RiskResult.reject(
            NAME, f"委托价 {px} 高于涨停价 {up}", price=str(px), limit_up=str(up),
            mode=mode, checked=True, price_limit_checked=True,
        )
    if side == "sell" and px < down:
        return RiskResult.reject(
            NAME, f"委托价 {px} 低于跌停价 {down}", price=str(px), limit_down=str(down),
            mode=mode, checked=True, price_limit_checked=True,
        )
    return RiskResult.pass_(
        NAME, limit_up=str(up), limit_down=str(down),
        mode=mode, checked=True, price_limit_checked=True,
    )
