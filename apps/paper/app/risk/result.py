"""风控结果的统一形状。

`reason` 是**给人看的中文**，会原样写进 `fin_order.decline_reason`——
二期的人工委托复用同一条路径，所以文案里不能出现内部黑话或英文状态码。
`detail` 是给日志与单测的结构化补充（数字都用 `Decimal`/`str`，不用 float）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RiskResult:
    name: str          # 'session' / 't1' / 'lot' / 'price_limit' / 'fee' / 'funds'
    ok: bool
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def pass_(name: str, **detail: Any) -> "RiskResult":
        return RiskResult(name=name, ok=True, reason=None, detail=detail)

    @staticmethod
    def reject(name: str, reason: str, **detail: Any) -> "RiskResult":
        return RiskResult(name=name, ok=False, reason=reason, detail=detail)
