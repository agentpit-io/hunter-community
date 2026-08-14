"""ComprehensiveJudge — 综合裁判节点

同时掌握市场面（技术分析）和新闻面（Sentinel 已验证事实）。
强制解释技术面与新闻面矛盾，不能各说各话。
"""
from loguru import logger

from agents.state import EnhancedAgentState
from agents.sentinel.llm_client import llm_json_call


def run_comprehensive_judge(state: EnhancedAgentState) -> dict:
    """
    对 Bull/Bear 辩论做综合裁决。
    Returns dict: {decision, confidence, key_reason, bull_summary, bear_summary,
                   investment_plan, stop_loss_hint}
    """
    debate       = state.debate_state
    verified_str = _format_facts(state.verified_facts)

    if state.sentinel_confidence >= 0.8:
        confidence_rule = "Sentinel 置信度极高（≥80%），其已验证事实权重等同于一手信息，优先采信。"
    elif state.sentinel_confidence < 0.5:
        confidence_rule = "Sentinel 置信度偏低（<50%），新闻面证据权重减半，以技术面为主。"
    else:
        confidence_rule = f"Sentinel 置信度 {int(state.sentinel_confidence*100)}%，正常权重采信。"

    system = (
        "你是综合投资裁判，同时掌握技术面和新闻面情报，使用简体中文输出。\n\n"
        "【情报权威规则】\n"
        f"{confidence_rule}\n"
        "已验证事实（可信度 > 0.85）权重等同于一手信息。\n"
        "被过滤的新闻不能作为任何论点的依据。\n"
        "当新闻面和技术面出现矛盾时，必须明确解释矛盾原因，再给出判断。\n\n"
        "做出明确的买入/持有/卖出建议，不要默认选择持有；要基于最强论据做决策。\n\n"
        "回复 JSON 格式：\n"
        '{"decision": "BUY|HOLD|SELL", "confidence": 0.0-1.0, '
        '"key_reason": "一句话核心理由", '
        '"bull_summary": "多头核心论点（50字）", "bear_summary": "空头核心论点（50字）", '
        '"investment_plan": "详细操作计划（200字以内）", '
        '"stop_loss_hint": "止损参考价位或建议"}'
    )

    user = (
        f"股票：{state.stock_name}（{state.ticker}）\n"
        f"当日涨跌：{state.change_pct:+.2f}% | 触发条件：{state.trigger_desc}\n\n"
        f"【技术面报告】\n{state.market_report}\n\n"
        f"【Sentinel 新闻情报】\n{state.sentinel_report}\n\n"
        f"【已验证核心事实】\n{verified_str}\n\n"
        f"【Bull vs Bear 完整辩论记录】\n{debate.history}\n\n"
        "请对以上辩论做出综合裁决。"
    )

    # Deep Think · 综合判官是决策关键点 · 用 Pro 模型换 8-15s 换更好判断质量
    # max_tokens 2500 → 8192 · reasoning 型模型下 2500 不够(见 llm_client 修复)
    parsed, meta = llm_json_call(system, user, deep=True, max_tokens=8192, temperature=0.4)
    if parsed:
        return parsed

    logger.warning("comprehensive_judge LLM parse failed: {}", meta.get("error"))
    return {
        "decision":        "HOLD",
        "confidence":      0.5,
        "key_reason":      "裁判 AI 暂不可用，建议人工判断",
        "bull_summary":    debate.bull_history[-300:] if debate.bull_history else "",
        "bear_summary":    debate.bear_history[-300:] if debate.bear_history else "",
        "investment_plan": "AI 分析暂不可用，请参考辩论记录自行判断。",
        "stop_loss_hint":  "",
    }


def _format_facts(facts: list) -> str:
    if not facts:
        return "（暂无已验证事实）"
    return "\n".join(
        f"  {i}. [{f.get('source','')}] {f.get('fact','')} （可信度 {f.get('weight',0):.2f}）"
        for i, f in enumerate(facts, 1)
    )
