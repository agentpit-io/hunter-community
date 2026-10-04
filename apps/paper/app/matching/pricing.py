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

## 成交量约束（`L05`）

`fin_param.liquidity_max_participation` 是「参与率」：成交股数**不许超过
`floor(盘口量 × 参与率)`**。`participation` 参数为 `None`（老项目没配）时**这条不生效**
——与加约束之前逐字节一致。约束生效时：

| 委托量 vs 上限 | `part_fill=false`（默认） | `part_fill=true` |
|---|---|---|
| ≤ 上限 | `filled`（整笔） | `filled`（整笔） |
| > 上限且上限 > 0 | `pending`（整笔或挂单，**不改数量**） | `partial`（成交 `上限` 股 + 剩余挂着） |
| > 上限且上限 = 0（盘口无量） | `pending` | `pending` |

**盘口量取哪一侧**：买入看卖盘挂量 `ask1_volume`，卖出看买盘挂量 `bid1_volume`
——那才是「此刻能接走多少」的量。**算不出（快照没有盘口量 / 港美股 `last_only`）
→ 不加约束**（`None`），不拿最新价顶替（红线 5：算不出就是算不出）。

## 期初（一期）

`part_fill = false`：只有 `filled` 与 `pending` 两个出口，没有部分成交。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Optional

from app.matching.model import ExecutionModel

FILLED = "filled"
PARTIAL = "partial"
PENDING = "pending"


@dataclass(frozen=True)
class OrderSpec:
    side: str                      # 'buy' / 'sell'
    qty: int
    price_type: str                # 'limit' / 'market'
    limit_price: Optional[Decimal] = None


@dataclass(frozen=True)
class MatchResult:
    outcome: str                   # 'filled' / 'partial' / 'pending'
    price: Optional[Decimal]       # 成交价（pending 时为 None）
    basis: str                     # 成交价的依据，写进日志与凭证
    pending_reason: Optional[str] = None
    qty: Optional[int] = None      # 'partial' 时实际成交股数；其余为 None（= 整笔委托量）
    cap_reason: Optional[str] = None   # 成交量约束的说明（留痕；未触发为 None）

    @property
    def filled(self) -> bool:
        return self.outcome == FILLED

    @property
    def partial(self) -> bool:
        return self.outcome == PARTIAL

    @property
    def matched(self) -> bool:
        """**有成交**（整笔或部分）。挂单/被拒为假。"""
        return self.outcome in (FILLED, PARTIAL)


def _tick(value: Decimal, tick: Decimal) -> Decimal:
    """把价格对齐到最小变动价位（A 股 = 0.01）。用 ROUND_HALF_UP，
    不用 Python 默认的银行家舍入（同 `risk.price_limit` 的理由）。"""
    return value.quantize(tick, rounding=ROUND_HALF_UP)


def available_volume(snapshot: dict, side: str) -> Optional[int]:
    """盘口量：买入看 `ask1_volume`（卖盘挂量），卖出看 `bid1_volume`（买盘挂量）。

    缺失 / 非数值 → `None`（**算不出**，不是 0）—— 调用方据此**不加约束**。
    """
    key = "ask1_volume" if side == "buy" else "bid1_volume"
    raw = (snapshot or {}).get(key)
    if raw is None or raw == "":
        return None
    try:
        return int(Decimal(str(raw)))
    except (ArithmeticError, ValueError, TypeError):
        return None


def liquidity_cap(snapshot: dict, side: str, participation) -> Optional[int]:
    """可成交股数上限 = `floor(盘口量 × 参与率)`。

    `participation is None`（未配）或盘口量算不出 → `None`（**无约束**）。
    盘口量 = 0 → `0`（此刻一股也接不走）。
    """
    if participation is None:
        return None
    vol = available_volume(snapshot, side)
    if vol is None:
        return None
    if vol <= 0:
        return 0
    cap = (Decimal(vol) * Decimal(str(participation))).to_integral_value(rounding=ROUND_FLOOR)
    return int(cap)


def match(spec: OrderSpec, snapshot: dict, model: ExecutionModel, *,
          participation=None) -> MatchResult:
    """按一张快照撮合一笔委托。**纯函数**。

    `participation`（参与率，来自 `fin_param.liquidity_max_participation`）为 `None`
    时行为与加约束之前**逐字节一致**。给了就按「盘口量 × 参与率」削量/挂单，
    见模块头「成交量约束」。
    """
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
                return _apply_cap(last, "snapshot_last_price", spec, snapshot, model, participation)
            return MatchResult(
                PENDING, None, "limit_not_reached",
                f"快照价 {last} 劣于买入限价 {limit}（买入要求快照价 ≤ 限价），挂单等待",
            )
        # sell
        if last >= limit:
            return _apply_cap(last, "snapshot_last_price", spec, snapshot, model, participation)
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
    final_basis = (f"{basis}+slippage" if basis.startswith(("ask", "bid"))
                   else "snapshot_last_price+slippage")
    return _apply_cap(price, final_basis, spec, snapshot, model, participation)


def _apply_cap(price: Decimal, basis: str, spec: OrderSpec, snapshot: dict,
               model: ExecutionModel, participation) -> MatchResult:
    """价格已经能成交，再过一道**成交量约束**（盘口量 × 参与率）。

    - 无约束（未配参与率 / 快照没有盘口量）→ 整笔 `filled`（与加约束之前一致）；
    - 委托量 ≤ 上限 → 整笔 `filled`；
    - 委托量 > 上限：`part_fill=true` → `partial`（成交上限股数）；否则 → `pending`
      （**不改数量**，整笔或挂单）。
    """
    cap = liquidity_cap(snapshot, spec.side, participation)
    if cap is None or spec.qty <= cap:
        return MatchResult(FILLED, price, basis, None)
    note = (f"成交量约束：盘口量 × 参与率 {participation} = {cap} 股"
            f" < 委托 {spec.qty} 股")
    if model.part_fill and cap > 0:
        return MatchResult(PARTIAL, price, basis, None, qty=cap, cap_reason=note)
    return MatchResult(
        PENDING, None, "liquidity_cap",
        note + "，整笔不成交（part_fill=false），挂单等待",
        cap_reason=note,
    )


def _dec(value) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    return Decimal(str(value))
