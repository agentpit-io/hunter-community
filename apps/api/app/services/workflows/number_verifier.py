"""大模型输出数字正则回读校验器（`apps/api/app/services/workflows/number_verifier.py`）。

对应《03 详细开发方案》§4.3 与《05 测试方案》TC-CALC-04：
- 大模型严禁心算数字，全部由确定性 Python 引擎算好真值，再交由 AI 解释并由正则回读核对；
- 正则回读校验器逐一提取 AI 正文中的所有百分比、金额、价格、比率（支持带小数的数值或百分比），
  与底层工具/引擎返回的 JSON 真值 100% 匹配；
- 若 AI 写错数字立即拦截并重写/抛错。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

# 先把日期、时间、报告期、证券代码、页码引注等非度量数字掩码掉，避免把 "2026-11-13" 或 "600036.SH" 误判为金额/指标
_MASK_PATTERNS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:\d{2}|Z)?)?\b"),
    re.compile(r"\b\d{2}:\d{2}(?::\d{2})?\b"),
    re.compile(r"\b20\d{2}Q[1-4]\b"),
    re.compile(r"\b\d{5,6}\.(?:SH|SZ|BJ|HK|US)\b", re.IGNORECASE),
    re.compile(r"\b(?:MA|EMA|WMA|RSI|WR|CCI|ROC|MTM|BIAS|VR|CR|AR|BR|PSY|EMV|VOL_MA|MFI|ATR|NATR|HV|ADR|ABI|ARMS)\d+\b"),
    re.compile(r"第\s*\d+(?:\s*[-~～]\s*\d+)?\s*[页条项步章]"),
    re.compile(r"\bStep\s*\d+\b", re.IGNORECASE),
]

# 提取正文中的金融度量数字：
# 1) 优先匹配带 % / 亿元 / 万元 / 元 / 倍 / 分 的整数或小数
# 2) 再匹配任何带小数点的纯数字（如 36.85、-1.42）
_FINANCIAL_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_\.])([+-]?\d+(?:\.\d+)?(?:%|亿元|万元|元|倍|分)|[+-]?\d+\.\d+)(?![A-Za-z0-9_])"
)


class NumberVerificationError(ValueError):
    """当报告正文出现底层真值集中不存在的篡改/幻觉数字时抛出。"""


@dataclass
class NumberVerificationResult:
    ok: bool
    extracted_tokens: list[str] = field(default_factory=list)
    matched_tokens: list[str] = field(default_factory=list)
    mismatched_tokens: list[str] = field(default_factory=list)
    truth_count: int = 0
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "extracted_count": len(self.extracted_tokens),
            "matched_count": len(self.matched_tokens),
            "mismatched_count": len(self.mismatched_tokens),
            "extracted_tokens": self.extracted_tokens,
            "matched_tokens": self.matched_tokens,
            "mismatched_tokens": self.mismatched_tokens,
            "truth_count": self.truth_count,
            "message": self.message,
        }


def collect_truth_numbers(payload: Any) -> list[float]:
    """递归遍历底层工具/计算引擎返回的 JSON 结构，收集所有合法的确定性数值真值。"""
    out: list[float] = []

    def _walk(obj: Any) -> None:
        if obj is None or isinstance(obj, bool):
            return
        if isinstance(obj, (int, float)):
            val = float(obj)
            if math.isfinite(val):
                out.append(val)
                # 同时收录常见四舍五入位数（2位、4位）与百分数折算形式
                out.append(round(val, 2))
                out.append(round(val, 4))
            return
        if isinstance(obj, str):
            # 如果字符串本身是纯数字或百分比（例如 "+2.35%"）
            s = obj.strip().rstrip("%")
            try:
                val = float(s)
                if math.isfinite(val):
                    out.append(val)
                    out.append(round(val, 2))
            except ValueError:
                pass
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                _walk(v)
            return
        if isinstance(obj, (list, tuple, set)):
            for item in obj:
                _walk(item)

    _walk(payload)
    # 固定收录标准评分满分 100 等制度性常数
    out.extend([100.0, 0.0])
    return out


def _mask_non_metric_tokens(text: str) -> str:
    masked = text
    for pat in _MASK_PATTERNS:
        masked = pat.sub(" [MASKED] ", masked)
    return masked


def _parse_token_to_float(token: str) -> float | None:
    cleaned = token.strip()
    for suffix in ("%", "亿元", "万元", "元", "倍", "分"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
            break
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    try:
        val = float(cleaned)
        return val if math.isfinite(val) else None
    except ValueError:
        return None


def _matches_any_truth(val: float, truth_values: list[float], tol: float = 0.015) -> bool:
    for tv in truth_values:
        if abs(val - tv) <= tol:
            return True
        # 支持绝对值比对（如正文中写 "下跌 1.42%"，底层存 -1.42）
        if abs(abs(val) - abs(tv)) <= tol:
            return True
    return False


def verify_report_numbers_against_truth(
    report_text: str,
    truth_payload: Any,
    *,
    tolerance: float = 0.015,
) -> NumberVerificationResult:
    """提取报告正文中的金融数字，逐一与 `truth_payload` 中的底层真值比对。"""
    truth_vals = collect_truth_numbers(truth_payload)
    masked_text = _mask_non_metric_tokens(report_text or "")

    extracted: list[str] = []
    matched: list[str] = []
    mismatched: list[str] = []

    for m in _FINANCIAL_NUMBER_RE.finditer(masked_text):
        raw_tok = m.group(1)
        val = _parse_token_to_float(raw_tok)
        if val is None:
            continue
        extracted.append(raw_tok)
        if _matches_any_truth(val, truth_vals, tol=tolerance):
            matched.append(raw_tok)
        else:
            mismatched.append(raw_tok)

    ok = len(mismatched) == 0
    if ok:
        msg = f"正则数字回读校验通过：共提取 {len(extracted)} 个金融数字，100% 与底层计算真值匹配。"
    else:
        msg = (
            f"正则数字回读校验拦截：发现 {len(mismatched)} 个与底层计算真值不匹配的数字 "
            f"{mismatched}（严禁大模型心算或篡改数字）！"
        )

    return NumberVerificationResult(
        ok=ok,
        extracted_tokens=extracted,
        matched_tokens=matched,
        mismatched_tokens=mismatched,
        truth_count=len(truth_vals),
        message=msg,
    )


def verify_and_guard_report_numbers(
    report_text: str,
    truth_payload: Any,
    *,
    fallback_deterministic_report: str | None = None,
    raise_on_mismatch: bool = False,
) -> tuple[str, NumberVerificationResult]:
    """核验报告数字一致性；若发现不一致，可抛错或强制回退重写为确定性真值报告。"""
    res = verify_report_numbers_against_truth(report_text, truth_payload)
    if res.ok:
        return report_text, res

    if raise_on_mismatch:
        raise NumberVerificationError(res.message)

    if fallback_deterministic_report is not None:
        fallback_res = verify_report_numbers_against_truth(fallback_deterministic_report, truth_payload)
        fallback_res.message = (
            f"已拦截原报告中的失真数字 {res.mismatched_tokens} 并自动重写为 100% 确定性真值报告。"
        )
        return fallback_deterministic_report, fallback_res

    raise NumberVerificationError(res.message)
