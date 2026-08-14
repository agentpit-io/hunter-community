"""F7 Stage B · 基于纯事实归因 + 引用校验

输入: Stage A 输出的事实清单 + 用户 thesis + kill conditions
输出: 完整推送内容 + thesis_status + 引用校验报告
"""
import re

from loguru import logger

from .llm_client import llm_json_call, estimate_cost_cny
from .prompts import (
    ATTRIBUTION_REASONING_SYSTEM,
    build_attribution_user_prompt,
)


def _extract_numbers(text: str) -> set[str]:
    """从文本里抽出所有数字（含百分比 / 金额）"""
    if not text:
        return set()
    # 匹配：百分比、亿、万、整数小数
    pattern = r"-?\d+(?:\.\d+)?[%亿万千]?"
    return set(re.findall(pattern, text))


def validate_facts_only(generated_text: str, facts: list[dict]) -> dict:
    """校验生成的文本只引用了事实清单里的数字（防 LLM 越界）

    返回: {
        "passed": bool,
        "unauthorized_numbers": [...],
        "violation_rate": 0.0-1.0,
    }
    """
    if not generated_text:
        return {"passed": True, "unauthorized_numbers": [], "violation_rate": 0.0}

    # 事实清单里的所有数字
    facts_text = " ".join(f.get("fact", "") for f in facts)
    facts_numbers = _extract_numbers(facts_text)

    # 生成文本里的数字
    gen_numbers = _extract_numbers(generated_text)

    # 越界数字 = 生成文本里有 但事实清单没有的
    unauthorized = []
    for num in gen_numbers:
        if num in facts_numbers:
            continue
        # 容忍 "0" "1" "2" 等小数字 + 单字符 + 百分比 < 100 等常识数字
        if len(num.replace(".", "").replace("%", "")) <= 2:
            continue
        unauthorized.append(num)

    rate = len(unauthorized) / max(1, len(gen_numbers))
    return {
        "passed":               len(unauthorized) == 0,
        "unauthorized_numbers": unauthorized,
        "violation_rate":       rate,
    }


async def reason_attribution(stock_name: str, change_pct: float,
                              thesis_text: str, kill_conditions: list[dict],
                              fact_extraction_result: dict) -> dict:
    """F7 Stage B 归因推理

    Args:
        stock_name:              股票名
        change_pct:              当日波动 %
        thesis_text:             用户 thesis 原文
        kill_conditions:         [{text, type, ...}, ...]
        fact_extraction_result:  Stage A 输出

    Returns:
        归因结果 dict（含 thesis_status / drivers_check / user_message 等）
    """
    facts    = fact_extraction_result.get("verifiable_facts", [])
    rejected = fact_extraction_result.get("rejected_as_opinion", [])

    if not facts:
        return {
            "thesis_status":           "INSUFFICIENT",
            "confidence":              0.0,
            "summary":                 "事实提取阶段未保留任何可验证事实，无法归因",
            "user_message":            f"{stock_name} 今日 {change_pct:+.2f}% — 信号不足，建议等待更多权威源",
            "drivers_check":           [],
            "kill_conditions_check":   [],
            "positive_evidence":       [],
            "risk_evidence":           [],
            "action_recommendation":   "等待更多信号",
            "validation":              {"passed": True, "unauthorized_numbers": [], "violation_rate": 0.0},
            "llm_meta":                {},
        }

    user_prompt = build_attribution_user_prompt(
        stock_name      = stock_name,
        change_pct      = change_pct,
        thesis_text     = thesis_text,
        kill_conditions = kill_conditions,
        verifiable_facts= facts,
        rejected_count  = len(rejected),
    )

    import asyncio
    # max_tokens 4000 → 8192 · reasoning 型模型下 4000 仍有可能被推理吃完(见 llm_client 修复)
    parsed, meta = await asyncio.to_thread(
        llm_json_call,
        ATTRIBUTION_REASONING_SYSTEM, user_prompt,
        max_tokens=8192, temperature=0.3,
    )

    if parsed is None:
        logger.warning("F7 Stage B: LLM 解析失败，降级")
        return {
            "thesis_status":           "INSUFFICIENT",
            "confidence":              0.0,
            "summary":                 "LLM 归因失败：" + str(meta.get("error", "unknown")),
            "user_message":            f"{stock_name} {change_pct:+.2f}% — 归因服务暂时不可用",
            "drivers_check":           [],
            "kill_conditions_check":   [],
            "positive_evidence":       [],
            "risk_evidence":           [],
            "action_recommendation":   "稍后重试",
            "validation":              {"passed": False, "unauthorized_numbers": [], "violation_rate": 0.0},
            "llm_meta":                meta,
        }

    # 引用校验：用 user_message 做主要校验对象
    user_msg = parsed.get("user_message", "") or ""
    validation = validate_facts_only(user_msg, facts)

    # 标准化输出（防止 LLM 字段缺失）
    result = {
        "thesis_status":         parsed.get("thesis_status", "INSUFFICIENT"),
        "confidence":            float(parsed.get("confidence", 0.5)),
        "summary":               parsed.get("summary", ""),
        "drivers_check":         parsed.get("drivers_check", []) or [],
        "kill_conditions_check": parsed.get("kill_conditions_check", []) or [],
        "positive_evidence":     parsed.get("positive_evidence", []) or [],
        "risk_evidence":         parsed.get("risk_evidence", []) or [],
        "user_message":          user_msg,
        "action_recommendation": parsed.get("action_recommendation", ""),
        "validation":            validation,
        "llm_meta":              meta,
    }

    cost = estimate_cost_cny(meta.get("tokens_in", 0), meta.get("tokens_out", 0))
    logger.info(
        "F7 Stage B {}: status={} conf={:.2f} validation={} cost=¥{}",
        stock_name, result["thesis_status"], result["confidence"],
        "passed" if validation["passed"] else f"FAIL({len(validation['unauthorized_numbers'])})",
        cost,
    )

    return result
