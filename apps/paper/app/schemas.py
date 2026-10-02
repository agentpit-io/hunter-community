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

from pydantic import BaseModel, Field, model_validator


class SnapshotIn(BaseModel):
    """**没有这个模型了** —— M3 起快照由服务端自己采集（`app/snapshot/`）。

    客户端不能提供快照：一旦能提供，成交价就由调用方决定，「成交价 = 委托到达
    时刻的快照价」这句口径就没有约束力了。它带着 `snapshot_time`，
    而那个时刻必须是数据源给的（`09 §六-6`）—— 调用方给的时刻不是数据源的时刻。
    """


class OrderIn(BaseModel):
    """一笔委托。

    **没有 `snapshot` 字段**（见上）：快照由服务端按 `code` 现取，
    取不到就拒绝，不由调用方喂。
    """

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
    # ── M-13：命令级幂等与账户版本（`01方案 §11.1`）─────────────────────
    idempotency_key: Optional[str] = Field(default=None, max_length=200)
    expected_version: Optional[int] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _limit_needs_price(self) -> "OrderIn":
        """限价单必须带限价；市价单不该带。

        这是**格式校验**（422），不是风控拒绝 —— 风控拒绝要落一条 `fin_order`
        留痕，而一张「限价但没有限价」的单连价格都没有，落不出有意义的委托。
        """
        if self.price_type == "limit" and self.limit_price is None:
            raise ValueError("限价委托必须提供 limit_price")
        if self.price_type == "market" and self.limit_price is not None:
            raise ValueError("市价委托不接受 limit_price（成交价由快照的对手价加滑点决定）")
        return self


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


class ExecutionModelIn(BaseModel):
    """执行模型（滑点 / 最小变动价位 / 部分成交开关）。`09 §4.3`。

    `slippage_ticks` 的单位是**最小变动价位**，`tick_size` 才是价格 ——
    「1 个最小变动价位」= `slippage_ticks=1, tick_size=0.01` = 0.01 元。
    """

    version: str = Field(min_length=1)
    slippage_ticks: Decimal = Field(ge=0)
    tick_size: Decimal = Field(gt=0)
    part_fill: bool = False
    note: Optional[str] = None


class JobIn(BaseModel):
    """提交一个长任务（`01方案 §10.4`）。"""

    type: str = Field(min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)
    project_id: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, max_length=200)


class JobSucceedIn(BaseModel):
    result_ref: str = Field(min_length=1)
    checkpoint: Optional[dict[str, Any]] = None


class ExpireIn(BaseModel):
    """收盘撤单 / 过期撤单。`reason='close'` 撤当日全部挂单，`'validity'` 只撤过期的。"""

    at: datetime
    reason: Literal["close", "validity"] = "close"


class CancelIn(BaseModel):
    memo: Optional[str] = None
