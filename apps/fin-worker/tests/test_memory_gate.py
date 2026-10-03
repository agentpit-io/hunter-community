"""R3 · 经验消费点：**规范化标的的精确匹配**（`app/strategy/memory_gate.py`）。

这一轮经验对决策的唯一作用就是「命中本标的的已验证结论 → 本时点不下单」，
所以判据必须在**写法类别**上过关，而不是「报上来的那一句过了就行」：
`plan/R3.md` §一.4 点名不许做文本包含匹配，理由是包含匹配会让 **`US:0700` 拦掉 `HK:00700`**。

用例按**判定类别**铺（命中怎么写 / 不命中怎么写），只加不删。
"""

from __future__ import annotations

import pytest

from app.strategy import memory_gate as mg


# ── ① 规范化 ─────────────────────────────────────────────────────────────

def test_normalize_symbol_is_market_colon_code_uppercased():
    assert mg.normalize_symbol("hk", " 00700 ") == "HK:00700"
    assert mg.normalize_symbol("CN_A", "601398") == "CN_A:601398"
    assert mg.normalize_symbol(None, "aapl") == ":AAPL"


def test_signature_splits_on_non_alnum():
    assert mg.symbol_signature("HK:00700") == ("HK", "00700")
    assert mg.symbol_signature("CN_A:601398") == ("CN", "A", "601398")
    assert mg.symbol_signature("") == ()


# ── ② 命中怎么写（每条都是一类写法）──────────────────────────────────────

@pytest.mark.parametrize("text", [
    "HK:00700 追高后回撤",             # 冒号
    "HK-00700 追高",                   # 连字符
    "HK 00700 追高",                   # 空格
    "HK/00700",                        # 斜杠
    "港股清单写的是 HK:00700 这一只",    # 夹在中文里
    "00700 与 HK:00700 都算",          # 同一串里出现别的裸代码也不影响
])
def test_hits_when_symbol_is_written_out(text):
    assert mg.applicability_hits(text, mg.normalize_symbol("HK", "00700")) is True


def test_hits_for_underscore_market_code():
    """`CN_A` 带下划线 —— 切词会把它拆成两段，连续子序列照样对得上。"""
    assert mg.applicability_hits("CN_A:601398 底部形态", mg.normalize_symbol("CN_A", "601398")) is True


# ── ③ 不命中怎么写（保守，宁漏拦不误拦）──────────────────────────────────

@pytest.mark.parametrize("text", [
    "US:0700 同类形态",                # ← 这一条就是「规范化」要解决的问题
    "HK:0700",                          # 代码位数不同
    "US:00700",                         # 市场不同
    "放量后 0700 回落",                  # **裸代码不是标的声明**
    "00700",                            # 同上
    "港股 00700 追高",                   # 市场写成中文 → 不猜（加别名表就是重新变模糊）
    "US:AAPL 与 00700",                 # 有别的市场
    "HK 00701",                         # 差一位
])
def test_misses_when_not_a_precise_symbol(text):
    assert mg.applicability_hits(text, mg.normalize_symbol("HK", "00700")) is False


@pytest.mark.parametrize("text", ["", None, "   ", "无标的"])
def test_misses_on_empty_or_targetless(text):
    assert mg.applicability_hits(text, mg.normalize_symbol("HK", "00700")) is False


def test_empty_target_never_hits():
    assert mg.applicability_hits("HK:00700", "") is False
    assert mg.applicability_hits("HK:00700", None) is False


def test_different_markets_never_collide():
    """`HK:00700` 与 `US:0700` 必须判为**不同标的**（任务书 §一.4 第 3 条）。"""
    assert mg.normalize_symbol("HK", "00700") != mg.normalize_symbol("US", "0700")
    assert mg.applicability_hits("US:0700", "HK:00700") is False
    assert mg.applicability_hits("HK:00700", "US:0700") is False


# ── ④ 三条同时成立才算拦（kind / status / 命中）─────────────────────────

def _item(**kw):
    base = {"experience_id": "exp_mem1", "kind": "verified", "status": "已确认",
            "statement": "追高后回撤概率显著上升", "applicability": "HK:00700 追高后"}
    base.update(kw)
    return base


def test_is_blocking_requires_all_three():
    assert mg.is_blocking(_item(), market="HK", code="00700") is True
    assert mg.is_blocking(_item(kind="fact"), market="HK", code="00700") is False
    assert mg.is_blocking(_item(kind="hypothesis"), market="HK", code="00700") is False
    assert mg.is_blocking(_item(status="待验证"), market="HK", code="00700") is False
    assert mg.is_blocking(_item(status="已推翻"), market="HK", code="00700") is False
    assert mg.is_blocking(_item(applicability="US:0700"), market="HK", code="00700") is False
    assert mg.is_blocking(_item(), market="US", code="0700") is False


def test_blocking_experience_returns_first_match_and_none_when_clean():
    items = [_item(experience_id="exp_a", applicability="US:0700"),
             _item(experience_id="exp_b"),
             _item(experience_id="exp_c")]
    got = mg.blocking_experience(items, market="HK", code="00700")
    assert got is not None and got["experience_id"] == "exp_b"
    assert got["statement"] == "追高后回撤概率显著上升"
    assert mg.blocking_experience([_item(applicability="US:0700")], market="HK", code="00700") is None


def test_blocking_experience_tolerates_garbage():
    assert mg.blocking_experience(None, market="HK", code="00700") is None
    assert mg.blocking_experience([None, "x", 5], market="HK", code="00700") is None
