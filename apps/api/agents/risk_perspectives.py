"""风控 3 方辩论 · Sprint B · TradingAgents 移植

3 位风控分析师从不同风险偏好角度评估综合判官的决策 · 各自独立发言:
  · risk_aggressive    · 激进派 · 主张放大仓位/加杠杆/延长持有 · 关注机会成本
  · risk_neutral       · 中性派 · 平衡收益与风险 · 常规仓位与止损
  · risk_conservative  · 保守派 · 主张缩仓/严格止损/快速离场 · 关注下行

各自看到:
  - 综合判官的决策 (BUY / HOLD / SELL + 置信度 + investment_plan)
  - 技术面 + 新闻面情报 + 多空辩论历史
  - 其他两位的前序发言(轮次内)

不使用 Deep Think (Flash 就够 · 只用于风控视角论证 · 综合裁决走 Deep Think)
"""
import os
from loguru import logger
from openai import OpenAI

from agents.state import EnhancedAgentState


def _call_llm(system: str, user: str, max_tokens: int = 3500) -> str:
    """内部通用 LLM 调用 · 返 plain text · 与 bull/bear_researcher 同构"""
    # ONE_API_* 是内部 SaaS 网关的历史命名 · 开源版用户没那个网关 · 缺 key 时回退到
    # .env 里统一的 LLM_* 三件套。timeout 60 → 120 因为推理型模型 reasoning tokens 一多就 40-60s+。
    api_key   = os.getenv("ONE_API_KEY")      or os.getenv("LLM_API_KEY", "")
    base_url  = os.getenv("ONE_API_BASE_URL") or os.getenv("LLM_BASE_URL", "http://104.197.139.51:3000/v1")
    model     = os.getenv("ONE_API_MODEL")    or os.getenv("LLM_DEFAULT_MODEL", "gemini-3.5-flash")
    if not api_key:
        logger.warning("risk_perspective: 无可用 key · 请在 .env 里填 LLM_API_KEY(或 ONE_API_KEY)")
        return "（风控分析暂不可用）"
    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=120)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # 默认 3500 是给 DeepSeek-R1 等推理型模型的 reasoning tokens 留余量,
            # 老值 700 全被 reasoning 吃完 · message.content 返空。
            max_tokens=max_tokens,
            temperature=0.6,
        )
        content = resp.choices[0].message.content or ""
        if not content.strip():
            finish = resp.choices[0].finish_reason if resp.choices else "unknown"
            usage  = resp.usage
            logger.warning(
                "risk_perspective: content 空 · finish={} tokens_in={} tokens_out={} model={}",
                finish,
                usage.prompt_tokens if usage else "?",
                usage.completion_tokens if usage else "?",
                model,
            )
            return "（风控分析暂不可用）"
        return content
    except Exception as e:
        logger.warning("risk_perspective LLM call failed: {}", e)
        return "（风控分析暂不可用）"


def _decision_cn(d: str) -> str:
    return {"BUY": "买入", "SELL": "卖出", "HOLD": "持有"}.get(d, d)


def _common_context(state: EnhancedAgentState, judgment: dict) -> str:
    decision = judgment.get("decision", "HOLD")
    confidence = judgment.get("confidence", 0.5)
    return (
        f"股票:{state.stock_name}({state.ticker})\n"
        f"综合判官决策:**{_decision_cn(decision)}** · 置信度 {int(float(confidence) * 100)}%\n"
        f"判官核心理由:{judgment.get('key_reason', '')}\n"
        f"投资计划:{judgment.get('investment_plan', '')[:300]}\n"
        f"止损参考:{judgment.get('stop_loss_hint', '(未给出)')}\n\n"
        f"【多头核心论点】{judgment.get('bull_summary', '')}\n"
        f"【空头核心论点】{judgment.get('bear_summary', '')}\n\n"
        f"【Sentinel 新闻研判】{state.sentinel_opinion} · 置信度 {int(state.sentinel_confidence * 100)}%\n"
        f"【技术面摘要】{(state.market_report or '')[:400]}\n"
    )


def run_risk_aggressive(state: EnhancedAgentState, judgment: dict, prior_debate: str = "") -> str:
    system = (
        "你是【激进派风控分析师】· 使用简体中文 · 直接输出分析文本(非 JSON)。\n\n"
        "你的立场:市场存在被低估的机会 · 判官的置信度可能偏保守 · 主张:\n"
        "  · 若判官建议 BUY → 论证是否可加大仓位/降低止损位/延长持有周期\n"
        "  · 若判官建议 HOLD → 论证是否可转为 BUY · 抓住反弹机会\n"
        "  · 若判官建议 SELL → 论证是否为过度反应 · 部分保留观察\n"
        "  · 关注机会成本 · 强调 fear of missing out\n"
        "你必须给出量化的仓位/止损/止盈建议(如 +5% 仓位 / 止损放宽至 -8%)。\n"
        "300 字以内 · 犀利有力 · 直接反驳保守派可能提的过度谨慎。"
    )
    user = _common_context(state, judgment)
    if prior_debate:
        user += f"\n【当前轮次已有的其他风控发言】\n{prior_debate}\n"
    user += "\n请发表激进派风控意见。"
    return _call_llm(system, user)


def run_risk_neutral(state: EnhancedAgentState, judgment: dict, prior_debate: str = "") -> str:
    system = (
        "你是【中性派风控分析师】· 使用简体中文 · 直接输出分析文本(非 JSON)。\n\n"
        "你的立场:在激进与保守之间寻求平衡 · 采用标准仓位与止损:\n"
        "  · 认可判官的核心判断 · 但会指出具体执行细节的风险点\n"
        "  · 主张常规仓位(单只股票 5-15%) · 常规止损(-5% ~ -10%)\n"
        "  · 明确指出激进派 vs 保守派各自的盲区\n"
        "  · 强调纪律性 · 反对情绪化操作\n"
        "你必须给出可执行的中庸方案(具体仓位百分比 · 具体止损位)。\n"
        "300 字以内 · 客观理性 · 不激进不保守。"
    )
    user = _common_context(state, judgment)
    if prior_debate:
        user += f"\n【当前轮次已有的其他风控发言】\n{prior_debate}\n"
    user += "\n请发表中性派风控意见。"
    return _call_llm(system, user)


def run_risk_conservative(state: EnhancedAgentState, judgment: dict, prior_debate: str = "") -> str:
    system = (
        "你是【保守派风控分析师】· 使用简体中文 · 直接输出分析文本(非 JSON)。\n\n"
        "你的立场:资金保全优先 · 判官的置信度可能被牛市/新闻情绪推高 · 主张:\n"
        "  · 若判官建议 BUY → 论证是否应减少仓位/收紧止损/缩短持有\n"
        "  · 若判官建议 HOLD → 论证是否应转为部分 SELL · 落袋为安\n"
        "  · 若判官建议 SELL → 论证是否应加速全部离场\n"
        "  · 强调 fat tail 风险 · 强调回撤对复利的破坏\n"
        "你必须给出量化的仓位/止损建议(如 -5% 仓位 / 止损收紧至 -3%)。\n"
        "300 字以内 · 冷静警惕 · 直接反驳激进派可能提的乐观论点。"
    )
    user = _common_context(state, judgment)
    if prior_debate:
        user += f"\n【当前轮次已有的其他风控发言】\n{prior_debate}\n"
    user += "\n请发表保守派风控意见。"
    return _call_llm(system, user)
