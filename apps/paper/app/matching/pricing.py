"""按快照撮合 —— **纯函数**，不连库、不取时间、不读 env。

这是「成交价完全可复现」的技术兑现：输入是（委托、快照、执行模型）三个值，
输出是一个价。同样的三个值调两次，结果逐位相同（有用例钉着）。

口径（任务书 §2 第③步，逐条对着写）：

| 委托 | 能否成交 | 成交价 |
|---|---|---|
| 限价买 | 快照价 **≤** 限价（快照价不劣于限价） | **快照价** |
| 限价卖 | 快照价 **≥** 限价 | **快照价** |
| 市价买 | 有对手价（卖一） | **卖一价 + 滑点** |
| 市价卖 | 有对手价（买一） | **买一价 − 滑点** |

「快照价」= `fin_snapshot.last_price`（`成交价 = 委托到达时刻的快照价`，任务书原文）。
市价单的对手价缺失时**退回 `last_price` 再加减滑点** —— 对手价是「更精确的那个」，
不是「唯一能用的那个」；退回的依据仍然是这张快照，不是别的价。

**快照不可成交（`tradable == False`）→ 一律 `pending`。** 宁可挂单，
也不用过期价成交（`09 §4.4`）。

一期 `part_fill = false`：只有 `filled` 与 `pending` 两个出口，没有部分成交。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from app.matching.model import ExecutionModel

FILLED = "filled"
PENDING = "pending"


@dataclass(frozen=True)
class OrderSpec:
    side: str                      # 'buy' / 'sell'
    qty: int
    price_type: str                # 'limit' / 'market'
    limit_price: Optional[Decimal] = None


@dataclass(frozen=True)
class MatchResult:
    outcome: str                   # 'filled' / 'pending'
    price: Optional[Decimal]       # 成交价（pending 时为 None）
    basis: str                     # 成交价的依据，写进日志与凭证
    pending_reason: Optional[str] = None

    @property
    def filled(self) -> bool:
        return self.outcome == FILLED


def _tick(value: Decimal, tick: Decimal) -> Decimal:
    """把价格对齐到最小变动价位（A 股 = 0.01）。用 ROUND_HALF_UP，
    不用 Python 默认的银行家舍入（同 `risk.price_limit` 的理由）。"""
    return value.quantize(tick, rounding=ROUND_HALF_UP)


def match(spec: OrderSpec, snapshot: dict, model: ExecutionModel) -> MatchResult:
    """按一张快照撮合一笔委托。**纯函数**。"""
    # ── 快照不可用 → 挂单 ────────────────────────────────────────────────
    if snapshot is None:
        return MatchResult(PENDING, None, "no_snapshot", "没有可用快照（行情未接通或断流），挂单等待")
    if not snapshot.get("tradable", False):
        flag = "missing_flag" if snapshot.get("missing_flag") else f"quality={snapshot.get('quality')}"
        return MatchResult(
            PENDING, None, "snapshot_not_tradable",
            f"快照不可用于成交（{flag}），挂单等待而非用过期价成交",
        )

    last = _dec(snapshot.get("last_price"))
    if last is None:
        return MatchResult(PENDING, None, "no_price", "快照没有最新价，挂单等待")

    if spec.price_type == "limit":
        limit = _dec(spec.limit_price)
        if limit is None:
            return MatchResult(PENDING, None, "no_limit_price", "限价委托没有限价，挂单等待")
        if spec.side == "buy":
            if last <= limit:
                return MatchResult(FILLED, last, "snapshot_last_price",
                                   None)
            return MatchResult(
                PENDING, None, "limit_not_reached",
                f"快照价 {last} 劣于买入限价 {limit}（买入要求快照价 ≤ 限价），挂单等待",
            )
        # sell
        if last >= limit:
            return MatchResult(FILLED, last, "snapshot_last_price", None)
        return MatchResult(
            PENDING, None, "limit_not_reached",
            f"快照价 {last} 劣于卖出限价 {limit}（卖出要求快照价 ≥ 限价），挂单等待",
        )

    # ── 市价单：对手价 + 滑点 ────────────────────────────────────────────
    slippage = model.slippage
    if spec.side == "buy":
        counterparty = _dec(snapshot.get("ask1_price"))
        basis = "ask1_price" if counterparty is not None else "snapshot_last_price"
        reference = counterparty if counterparty is not None else last
        price = _tick(reference + slippage, model.tick_size)
    else:
        counterparty = _dec(snapshot.get("bid1_price"))
        basis = "bid1_price" if counterparty is not None else "snapshot_last_price"
        reference = counterparty if counterparty is not None else last
        price = _tick(reference - slippage, model.tick_size)

    if price <= 0:
        return MatchResult(
            PENDING, None, "non_positive_price",
            f"按对手价 {reference} 加减滑点后得到非正价格 {price}，挂单等待",
        )
    return MatchResult(FILLED, price, f"{basis}+slippage" if basis.startswith(("ask", "bid"))
                       else "snapshot_last_price+slippage", None)


def _dec(value) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    return Decimal(str(value))
