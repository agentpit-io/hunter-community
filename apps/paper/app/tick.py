"""逐市场最小变动价位（tick size）—— `L05` 第 5 项。

**唯一来源**（红线 14：同一事实不许再写第二遍）。解析顺序：

| 级 | 从哪读 | 何时命中 |
|---|---|---|
| ① 标的 | `fin_instrument.tick_size` | 给了就用（人工 / 数据源对该标的的覆盖） |
| ② 市场 | `fin_market_rule.tick_spec` | `{"mode":"fixed","tick":…}` 或逐价位区间 `bands` |
| ③ 回落 | `fin_execution_model.tick_size` | 老数据（两级都没配）→ **回落全局值**，行为不变 |

- `bands`：按价格**升序**排的档，取**第一条 `price < lt` 命中**的档；末档无 `lt` = 上无界。
  港股就是这种形状（HKEX 证券最小价差表，见迁移 `0050` 的种子）。
- **算不出价格**（如只有日期、无最新价）→ 用最后一档（上无界）或 `fixed` 值 ——
  这是「按价查档」查不到时的确定性兜底，不是编价。

`tick_size` 一律 `Decimal`（价格全程精确，禁 float）。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional


def _dec(value) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def tick_from_spec(spec: Optional[dict], price: Optional[Decimal]) -> Optional[Decimal]:
    """从市场级 `tick_spec`（JSONB）按价查档。形状不认 → `None`（调用方回落全局值）。"""
    if not isinstance(spec, dict):
        return None
    mode = spec.get("mode")
    if mode == "fixed":
        return _dec(spec.get("tick"))
    if mode != "bands":
        return None
    bands = spec.get("bands") or []
    if not isinstance(bands, list):
        return None
    for band in bands:
        if not isinstance(band, dict):
            continue
        tick = _dec(band.get("tick"))
        if tick is None:
            continue
        lt = _dec(band.get("lt"))
        # 末档无 lt = 上无界；价格算不出（None）也落在这里。
        if lt is None or price is None or price < lt:
            return tick
    return None


def resolve_tick(instrument: Optional[dict], market_rule: Optional[dict], price,
                 *, fallback: Decimal) -> Decimal:
    """逐市场 / 按标的解析最小变动价位。`fallback` = 全局执行模型值（老数据回落）。

    **数值与全局值相同时回落全局值本身**（不是新建一个同值对象）：`Decimal("0.01")
    与 `Decimal("0.0100")` 数值相等、**刻度不同** —— 用解析值去 `quantize` 会把成交价
    渲染成 `10.01` 而不是老口径的 `10.0100`，改掉老行为（L05 明确「不改老行为」）。
    数值相等就用回落值，刻度随之保持一致。
    """
    px = _dec(price)
    inst_tick = _dec((instrument or {}).get("tick_size"))
    if inst_tick is not None and inst_tick > 0:
        return fallback if inst_tick == fallback else inst_tick
    spec_tick = tick_from_spec((market_rule or {}).get("tick_spec"), px)
    if spec_tick is not None and spec_tick > 0:
        return fallback if spec_tick == fallback else spec_tick
    return fallback
