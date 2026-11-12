"""撮合层：M-12 的四步链路（取快照 → 过规则 → 按快照撮合 → 记账并绑快照编号）。

口径的三条底线（任务书 §2）：

1. **成交价 = 委托到达时刻的快照价。** 不用当日均价、不用收盘价、不回填。
2. **每笔成交都绑一个快照编号**（`fin_trade.snapshot_id`，NOT NULL）。
3. **不可成交就挂单**，收盘仍未成交就撤单解冻。一期 `part_fill = false`：
   整笔成交或挂单，没有部分成交。
"""

from app.matching.engine import execute, expire_open_orders, match_open_orders
from app.matching.model import ExecutionModel, load_execution_model
from app.matching.pricing import MatchResult, OrderSpec, match

__all__ = [
    "execute",
    "expire_open_orders",
    "match_open_orders",
    "ExecutionModel",
    "load_execution_model",
    "MatchResult",
    "OrderSpec",
    "match",
]
