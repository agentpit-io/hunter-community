"""执行模型：滑点与部分成交开关，参数化（`09 §4.3` 的 `fin_execution_model`）。

一期默认行 `paper-model-v1`：`slippage_ticks = 1`、`tick_size = 0.01`、
`part_fill = false` —— 值来自 `总控规则 §八` 的默认决策，由迁移 `0025` 种下，
**不是代码里的魔法数**。要改滑点改表里的行（或者插一个新版本行），不改代码。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class ExecutionModel:
    version: str
    slippage_ticks: Decimal
    tick_size: Decimal
    part_fill: bool
    note: Optional[str] = None

    @property
    def slippage(self) -> Decimal:
        """滑点 = `slippage_ticks × tick_size`（一个**价格**，不是比例）。"""
        return (self.slippage_ticks * self.tick_size)


def load_execution_model(cur, version: Optional[str] = None) -> Optional[ExecutionModel]:
    if version:
        cur.execute("SELECT * FROM fin_execution_model WHERE version = %s", (version,))
    else:
        cur.execute(
            "SELECT * FROM fin_execution_model ORDER BY effective_from DESC, version DESC LIMIT 1"
        )
    row = cur.fetchone()
    if not row:
        return None
    return ExecutionModel(
        version=row["version"],
        slippage_ticks=Decimal(str(row["slippage_ticks"])),
        tick_size=Decimal(str(row["tick_size"])),
        part_fill=bool(row["part_fill"]),
        note=row.get("note"),
    )
