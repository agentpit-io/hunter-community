# -*- coding: utf-8 -*-
"""R2 · 统一经验系统：**纯校验**（不连库、不连网）。

八条硬校验里能脱离数据库测的四条（规则 1 / 2 / 5 / 6）加上枚举与取值范围，
在 `memory.validate_append` / `memory.statement_has_numbers` 里被钉死。
「引用真实存在」「holdout 传染」「source 由通道决定」要在真库上测 ——
见 `tests/test_fin_memory_router.py`。

    cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_memory.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# sys.path 归一（同 test_fin_report.py 的说明）：`apps/api/__init__.py` 是空文件，
# 若 `/` 进了 sys.path，`import app` 会命中 `/app` 目录包而不是 `/app/app`。
_API_ROOT = Path(__file__).resolve().parents[1]
for _p in ("", "/", str(_API_ROOT)):
    while _p in sys.path:
        sys.path.remove(_p)
sys.path.insert(0, str(_API_ROOT))
_bad_app = sys.modules.get("app")
if _bad_app is not None and Path(getattr(_bad_app, "__file__", "") or "").parent == _API_ROOT:
    del sys.modules["app"]

from app.services.fin import memory as M  # noqa: E402

EV = [{"evidence_kind": "report", "ref_id": "rpt_x"}]


# ── 规则 6：statement 含阿拉伯数字 ────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "3 笔交易后追高胜率下降",   # 半角
    "跌 5% 之后反弹",           # 带百分号
    "15:30 之后追高胜率下降",   # 时刻（report.extract_numbers 会掩掉它，这里必须拦）
    "样本 30",
    "０５成",                   # 全角
    "T+1 后",
])
def test_statement_with_arabic_digits_is_flagged(text):
    assert M.statement_has_numbers(text) is True


@pytest.mark.parametrize("text", [
    "港股缩量后追高胜率显著下降",
    "大盘转弱时突破失败率上升",
    "涨停后整理不充分容易回落",
    "",                      # 空串不算含数字
])
def test_pure_chinese_statement_passes(text):
    assert M.statement_has_numbers(text) is False


def test_validate_rejects_statement_with_number():
    with pytest.raises(M.MemoryValidationError) as e:
        M.validate_append(kind="fact", statement="重复 3 次后胜率下降", evidence=EV)
    assert "阿拉伯数字" in str(e.value)


# ── 规则 1：证据至少一行 ─────────────────────────────────────────────────

@pytest.mark.parametrize("evidence", [[], None, ()])
def test_validate_rejects_empty_evidence(evidence):
    with pytest.raises(M.MemoryValidationError) as e:
        M.validate_append(kind="fact", statement="缩量后追高胜率下降", evidence=evidence)
    assert "至少一行" in str(e.value)


def test_validate_rejects_evidence_missing_ref():
    with pytest.raises(M.MemoryValidationError) as e:
        M.validate_append(kind="fact", statement="缩量后追高胜率下降",
                          evidence=[{"evidence_kind": "report", "ref_id": ""}])
    assert "ref_id" in str(e.value)


def test_validate_rejects_duplicate_evidence():
    dup = [{"evidence_kind": "report", "ref_id": "rpt_x"},
           {"evidence_kind": "report", "ref_id": "rpt_x"}]
    with pytest.raises(M.MemoryValidationError) as e:
        M.validate_append(kind="fact", statement="缩量后追高胜率下降", evidence=dup)
    assert "重复" in str(e.value)


# ── 规则 2：hypothesis 强制「待验证」 ────────────────────────────────────

def test_hypothesis_status_forced_pending_even_if_caller_insists():
    out = M.validate_append(kind="hypothesis", statement="缩量后追高胜率下降",
                            evidence=EV, status="已确认")
    assert out["status"] == "待验证"


# ── 规则 5：verified 必填 method / sample_size ───────────────────────────

def test_verified_without_method_rejected():
    with pytest.raises(M.MemoryValidationError) as e:
        M.validate_append(kind="verified", statement="缩量突破后追高胜率下降",
                          evidence=EV, sample_size=10)
    assert "method" in str(e.value)


def test_verified_without_sample_size_rejected():
    with pytest.raises(M.MemoryValidationError) as e:
        M.validate_append(kind="verified", statement="缩量突破后追高胜率下降",
                          evidence=EV, method="全样本回测")
    assert "sample_size" in str(e.value)


def test_verified_sample_size_below_one_rejected():
    with pytest.raises(M.MemoryValidationError) as e:
        M.validate_append(kind="verified", statement="缩量突破后追高胜率下降",
                          evidence=EV, method="全样本回测", sample_size=0)
    assert "≥ 1" in str(e.value)


def test_verified_ok_defaults_confirmed():
    out = M.validate_append(kind="verified", statement="缩量突破后追高胜率下降",
                            evidence=EV, method="全样本回测", sample_size=42)
    assert out["status"] == "已确认" and out["sample_size"] == 42


# ── 枚举 / 取值范围 / external ───────────────────────────────────────────

def test_unknown_kind_rejected():
    with pytest.raises(M.MemoryValidationError):
        M.validate_append(kind="guess", statement="缩量后追高胜率下降", evidence=EV)


def test_external_evidence_rejected():
    with pytest.raises(M.MemoryValidationError) as e:
        M.validate_append(kind="fact", statement="缩量后追高胜率下降",
                          evidence=[{"evidence_kind": "external", "ref_id": "arxiv:123"}])
    assert "external" in str(e.value)


def test_invalid_market_rejected():
    with pytest.raises(M.MemoryValidationError):
        M.validate_append(kind="fact", statement="缩量后追高胜率下降",
                          evidence=EV, market="JP")


def test_confidence_out_of_range_rejected():
    for bad in (-0.1, 1.5):
        with pytest.raises(M.MemoryValidationError):
            M.validate_append(kind="fact", statement="缩量后追高胜率下降",
                              evidence=EV, confidence=bad)


def test_supersedes_forces_overturned_status():
    out = M.validate_append(kind="fact", statement="缩量后追高胜率下降",
                            evidence=EV, supersedes="exp_old")
    assert out["status"] == "已推翻"


def test_holdout_tainted_normalized_to_bool():
    out = M.validate_append(kind="fact", statement="缩量后追高胜率下降",
                            evidence=[{"evidence_kind": "report", "ref_id": "rpt_x",
                                       "holdout_tainted": 1}])
    assert out["evidence"][0]["holdout_tainted"] is True
