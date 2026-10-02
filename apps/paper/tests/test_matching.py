"""撮合（M-12 第③步）：口径、滑点、可复现、不部分成交。

`match()` 是**纯函数**（不连库、不取时间、不读 env），所以这一组用例不需要账本库。
口径逐条对着任务书 §2 写：

| 委托 | 能否成交 | 成交价 |
|---|---|---|
| 限价买 | 快照价 ≤ 限价 | **快照价** |
| 限价卖 | 快照价 ≥ 限价 | **快照价** |
| 市价买 | 有对手价 | **卖一价 + 滑点** |
| 市价卖 | 有对手价 | **买一价 − 滑点** |

**成交价完全可复现**：同一（委托、快照、执行模型）调两次，逐位相同 ——
这是「同一快照 + 同一委托 → 同一成交价」的机器可验形式。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.matching.model import ExecutionModel
from app.matching.pricing import FILLED, PENDING, OrderSpec, match

MODEL = ExecutionModel(version="paper-model-v1", slippage_ticks=Decimal("1"),
                       tick_size=Decimal("0.01"), part_fill=False)

TWO_TICKS = ExecutionModel(version="m2", slippage_ticks=Decimal("2"),
                           tick_size=Decimal("0.01"), part_fill=False)


def snap(last="10.00", bid1=None, ask1=None, *, quality="ok", missing=False, tradable=True):
    return {
        "snapshot_id": "SNAP-TEST", "code": "600519",
        "last_price": None if last is None else Decimal(last),
        "bid1_price": None if bid1 is None else Decimal(bid1),
        "ask1_price": None if ask1 is None else Decimal(ask1),
        "prev_close": Decimal("10.00"),
        "quality": quality, "missing_flag": missing, "tradable": tradable,
    }


def limit(side, price, qty=100):
    return OrderSpec(side=side, qty=qty, price_type="limit", limit_price=Decimal(price))


def market(side, qty=100):
    return OrderSpec(side=side, qty=qty, price_type="market")


# ── 限价单 ────────────────────────────────────────────────────────────────

def test_limit_buy_fills_at_snapshot_price_when_not_worse():
    r = match(limit("buy", "10.00"), snap(last="10.00"), MODEL)
    assert r.filled and r.price == Decimal("10.00")
    assert r.basis == "snapshot_last_price"


def test_limit_buy_fills_better_than_limit():
    """快照价低于限价 = 对买方更优 → 成交，且**成交在快照价上**（不是限价）。"""
    r = match(limit("buy", "10.50"), snap(last="10.40"), MODEL)
    assert r.filled and r.price == Decimal("10.40")


def test_limit_buy_does_not_fill_when_snapshot_is_worse():
    r = match(limit("buy", "10.00"), snap(last="10.01"), MODEL)
    assert r.outcome == PENDING and r.price is None
    assert "劣于买入限价" in r.pending_reason


def test_limit_sell_fills_at_snapshot_price_when_not_worse():
    r = match(limit("sell", "10.00"), snap(last="10.00"), MODEL)
    assert r.filled and r.price == Decimal("10.00")


def test_limit_sell_does_not_fill_below_limit():
    r = match(limit("sell", "10.00"), snap(last="9.99"), MODEL)
    assert r.outcome == PENDING
    assert "劣于卖出限价" in r.pending_reason


# ── 市价单：对手价 ± 滑点 ─────────────────────────────────────────────────

def test_market_buy_pays_ask_plus_one_tick():
    r = match(market("buy"), snap(last="10.00", ask1="10.02", bid1="10.00"), MODEL)
    assert r.filled and r.price == Decimal("10.03")      # 10.02 + 0.01
    assert r.basis == "ask1_price+slippage"


def test_market_sell_receives_bid_minus_one_tick():
    r = match(market("sell"), snap(last="10.00", bid1="9.98", ask1="10.00"), MODEL)
    assert r.filled and r.price == Decimal("9.97")       # 9.98 − 0.01
    assert r.basis == "bid1_price+slippage"


def test_slippage_scales_with_the_execution_model():
    """滑点是「N 个最小变动价位」，换模型就换价 —— 参数在表里，不在代码里。"""
    r = match(market("buy"), snap(last="10.00", ask1="10.02"), TWO_TICKS)
    assert r.price == Decimal("10.04")                   # 10.02 + 2 × 0.01


def test_market_buy_falls_back_to_last_price_without_book():
    r = match(market("buy"), snap(last="10.00"), MODEL)
    assert r.filled and r.price == Decimal("10.01")
    assert r.basis == "snapshot_last_price+slippage"


def test_market_with_no_book_and_no_price_stays_pending():
    r = match(market("buy"), snap(last=None, ask1=None), MODEL)
    assert r.outcome == PENDING


# ── 快照不可用 → 一律挂单 ────────────────────────────────────────────────

def test_missing_snapshot_parks_everything():
    assert match(limit("buy", "10.00"), None, MODEL).outcome == PENDING
    assert match(market("buy"), None, MODEL).outcome == PENDING


def test_stale_snapshot_never_fills():
    stale = snap(last="10.00", quality="stale", missing=True, tradable=False)
    r = match(limit("buy", "10.00"), stale, MODEL)
    assert r.outcome == PENDING and r.basis == "snapshot_not_tradable"
    assert "快照不可用于成交" in r.pending_reason


def test_missing_flag_alone_parks_even_if_quality_says_ok():
    """`tradable` 是唯一判据 —— 三个条件（不缺失 / quality ok / 有价）缺一不可。"""
    odd = snap(last="10.00", quality="ok", missing=True, tradable=False)
    assert match(limit("buy", "10.00"), odd, MODEL).outcome == PENDING


# ── 可复现 & 一期不部分成交 ───────────────────────────────────────────────

@pytest.mark.parametrize("spec,snapshot,model", [
    (limit("buy", "10.50"), snap(last="10.40"), MODEL),
    (limit("sell", "9.50"), snap(last="9.60"), MODEL),
    (market("buy"), snap(last="10.00", ask1="10.02"), MODEL),
    (market("sell"), snap(last="10.00", bid1="9.98"), TWO_TICKS),
])
def test_matching_is_reproducible(spec, snapshot, model):
    """同一快照 + 同一委托 → 同一成交价。调两次逐位相同，写进凭证才敢让人核对。"""
    first, second = match(spec, snapshot, model), match(spec, snapshot, model)
    assert first == second
    assert first.price == second.price


def test_no_partial_fill_in_phase_one():
    """一期只有两个出口：整笔成交或挂单。没有 partially_filled。"""
    for result in (match(limit("buy", "10.00"), snap(last="10.00"), MODEL),
                   match(limit("buy", "10.00"), snap(last="10.10"), MODEL),
                   match(market("buy"), snap(last="10.00"), MODEL)):
        assert result.outcome in (FILLED, PENDING)


def test_market_price_aligns_to_tick():
    """价格对齐到最小变动价位（0.01）；对手价带半厘时取整到分。"""
    r = match(market("buy"), snap(last="10.00", ask1="10.005"), MODEL)
    assert r.price == Decimal("10.02")               # 10.015 → ROUND_HALF_UP → 10.02


def test_negative_or_zero_result_parks_instead_of_filling():
    """减掉滑点之后价格 ≤ 0（对手价低得离谱）→ 不成交，挂单。"""
    r = match(market("sell"), snap(last="10.00", bid1="0.005"), MODEL)
    assert r.outcome == PENDING and r.basis == "non_positive_price"


def test_execution_model_slippage_property():
    assert MODEL.slippage == Decimal("0.01")
    assert TWO_TICKS.slippage == Decimal("0.02")
