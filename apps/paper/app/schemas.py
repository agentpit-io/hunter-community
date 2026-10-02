"""请求体模型。金额字段一律 `Decimal`（pydantic 从字符串解析，**不经过 float**）。

`source` 与 `actor` **一期就要能被传入**（`09 §4.5`：二期人工委托即插即用），
默认 `'ai'` / `'system'`。注意：它们只随委托落库供统计，**风控不看它们**
（`09 §六-10`）。

`qty` 在这里只约束 `> 0`：`qty ≤ 0` 是格式错误（422），不是风控拒绝——
风控拒绝要靠 `fin_order.decline_reason` 留痕，而 `fin_order.qty` 有 `CHECK (qty > 0)`，
落不进去。数量是否为整手由风控第 3 条判，会产生一条 `rejected` 委托。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class SnapshotIn(BaseModel):
    """成交价的依据。M2 允许占位快照；M3 起必须是采集到的真快照。"""

    snapshot_id: str = Field(min_length=1)
    snapshot_time: datetime
    source: Optional[str] = None
    last_price: Decimal
    prev_close: Optional[Decimal] = None
    bid1_price: Optional[Decimal] = None
    ask1_price: Optional[Decimal] = None
    quality: Literal["ok", "stale", "missing"] = "ok"
    missing_flag: bool = False


class OrderIn(BaseModel):
    project_id: str
    code: str = Field(min_length=1)
    side: Literal["buy", "sell"]
    qty: int = Field(gt=0)
    price_type: Literal["limit", "market"] = "limit"
    limit_price: Optional[Decimal] = None
    source: Literal["ai", "human", "human_confirmed"] = "ai"
    actor: str = "system"
    intent_ref: Optional[dict[str, Any]] = None
    decision_ref: Optional[str] = None
    valid_until: Optional[datetime] = None
    snapshot: SnapshotIn


class InstrumentIn(BaseModel):
    code: str = Field(min_length=1)
    name: str
    exchange: Literal["SH", "SZ", "BJ"]
    board: Literal["main", "chinext", "star", "bse"]
    is_st: bool = False
    limit_up_pct: Decimal
    limit_down_pct: Decimal
    lot_size: int = 100
    listed_at: Optional[date] = None
    is_active: bool = True
    source: str


class CalendarIn(BaseModel):
    trade_date: date
    is_trading: bool
    sessions: list[dict[str, str]] = Field(default_factory=list)
    note: Optional[str] = None


class FeeModelIn(BaseModel):
    version: str = Field(min_length=1)
    commission_pct: Decimal
    commission_min: Decimal
    stamp_tax_pct: Decimal
    transfer_fee_pct: Decimal


class ValuationIn(BaseModel):
    as_of: datetime
