"""RiskJudge — 风险评估节点（最终判断）

整合前序所有分析，输出最终结构化决策，并在 Sentinel 与技术面矛盾时修正置信度。
"""
from agents.state import EnhancedAgentState


def run_risk_judge(state: EnhancedAgentState, judgment: dict) -> dict:
    """
    Args:
        state:    完整 EnhancedAgentState
        judgment: ComprehensiveJudge 或 ExitStrategyAdvisor 的输出 dict

    Returns FinalDecisionResult dict
    """
    if state.debate_mode == "exit_strategy":
        return {
            "decision":           "SELL",
            "confidence":         max(state.sentinel_confidence, 0.7),
            "key_reason":         f"止损条件触发：{state.kill_condition_desc}",
            "bull_summary":       "",
            "bear_summary":       "",
            "sentinel_summary":   state.sentinel_report,
            "market_report":      state.market_report,
            "stop_loss":          None,
            "investment_plan":    "",
            "exit_plan":          judgment.get("exit_plan", ""),
            "recommended_exit":   judgment.get("recommended_option", "B"),
            "exit_price_targets": judgment.get("price_targets", ""),
            "debate_mode":        "exit_strategy",
            "sentinel_conflicts": False,
        }

    decision   = judgment.get("decision", "HOLD")
    confidence = float(judgment.get("confidence", 0.5))

    # Sentinel 高置信度与裁判结论矛盾时降低置信度
    sentinel_conflicts = False
    note = ""
    if state.sentinel_confidence >= 0.7:
        if state.sentinel_opinion == "看空" and decision == "BUY":
            confidence = max(0.3, confidence - 0.2)
            note = "⚠️ Sentinel 新闻面看空（高置信度），与多头建议存在矛盾，请谨慎。"
            sentinel_conflicts = True
        elif state.sentinel_opinion == "看多" and decision == "SELL":
            confidence = max(0.3, confidence - 0.15)
            note = "⚠️ Sentinel 新闻面看多（高置信度），与卖出建议存在矛盾，请复核。"
            sentinel_conflicts = True

    key_reason = judgment.get("key_reason", "")
    if note:
        key_reason = f"{key_reason}  {note}"

    return {
        "decision":          decision,
        "confidence":        round(confidence, 2),
        "key_reason":        key_reason,
        "bull_summary":      judgment.get("bull_summary", ""),
        "bear_summary":      judgment.get("bear_summary", ""),
        "sentinel_summary":  state.sentinel_report,
        "market_report":     state.market_report,
        "stop_loss":         _parse_stop_loss(judgment.get("stop_loss_hint", "")),
        "investment_plan":   judgment.get("investment_plan", ""),
        "exit_plan":         "",
        "debate_mode":       "normal",
        "sentinel_conflicts": sentinel_conflicts,
    }


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
