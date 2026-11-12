"""快照层：成交价的**唯一依据**（`05 §3.2` M-12 第①步）。

两条边界写在这个包的入口，改之前先读：

1. **`snapshot_time` 来自数据源，永远不取本机时间**（`09 §六-6`）。
   数据源没给时刻 → **不落这一行**（宁可不成交，也不拿一个编出来的时刻去成交）。
2. **只追加**：没有 UPDATE、没有 DELETE。错了用新的快照覆盖语义（按时间取最新），
   而不是改历史行。
"""

from app.snapshot.source import (
    HttpQuoteSource,
    NullQuoteSource,
    Quote,
    QuoteSource,
    get_source,
    set_source,
)
from app.snapshot.store import capture, get as get_snapshot, latest_for_code, snapshot_id_for

__all__ = [
    "Quote",
    "QuoteSource",
    "HttpQuoteSource",
    "NullQuoteSource",
    "get_source",
    "set_source",
    "capture",
    "get_snapshot",
    "latest_for_code",
    "snapshot_id_for",
]
