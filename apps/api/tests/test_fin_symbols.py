# -*- coding: utf-8 -*-
"""R5 · 标的市场规范化（`app/services/fin/symbols.py`）—— **不连库、不连网**。

这一层存在的全部理由：让 `HK:00700` 与 `US:0700` 判为**不同标的**。
用例按**写法类别**铺（市场怎么写、代码怎么写、什么该拒绝），只加不删。

    cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_symbols.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_API_ROOT = Path(__file__).resolve().parents[1]
for _p in ("", "/", str(_API_ROOT)):
    while _p in sys.path:
        sys.path.remove(_p)
sys.path.insert(0, str(_API_ROOT))
_bad_app = sys.modules.get("app")
if _bad_app is not None and Path(getattr(_bad_app, "__file__", "") or "").parent == _API_ROOT:
    del sys.modules["app"]

from app.services.fin import symbols as S  # noqa: E402


# ── ① 真值表（任务书 §一.3 逐字）──────────────────────────────────────────

def test_task_book_truth_table():
    assert S.normalize("HK", "00700") == "HK:00700"
    assert S.normalize("US", "0700") == "US:0700"
    assert S.normalize("HK", "00700") != S.normalize("US", "0700")


def test_market_aliases_and_case():
    assert S.normalize("hk", " 00700 ") == "HK:00700"
    assert S.normalize(" us ", "aapl") == "US:AAPL"
    assert S.normalize("a", "600519") == "CN_A:600519"
    assert S.normalize("cn", "600519") == "CN_A:600519"
    assert S.normalize("cn_a", "600519") == "CN_A:600519"


def test_exchange_suffix_stripped():
    """`fin_trade.code` 里 `AAPL` 与 `AAPL.US` 两种形态都出现过 —— 同一只票一个身份。"""
    assert S.normalize("US", "AAPL.US") == "US:AAPL"
    assert S.normalize("HK", "00700.HK") == "HK:00700"
    assert S.normalize("CN_A", "600519.SH") == "CN_A:600519"


def test_no_zero_padding_and_no_leading_zero_strip():
    """**不做零填充、不剥前导零** —— `0700` 与 `700` 是两个不同的输入。"""
    assert S.normalize("US", "0700") == "US:0700"
    assert S.normalize("US", "700") == "US:700"
    assert S.normalize("HK", "700") == "HK:700"


# ── ② 该拒绝的（fail-closed，不静默保留原样）──────────────────────────────

@pytest.mark.parametrize("market", ["NASDAQ", "SH", "sz", "港股", "HK_US", ""])
def test_unknown_market_raises(market):
    with pytest.raises(ValueError):
        S.normalize(market, "00700")


@pytest.mark.parametrize("code", ["", "   ", None])
def test_empty_code_raises(code):
    with pytest.raises(ValueError):
        S.normalize("HK", code)


# ── ③ 批量：去重 + 排序（确定性落到 TEXT[]）──────────────────────────────

def test_normalize_many_dedups_and_sorts():
    got = S.normalize_many([("US", "0700"), ("HK", "00700"), ("CN_A", "601398"),
                            ("hk", "00700"), ("US", "0700")])
    assert got == ["CN_A:601398", "HK:00700", "US:0700"]


def test_normalize_many_skips_unusable_without_inventing():
    """认不出的（未知市场 / 空代码）**跳过**，不编一个假的。"""
    got = S.normalize_many([("NASDAQ", "AAPL"), ("HK", ""), ("HK", "00700")])
    assert got == ["HK:00700"]


# ── ④ 形态判定 / 解析（服务层校验 symbols 入参用）────────────────────────

def test_is_symbol_and_parse():
    assert S.is_symbol("HK:00700") is True
    assert S.is_symbol("US:0700") is True
    assert S.is_symbol("CN_A:601398") is True
    assert S.is_symbol("0700") is False          # 裸代码不是规范化标的
    assert S.is_symbol("HK:") is False
    assert S.is_symbol("") is False
    assert S.parse("HK:00700") == ("HK", "00700")
    assert S.parse("HK:00700 追高") is None
    assert S.parse("0700") is None


def test_normalized_output_always_is_symbol():
    for market, code in [("hk", "00700"), ("us", "aapl.us"), ("a", "600519")]:
        assert S.is_symbol(S.normalize(market, code)) is True
