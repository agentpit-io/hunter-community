"""ExitStrategyAdvisor — 止损策略顾问节点（仅在 kill condition 触发时激活）

不讨论"是否持有"，专注最优退出策略：立即清仓 / 分批减仓 / 移动止损。
"""
from loguru import logger

from agents.state import EnhancedAgentState
from agents.sentinel.llm_client import llm_json_call


def run_exit_strategy_advisor(state: EnhancedAgentState) -> dict:
    """
    Returns dict:
    {recommended_option, rationale, exit_plan, price_targets}
    """
    verified_str = _format_facts(state.verified_facts)

    system = (
        "你是止损策略顾问，专注于帮助投资者以最优方式退出持仓。使用简体中文输出。\n"
        "止损条件已触发，你的任务不是判断是否持有，而是讨论最优退出策略。\n\n"
        "可选退出策略：\n"
        "  选项A - 立即清仓：适用于基本面恶化、系统性风险；代价是可能卖在低点\n"
        "  选项B - 分批减仓（3-5天）：适用于流动性好、下跌不急剧；暴露时间较长\n"
        "  选项C - 移动止损（设定止损价）：适用于可能企稳反弹的超跌；需实时监控\n\n"
        "回复 JSON 格式：\n"
        '{"recommended_option": "A|B|C", "rationale": "推荐理由（100字）", '
        '"exit_plan": "详细退出计划（150字）", "price_targets": "具体价位建议"}'
    )

    user = (
        f"股票：{state.stock_name}（{state.ticker}）\n"
        f"当日涨跌：{state.change_pct:+.2f}%\n"
        f"触发止损条件：{state.kill_condition_desc}\n\n"
        f"【技术面报告】\n{state.market_report}\n\n"
        f"【已验证新闻事实】\n{verified_str}\n\n"
        f"【Sentinel 综合研判】{state.sentinel_opinion} "
        f"（置信度 {int(state.sentinel_confidence*100)}%）\n\n"
        "基于以上信息，推荐最优退出策略并给出具体操作建议。"
    )

    # max_tokens 1000 → 8192 · reasoning 型模型下 1000 不够(见 llm_client 修复)
    parsed, meta = llm_json_call(system, user, max_tokens=8192, temperature=0.3)
    if parsed:
        return parsed

    logger.warning("exit_strategy_advisor LLM failed: {}", meta.get("error"))
    return {
        "recommended_option": "B",
        "rationale": f"止损条件已触发（{state.kill_condition_desc}），建议分批减仓以降低风险",
        "exit_plan": "建议在 3-5 个交易日内分批减仓，首批减仓 30-50%，剩余设置移动止损。",
        "price_targets": "具体价位请参考技术支撑位。",
    }


def _format_facts(facts: list) -> str:
    if not facts:
        return "（暂无已验证事实）"
    return "\n".join(
        f"  {i}. [{f.get('source','')}] {f.get('fact','')} （可信度 {f.get('weight',0):.2f}）"
        for i, f in enumerate(facts, 1)
    )
