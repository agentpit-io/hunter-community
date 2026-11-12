# -*- coding: utf-8 -*-
"""R3 · 复核回路与内容哈希冻结 —— **纯函数用例**（不连库、不连网）。

    cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_review.py -q

三块：

1. **内容指纹**（`memory.content_hash` / `aggregate_hash`）：口径稳定、对「可见性会变的
   那六个字段」敏感、对顺序不敏感 —— 这是 `plan/R3.md` §一.3b 的可执行定义；
2. **`source` 的服务端判定**（`memory.resolve_source`）：内网 → `ai`；证据全是人工成交
   → `human_mixed`；JWT → `human_mixed`；混合证据 → 退回 `ai`（保守方向）；
3. **复核候选**（`review.*`）：只允许 `fact`/`hypothesis`、`statement` 不许有阿拉伯数字、
   引用必须真实存在、模型不可用时降级为规则文案。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

import pytest

from app.services.fin import memory as memory_svc
from app.services.fin import review as review_svc

SH = timezone(timedelta(hours=8))


# ════════════════════════════════════════════════════════════════════════
# 一 · 内容指纹（R3 §3b）
# ════════════════════════════════════════════════════════════════════════

def _row(**kw):
    base = {"experience_id": "exp_b", "kind": "verified", "status": "已确认",
            "statement": "缩量整理后突破的持续性更强", "applicability": "HK · 日线",
            "valid_until": None, "superseded_by": None}
    base.update(kw)
    return base


def test_content_hash_is_stable_and_hex():
    h = memory_svc.content_hash(_row())
    assert h == memory_svc.content_hash(_row())
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


@pytest.mark.parametrize("field,value", [
    ("kind", "fact"),
    ("status", "待验证"),
    ("statement", "改了正文"),
    ("applicability", "US · 日线"),
    ("valid_until", datetime(2026, 12, 31, tzinfo=SH)),
    ("superseded_by", "exp_other"),
])
def test_content_hash_changes_when_visibility_fields_change(field, value):
    """这六个字段就是「后来改变可见性」的全部入口 —— 每一个都必须让指纹变。"""
    assert memory_svc.content_hash(_row()) != memory_svc.content_hash(_row(**{field: value}))


def test_content_hash_ignores_irrelevant_fields():
    """与可见性无关的列（id / 时间戳 / 证据计数）不参与指纹 —— 换了也不该变。"""
    base = _row()
    other = _row()
    other["created_at"] = "2027-01-01T00:00:00+08:00"
    other["confidence"] = 0.99
    assert memory_svc.content_hash(base) == memory_svc.content_hash(other)


def test_content_hashes_is_sorted_by_id():
    rows = [_row(experience_id="exp_z"), _row(experience_id="exp_a")]
    hashes = memory_svc.content_hashes(rows)
    assert list(hashes) == ["exp_a", "exp_z"]


def test_aggregate_hash_is_order_independent_and_sensitive():
    a = memory_svc.content_hashes([_row(experience_id="exp_a")])
    b = memory_svc.content_hashes([_row(experience_id="exp_b")])
    assert memory_svc.aggregate_hash({**b, **a}) == memory_svc.aggregate_hash({**a, **b})
    changed = {"exp_a": "0" * 64, "exp_b": b["exp_b"]}
    assert memory_svc.aggregate_hash(changed) != memory_svc.aggregate_hash({**a, **b})
    assert memory_svc.aggregate_hash({}) == memory_svc.aggregate_hash({})


def test_content_hash_separator_prevents_field_bleed():
    """分隔符是 `\\x1f` 而不是 `|` —— 不然「a|b」与「a」+「b」两行会撞成同一个值。"""
    x = _row(statement="AB", applicability="C")
    y = _row(statement="A", applicability="BC")
    assert memory_svc.content_hash(x) != memory_svc.content_hash(y)


# ════════════════════════════════════════════════════════════════════════
# 二 · source 的服务端判定（R3 · 规则 7 的扩展）
# ════════════════════════════════════════════════════════════════════════

def _ev(kind, ref):
    return {"evidence_kind": kind, "ref_id": ref}


def test_source_internal_plain_is_ai():
    assert memory_svc.resolve_source("internal", [_ev("report", "rpt_1")], {}) == ("ai", "ai")


def test_source_internal_all_human_trades_is_human_mixed():
    src = memory_svc.resolve_source(
        "internal", [_ev("trade", "trd_1"), _ev("trade", "trd_2")],
        {"trd_1": "human", "trd_2": "human_confirmed"})
    assert src == ("human_mixed", "human:trade")


def test_source_internal_ai_trade_stays_ai():
    assert memory_svc.resolve_source(
        "internal", [_ev("trade", "trd_1")], {"trd_1": "ai"}) == ("ai", "ai")


def test_source_internal_mixed_evidence_falls_back_to_ai():
    """掺了一条 AI 成交或一条事实行 → 退回 `ai`（宁可把人工记成 AI，不反过来）。"""
    ev = [_ev("trade", "trd_1"), _ev("report", "rpt_1")]
    assert memory_svc.resolve_source("internal", ev, {"trd_1": "human"}) == ("ai", "ai")
    assert memory_svc.resolve_source(
        "internal", [_ev("trade", "trd_1"), _ev("trade", "trd_2")],
        {"trd_1": "human", "trd_2": "ai"}) == ("ai", "ai")


def test_source_jwt_is_human_mixed():
    assert memory_svc.resolve_source("jwt", [_ev("report", "rpt_1")], {}) == ("human_mixed", "user")


# ════════════════════════════════════════════════════════════════════════
# 三 · 复核候选（review）
# ════════════════════════════════════════════════════════════════════════

def _collected(*, facts=True, trades=True, report=True):
    return {
        "project_id": "prj_1", "trade_date": "2026-10-09", "market": "CN_A",
        "report": {"report_id": "rpt_1", "self_review": {}} if report else None,
        "facts": ([{"metric_key": "nav", "label_cn": "净值", "value": "1.0",
                    "unit": "倍", "currency": None, "ref_id": "rpt_1:nav",
                    "source_ref": "fin_valuation:2026-10-09"}] if facts else []),
        "trades": ([{"trade_id": "trd_1", "code": "601398", "side": "buy", "qty": 100,
                     "price": "10", "amount": "1000", "total_fee": "5", "source": "ai",
                     "actor": "fin-worker", "market": "CN_A",
                     "traded_at": "2026-10-09T01:30:00+00:00"}] if trades else []),
        "codes": ["601398"],
    }


def test_deterministic_candidates_use_real_refs():
    out = review_svc.deterministic_candidates(_collected())
    assert len(out) == 1                       # 只有 AI 成交
    assert out[0]["kind"] == "fact"
    assert out[0]["evidence"] == [{"evidence_kind": "trade", "ref_id": "trd_1"}]
    assert not review_svc._has_numbers(out[0]["statement"])
    ok, why = review_svc.validate_candidate(out[0], _collected())
    assert ok, why


def test_deterministic_candidates_split_human_trades():
    c = _collected()
    c["trades"].append({"trade_id": "trd_2", "code": "601398", "side": "buy", "qty": 100,
                        "price": "10", "amount": "1000", "total_fee": "5",
                        "source": "human", "actor": "user", "market": "CN_A",
                        "traded_at": "2026-10-09T02:00:00+00:00"})
    out = review_svc.deterministic_candidates(c)
    assert [x["basis"] for x in out] == ["rule:ai_trades", "rule:human_trades"]
    assert out[1]["evidence"] == [{"evidence_kind": "trade", "ref_id": "trd_2"}]


def test_deterministic_candidates_empty_when_nothing_to_review():
    assert review_svc.deterministic_candidates(_collected(facts=False, trades=False,
                                                          report=False)) == []


def test_deterministic_candidates_report_without_trades():
    out = review_svc.deterministic_candidates(_collected(trades=False))
    assert len(out) == 1 and out[0]["evidence"][0]["evidence_kind"] == "fact"


@pytest.mark.parametrize("cand,fragment", [
    ({"kind": "verified", "statement": "x", "evidence": [_ev("trade", "trd_1")]}, "fact"),
    ({"kind": "fact", "statement": "", "evidence": [_ev("trade", "trd_1")]}, "空"),
    ({"kind": "fact", "statement": "胜率 62%", "evidence": [_ev("trade", "trd_1")]}, "阿拉伯数字"),
    ({"kind": "fact", "statement": "全中文", "evidence": []}, "至少一行"),
    ({"kind": "fact", "statement": "全中文", "evidence": [_ev("trade", "trd_不存在")]}, "不在当日"),
    ({"kind": "fact", "statement": "全中文", "evidence": [_ev("fact", "rpt_1:不存在")]}, "不在当日"),
    ({"kind": "fact", "statement": "全中文", "evidence": [_ev("snapshot", "SNAP-1")]}, "只引用"),
    ({"kind": "fact", "statement": "全中文",
      "evidence": [_ev("trade", "trd_1"), _ev("trade", "trd_1")]}, "重复"),
])
def test_validate_candidate_rejects(cand, fragment):
    ok, why = review_svc.validate_candidate(cand, _collected())
    assert not ok and fragment in why


def test_parse_experiences_strips_fences_and_finds_object():
    items = review_svc._parse_experiences('```json\n{"experiences":[{"kind":"fact"}]}\n```')
    assert items == [{"kind": "fact"}]
    with pytest.raises(ValueError):
        review_svc._parse_experiences("no json here")
    with pytest.raises(ValueError):
        review_svc._parse_experiences('{"experiences":[]}')


def test_to_candidate_maps_keys_to_real_refs():
    cand = review_svc._to_candidate(
        {"kind": "fact", "statement": "无数字结论", "evidence_fact_keys": ["nav"],
         "evidence_trade_ids": ["trd_1"]}, _collected())
    assert cand["evidence"] == [{"evidence_kind": "fact", "ref_id": "rpt_1:nav"},
                               {"evidence_kind": "trade", "ref_id": "trd_1"}]
    assert review_svc.validate_candidate(cand, _collected())[0] is True
    # 没有报告 → 建不出 fact 引用 → 该候选作废（返回 None）
    assert review_svc._to_candidate({"kind": "fact", "evidence_fact_keys": ["nav"]},
                                    _collected(report=False)) is None


# ── propose：模型答了但全废 → 降级；模型挂了 → 降级 ───────────────────────

def test_propose_uses_llm_candidates_when_valid():
    async def fake_analyzer(collected):
        return [{"kind": "fact", "statement": "今天只做了一件事",
                 "evidence_trade_ids": ["trd_1"]}]
    out = asyncio.run(review_svc.propose(_collected(), analyzer=fake_analyzer))
    assert out["used_fallback"] is False and out["reason"] is None
    assert len(out["candidates"]) == 1 and out["candidates"][0]["basis"] == "llm"


def test_propose_degrades_when_model_returns_junk():
    async def fake_analyzer(collected):
        return [{"kind": "fact", "statement": "胜率 62%", "evidence_trade_ids": ["trd_1"]}]
    out = asyncio.run(review_svc.propose(_collected(), analyzer=fake_analyzer))
    assert out["used_fallback"] is True and "未通过校验" in out["reason"]
    assert out["rejected"] and "阿拉伯数字" in out["rejected"][0]["reason"]
    assert out["candidates"][0]["basis"].startswith("rule:")   # 降级为规则文案


def test_propose_degrades_when_model_raises():
    async def boom(collected):
        raise RuntimeError("模型连不上")
    out = asyncio.run(review_svc.propose(_collected(), analyzer=boom))
    assert out["used_fallback"] is True and "模型连不上" in out["reason"]
    assert out["rejected"] == []


def test_propose_no_data_no_candidates():
    async def boom(collected):
        raise RuntimeError("down")
    out = asyncio.run(review_svc.propose(_collected(facts=False, trades=False,
                                                    report=False), analyzer=boom))
    assert out["used_fallback"] is True and out["candidates"] == []


# ── R5 · 候选经验的结构化标签（确定性，不打给模型）───────────────────────

def _regime(label="unknown", version="regime-v1"):
    return {"label": label, "rule_version": version,
            "source_snapshot_id": None, "as_of": "2026-10-03T16:30:00+08:00"}


def test_tag_candidates_attaches_symbols_and_regime():
    cands = [{"statement": "a", "kind": "fact"}, {"statement": "b", "kind": "fact"}]
    out = review_svc.tag_candidates(cands, symbols=["HK:00700"], regime_out=_regime())
    assert out[0]["symbols"] == ["HK:00700"]
    assert out[0]["regime_tags"] == ["unknown"]
    assert out[0]["regime_source"] == "regime-v1"
    # 不改原对象（返回的是副本）
    assert "symbols" not in cands[0]


def test_tag_candidates_writes_unknown_verbatim():
    """取不到行情时**照写** `["unknown"]`（不是跳过）—— 聚合侧据此分开。"""
    out = review_svc.tag_candidates([{"statement": "a"}], symbols=None, regime_out=_regime())
    assert out[0]["regime_tags"] == ["unknown"]
    assert out[0]["symbols"] is None


def test_tag_candidates_carries_real_regime():
    out = review_svc.tag_candidates([{"statement": "a"}], symbols=[],
                                    regime_out=_regime(label="bull"))
    assert out[0]["regime_tags"] == ["bull"]


def test_tag_candidates_skips_garbage_entries():
    out = review_svc.tag_candidates([None, "x", {"statement": "ok"}], symbols=[],
                                    regime_out=_regime())
    assert len(out) == 1 and out[0]["statement"] == "ok"
