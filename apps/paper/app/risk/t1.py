"""风控第 2 条 · 可卖数量（按市场的可卖规则）。

当日买入的股票是否计入可卖，**按市场**：
  · `t_plus_n`（A 股，`n=1`）：当日买入**不可卖** —— `fin_position.sellable_qty`
    由记账层的日切（`ledger.confirm_t1`）维护，卖出量必须 ≤ 可卖量；
  · `same_day`（港 / 美股）：**当日买入当日可卖** —— 可卖量 = 当前总持仓
    （`n=0`，忽略 `sellable_qty`）。

**判断只看可卖量，不看 `qty`** —— 拿总持仓当可卖是 A 股最容易犯的错
（`t_plus_n` 下 `effective_sellable` 就是 `sellable_qty`，不是 `position_qty`）。

挂单占用的股数由调用方（`matching/engine.py`）在传入前从可卖量里扣掉 —— 本模块
只判「这批股够不够卖」，不负责算占用（占用是 `open_sell_committed` 的事）。
"""

from __future__ import annotations

from app.risk.result import RiskResult

NAME = "t1"

# 与 `fin_market_rule.sellable_rule` 的 CHECK 同口径。
RULES = ("t_plus_n", "same_day")


def effective_sellable(rule: str, position_qty: int, sellable_qty: int) -> int:
    """按市场规则算出「此刻真正能卖多少股」。**纯函数**。

    · `same_day`  → 总持仓全可卖（`n=0`）；
    · `t_plus_n`  → 只看记账层维护的 `sellable_qty`（当日买入不计入）。
    """
    if rule == "same_day":
        return max(0, int(position_qty or 0))
    return max(0, int(sellable_qty or 0))


def check_t1(
    side: str,
    qty: int,
    sellable_qty: int,
    *,
    rule: str = "t_plus_n",
    days: int = 1,
    position_qty: int = 0,
) -> RiskResult:
    if side == "buy":
        return RiskResult.pass_(NAME, sellable_qty=sellable_qty)
    if side != "sell":
        return RiskResult.reject(NAME, f"无法识别的买卖方向 {side!r}")
    if rule not in RULES:
        # 不认识的规则：**不放行**（拿别的市场规则顶替就是编数据）。
        return RiskResult.reject(NAME, f"无法识别的可卖规则 {rule!r}，拒绝委托", rule=rule)

    avail = effective_sellable(rule, position_qty, sellable_qty)
    if qty > avail:
        label = "T+0（当日买卖）" if rule == "same_day" else f"T+{int(days)}"
        hint = "" if rule == "same_day" else "当日买入的股票要到次日才可卖"
        return RiskResult.reject(
            NAME,
            f"可卖数量不足（{label}）：本次卖出 {qty} 股，可卖 {avail} 股"
            + (f"（{hint}）" if hint else ""),
            qty=qty,
            sellable_qty=avail,
            rule=rule,
        )
    return RiskResult.pass_(NAME, sellable_qty=avail, rule=rule)
