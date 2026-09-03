"""BearResearcher — 空头辩论节点

Sentinel 已验证利空事实注入辩论 prompt，明确区分真实利空与投毒噪音。
"""
import os
from loguru import logger
from openai import OpenAI

from agents.state import EnhancedAgentState, DebateState


def run_bear_researcher(state: EnhancedAgentState, debate: DebateState) -> DebateState:
    """一轮空头辩论，更新 DebateState"""
    verified_str = _format_facts(state.verified_facts)
    filtered_str = _format_filtered(state.filtered_facts)

    system = (
        "你是一位空头分析师，主张卖出/回避该股票。使用简体中文输出所有内容。\n\n"
        "【重要约束】：\n"
        f"Sentinel 新闻系统已分析过以下信息：\n"
        f"已验证真实事实（可引用作为证据）：\n{verified_str}\n\n"
        f"以下内容已被判定为投毒/低质新闻（禁止引用作为证据）：\n{filtered_str}\n\n"
        "在你的空头论证中，必须区分真实利空和噪音：\n"
        "- 用已验证的事实支撑空头逻辑\n"
        "- 对被过滤内容如需提及，必须注明'此信息已被 Sentinel 过滤，可信度低'"
    )

    sentiment_note = ""
    if state.sentinel_opinion == "看多":
        sentiment_note = (
            f"\n注意：Sentinel 新闻面综合研判为「{state.sentinel_opinion}」"
            f"（置信度 {int(state.sentinel_confidence*100)}%）。"
            "你需要解释为什么尽管新闻面偏正面，但技术面或其他风险仍支撑空头逻辑。"
        )

    user = (
        f"股票：{state.stock_name}（{state.ticker}）\n"
        f"当日涨跌：{state.change_pct:+.2f}% | 触发条件：{state.trigger_desc}\n\n"
        f"技术面分析：\n{state.market_report}\n\n"
        f"Sentinel 新闻情报摘要：\n{state.sentinel_report}\n"
        f"{sentiment_note}\n\n"
        f"当前辩论历史：\n{debate.history}\n\n"
        f"多头上一轮论点：\n{debate.current_response}\n\n"
        "请发表有说服力的空头论点，明确区分真实利空与被过滤噪音，约 300-400 字。"
    )

    response = _call_llm(user)
    argument = f"【空头分析师】：{response}"

    return DebateState(
        history=debate.history + "\n" + argument,
        bull_history=debate.bull_history,
        bear_history=debate.bear_history + "\n" + argument,
        current_response=argument,
        count=debate.count + 1,
    )


def _format_facts(facts: list) -> str:
    if not facts:
        return "（暂无已验证事实）"
    return "\n".join(
        f"  {i}. [{f.get('source','')}] {f.get('fact','')} （可信度 {f.get('weight',0):.2f}）"
        for i, f in enumerate(facts, 1)
    )


def _format_filtered(facts: list) -> str:
    if not facts:
        return "（无被过滤内容）"
    return "\n".join(f"  - {f.get('text','')}" for f in facts)


def _call_llm(user: str) -> str:
    system = "你是一位专业的空头股票分析师，使用简体中文，直接输出分析文本（非 JSON）。"
    # ONE_API_* 是内部 SaaS 网关的历史命名 · 开源版用户没那个网关 · 缺 key 时回退到
    # .env 里统一的 LLM_* 三件套。timeout 60 → 120 因为推理型模型 reasoning tokens 一多就 40-60s+。
    api_key   = os.getenv("ONE_API_KEY")      or os.getenv("LLM_API_KEY", "")
    base_url  = os.getenv("ONE_API_BASE_URL") or os.getenv("LLM_BASE_URL", "http://104.197.139.51:3000/v1")
    model     = os.getenv("ONE_API_MODEL")    or os.getenv("LLM_DEFAULT_MODEL", "gemini-3.8-flash")
    if not api_key:
        logger.warning("bear_researcher: 无可用 key · 请在 .env 里填 LLM_API_KEY(或 ONE_API_KEY)")
        return "（空头分析暂不可用）"
    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=120)
        resp  = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            # DeepSeek-R1 等推理型模型 reasoning tokens 常 2000-5000+,
            # 老值 800 全被 reasoning 吃完 · message.content 返空。抬到 4096
            # 与 bull_researcher 对齐,同时补 finish_reason/usage 便于排错。
            max_tokens=4096,
            temperature=0.7,
        )
        content = resp.choices[0].message.content or ""
        if not content.strip():
            finish = resp.choices[0].finish_reason if resp.choices else "unknown"
            usage  = resp.usage
            logger.warning(
                "bear_researcher: content 空 · finish={} tokens_in={} tokens_out={} model={}",
                finish,
                usage.prompt_tokens if usage else "?",
                usage.completion_tokens if usage else "?",
                model,
            )
            return "（空头分析暂不可用）"
        return content
    except Exception as e:
        logger.warning("bear_researcher LLM call failed: {}", e)
        return "（空头分析暂不可用）"
