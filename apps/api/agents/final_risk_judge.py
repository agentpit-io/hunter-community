"""风控最终裁决 · 综合 3 方风控辩论 → 修正决策/置信度/止损 · Deep Think 版

替代原 risk_judge.py(纯规则)· 让 Deep Think 模型基于 3 方争论做最终裁决

Returns FinalDecisionResult dict (与 risk_judge.py 同 schema · 便替换)
"""
from loguru import logger

from agents.state import EnhancedAgentState
from agents.sentinel.llm_client import llm_json_call


def run_final_risk_judge(
    state: EnhancedAgentState,
    judgment: dict,
    risk_debate: dict,
) -> dict:
    """
    Args:
        state:        完整 EnhancedAgentState
        judgment:     ComprehensiveJudge 的输出 dict
        risk_debate:  {"aggressive": str, "neutral": str, "conservative": str}
                      3 方风控发言 (Sprint B 新增)

    Returns FinalDecisionResult dict:
        · 与原 risk_judge.py 保持字段兼容
        · 新增 risk_debate 字段 · 用于 markdown 报告展示
    """
    decision_in = judgment.get("decision", "HOLD")
    confidence_in = float(judgment.get("confidence", 0.5))

    system = (
        "你是【风控委员会最终裁判】· 使用简体中文 · 输出 JSON。\n\n"
        "3 位风控分析师(激进/中性/保守)已围绕综合判官的决策展开辩论。\n"
        "你的任务:综合 3 方意见 · 修正决策/置信度/止损/仓位 · 给出最终执行方案。\n\n"
        "【裁决规则】\n"
        "  1. 3 方一致同意 → 提高置信度 5-10%\n"
        "  2. 中性派 + 保守派对齐 → 采纳偏保守方案 · 置信度可能降低\n"
        "  3. 中性派 + 激进派对齐 → 采纳偏激进方案 · 但止损必须严格\n"
        "  4. 3 方分歧严重 → 保留原判官决策 · 但下调置信度 10-15%\n"
        "  5. 若 Sentinel 新闻面与技术面矛盾(state.sentinel_confidence >= 0.7 且与决策相反) → "
        "     必须显式警告 · 置信度封顶 60%\n\n"
        "【输出 JSON schema】\n"
        '{"decision":"BUY|HOLD|SELL","confidence":0.0-1.0,'
        '"position_pct":<0-100>,"stop_loss_pct":<正数·止损百分比·如 5 表示 -5%>,'
        '"take_profit_pct":<正数·目标涨幅·如 15 表示 +15%>,'
        '"time_horizon":"<持有周期·如"1-3个月">",'
        '"key_reason":"一句话最终理由(含 3 方观点权衡)",'
        '"execution_plan":"具体执行细节·200 字以内",'
        '"consensus_level":"unanimous|majority|split"}'
    )

    user = (
        f"股票:{state.stock_name}({state.ticker})\n"
        f"综合判官原决策:{decision_in} · 置信度 {int(confidence_in * 100)}%\n"
        f"判官核心理由:{judgment.get('key_reason', '')}\n"
        f"判官投资计划:{judgment.get('investment_plan', '')[:300]}\n\n"
        f"【Sentinel】{state.sentinel_opinion} · 置信度 {int(state.sentinel_confidence * 100)}%\n\n"
        f"═══════════════════════════════════════\n"
        f"【激进派风控意见】\n{risk_debate.get('aggressive', '(未发言)')}\n\n"
        f"═══════════════════════════════════════\n"
        f"【中性派风控意见】\n{risk_debate.get('neutral', '(未发言)')}\n\n"
        f"═══════════════════════════════════════\n"
        f"【保守派风控意见】\n{risk_debate.get('conservative', '(未发言)')}\n"
        f"═══════════════════════════════════════\n\n"
        f"请以风控委员会主席身份 · 综合三方意见 · 输出最终裁决 JSON。"
    )

    # Deep Think · 最终裁决走 Pro 模型 · 决策质量优先
    # max_tokens 1500 → 8192 · reasoning 型模型下 1500 不够(见 llm_client 修复)
    parsed, meta = llm_json_call(system, user, deep=True, max_tokens=8192, temperature=0.3)

    # ── fallback · LLM 失败时回退到 rule-based ──
    if not parsed:
        logger.warning("[final_risk_judge] Deep Think 失败 · 回退到规则版")
        return _rule_based_fallback(state, judgment, risk_debate)

    decision_out = parsed.get("decision", decision_in)
    confidence_out = float(parsed.get("confidence", confidence_in))

    # Sentinel 冲突强制封顶
    sentinel_conflicts = False
    if state.sentinel_confidence >= 0.7:
        if state.sentinel_opinion == "看空" and decision_out == "BUY":
            confidence_out = min(confidence_out, 0.6)
            sentinel_conflicts = True
        elif state.sentinel_opinion == "看多" and decision_out == "SELL":
            confidence_out = min(confidence_out, 0.6)
            sentinel_conflicts = True

    # 组织止损具体数值 · 若给了 pct 则以当前价推算; 未给则保留判官原 hint
    stop_loss_pct = parsed.get("stop_loss_pct")
    stop_loss_hint = judgment.get("stop_loss_hint", "")
    stop_loss_num = _parse_stop_loss(stop_loss_hint)

    return {
        "decision": decision_out,
        "confidence": round(confidence_out, 2),
        "key_reason": parsed.get("key_reason", judgment.get("key_reason", "")),
        "bull_summary": judgment.get("bull_summary", ""),
        "bear_summary": judgment.get("bear_summary", ""),
        "sentinel_summary": state.sentinel_report,
        "market_report": state.market_report,
        "stop_loss": stop_loss_num,
        "investment_plan": parsed.get("execution_plan", judgment.get("investment_plan", "")),
        "exit_plan": "",
        "debate_mode": "normal",
        "sentinel_conflicts": sentinel_conflicts,
        # ── Sprint B 新增字段 ──
        "position_pct": parsed.get("position_pct"),
        "stop_loss_pct": stop_loss_pct,
        "take_profit_pct": parsed.get("take_profit_pct"),
        "time_horizon": parsed.get("time_horizon", ""),
        "consensus_level": parsed.get("consensus_level", "majority"),
        "risk_debate": risk_debate,   # 3 方发言 · 供 markdown 报告展示
    }


def _rule_based_fallback(
    state: EnhancedAgentState,
    judgment: dict,
    risk_debate: dict,
) -> dict:
    """LLM 失败时的规则版兜底 · 沿用原 risk_judge.py 逻辑"""
    from agents.risk_judge import run_risk_judge
    result = run_risk_judge(state, judgment)
    result["risk_debate"] = risk_debate
    result["consensus_level"] = "unavailable"
    return result


def _parse_stop_loss(hint: str) -> float | None:
    if not hint:
        return None
    import re
    nums = re.findall(r"\d+(?:\.\d+)?", hint)
    if nums:
        try:
            return float(nums[0])
        except Exception:
            pass
    return None
