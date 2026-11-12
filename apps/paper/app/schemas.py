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
    # 二期（N3）放开市场：交易所 / 板块加港美股取值（与 `0029` 的 CHECK 同一组）。
    exchange: Literal["SH", "SZ", "BJ", "HKEX", "NASDAQ", "NYSE", "AMEX", "CBOE"]
    board: Literal["main", "chinext", "star", "bse", "hk_main", "hk_gem", "us_main", "us_other"]
    is_st: bool = False
    # 港美股**没有每日涨跌幅限制** → 存 NULL（`0029` 已放开 NOT NULL）；A 股照旧传幅度。
    limit_up_pct: Optional[Decimal] = None
    limit_down_pct: Optional[Decimal] = None
    lot_size: int = 100
    listed_at: Optional[date] = None
    is_active: bool = True
    market: Literal["CN_A", "HK", "US"] = "CN_A"
    currency: Literal["CNY", "HKD", "USD"] = "CNY"
    source: str


class CalendarIn(BaseModel):
    trade_date: date
    is_trading: bool
    sessions: list[dict[str, str]] = Field(default_factory=list)
    note: Optional[str] = None
    # 0029 起日历主键是 (market, trade_date)；缺省 CN_A 保持旧调用方行为不变。
    market: Literal["CN_A", "HK", "US"] = "CN_A"


class FeeModelIn(BaseModel):
    version: str = Field(min_length=1)
    commission_pct: Decimal
    commission_min: Decimal
    stamp_tax_pct: Decimal
    transfer_fee_pct: Decimal
    # 0029 加市场/币种；0032 加印花税方向。缺省值保持一期 A 股口径不变。
    market: Literal["CN_A", "HK", "US"] = "CN_A"
    currency: Literal["CNY", "HKD", "USD"] = "CNY"
    stamp_side: Literal["buy", "sell", "both", "none"] = "sell"


class ValuationIn(BaseModel):
    as_of: datetime
    # N4：估值是**子账户级**的。缺省 None → 取项目 market_scope（A 股项目 = CN_A，行为不变）。
    market: Optional[Literal["CN_A", "HK", "US"]] = None


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


class CheckpointIn(BaseModel):
    """只写业务检查点，不改状态（`01方案 §11.2` 长计算中断按检查点恢复）。"""

    checkpoint: dict[str, Any] = Field(default_factory=dict)


class ExpireIn(BaseModel):
    """收盘撤单 / 过期撤单。`reason='close'` 撤当日全部挂单，`'validity'` 只撤过期的。"""

    at: datetime
    reason: Literal["close", "validity"] = "close"


class CancelIn(BaseModel):
    memo: Optional[str] = None


class HaltIn(BaseModel):
    """停牌状态位的登记（L05）。

    **数据源未接** —— 停牌由人工登记口置位（`halted_source` 如实写 `manual`）。
    `halted=false` 即解除停牌。
    """

    halted: bool = True
    reason: Optional[str] = None
    actor: str = "system"


class CorporateActionIn(BaseModel):
    """公司行为登记（L05 · **数据源未接** → 人工登记口）。

    - `dividend`：**必须**给 `cash_per_share`（每股现金，本币税前）；`ratio` 不填。
    - `split` / `bonus`：**必须**给 `ratio`（拆合股 = 拆后/拆前倍数；送股 = 每股送股数）；`cash_per_share` 不填。

    登记即应用（账务在同一事务里落 `fin_cash_ledger` / `fin_position`），并把这次
    实际应用的净影响写进事件行（`qty_delta` / `cash_delta`），供对账核。
    """

    code: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    action_type: Literal["dividend", "split", "bonus"]
    ex_date: date
    cash_per_share: Optional[Decimal] = Field(default=None, ge=0)
    ratio: Optional[Decimal] = Field(default=None, gt=0)
    memo: Optional[str] = None
    actor: str = "system"

    @model_validator(mode="after")
    def _required_fields_by_type(self) -> "CorporateActionIn":
        if self.action_type == "dividend":
            if self.cash_per_share is None:
                raise ValueError("分红必须提供 cash_per_share（每股现金）")
            if self.ratio is not None:
                raise ValueError("分红不接受 ratio（那是拆合股/送股的比例）")
        else:
            if self.ratio is None:
                raise ValueError("拆合股 / 送股必须提供 ratio")
            if self.cash_per_share is not None:
                raise ValueError("拆合股 / 送股不接受 cash_per_share（那是分红的每股现金）")
        return self


class AllowanceIn(BaseModel):
    """执行允许名单的一条登记（L06）—— 放行或撤销。

    `scope` / `subject` 的取值与语义见 `app/allowlist.py`（判据的唯一实现）。
    `granted_by` 必填 —— 谁给的权限要留痕（只追加表，撤销也是再追加一条）。
    """

    scope: Literal["project", "instrument", "tool"]
    subject: str = Field(min_length=1, max_length=200)
    granted_by: str = Field(min_length=1, max_length=200)
    note: Optional[str] = Field(default=None, max_length=500)
