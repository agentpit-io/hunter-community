"""风控 · 停牌检查（`L05` 第 3 项）。

标的停牌即**拒单**（买 / 卖都拒）——停牌时既成交不了也不该成交。

**数据源未接**：`fin_instrument.halted` 由**人工登记口**置位
（`POST /api/v1/instruments/{code}/halt`），`halted_source` 如实写 `manual`。
本模块只**读**这个状态位，不猜、不从别处推断停牌（红线 5：不许编造停牌）。

`instrument` 缺失时**放行**（交给别的风控去拒）—— 停牌这一条只有在「知道标的元数据」
时才有意义；标的都没有是另一条规则的事（`price_limit` / `session`），不在本模块重复报。
"""

from __future__ import annotations

from typing import Optional

from app.risk.result import RiskResult

NAME = "halt"


def check_halt(instrument: Optional[dict]) -> RiskResult:
    if instrument is None:
        return RiskResult.pass_(NAME, checked=False, note="没有标的元数据，停牌检查不适用")
    if instrument.get("halted"):
        code = instrument.get("code") or ""
        reason = instrument.get("halted_reason") or "（未填原因）"
        at = instrument.get("halted_at")
        src = instrument.get("halted_source") or "manual"
        return RiskResult.reject(
            NAME,
            f"{code} 已停牌，拒绝委托（停牌原因：{reason}；来源：{src}"
            + (f"；登记于 {at}" if at else "") + "）",
            halted=True, halted_reason=reason, halted_source=src,
        )
    return RiskResult.pass_(NAME, checked=True)
