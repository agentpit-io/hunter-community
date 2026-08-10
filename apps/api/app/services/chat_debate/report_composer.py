"""Chat debate 最终 markdown 报告拼接

把 agents/ 跑完的 state + judgment + final 编成一份可直接送前端 chat 消息流
的报告 · 自动接 Phase 2b Artifact publish (inferArtifactTitle 会抽首行 h1)
"""
from datetime import datetime, timezone, timedelta

from agents.state import EnhancedAgentState


_CST = timezone(timedelta(hours=8))

_DECISION_EMOJI = {
    "BUY":  "🟢",
    "SELL": "🔴",
    "HOLD": "🟡",
}
_DECISION_CN = {
    "BUY":  "买入",
    "SELL": "卖出",
    "HOLD": "持有",
}


def compose_debate_report(
    state: EnhancedAgentState,
    judgment: dict,
    final: dict,
    user_question: str = "",
) -> str:
    """
    Args:
        state:         EnhancedAgentState · 含 market_report / sentinel_report / debate_state
        judgment:      ComprehensiveJudge 输出
        final:         RiskJudge 输出(最终决策)
        user_question: 用户原始问题·作为报告开头引言

    Returns:
        完整 markdown 报告字符串
    """
    now = datetime.now(_CST).strftime("%Y-%m-%d %H:%M CST")
    decision = final.get("decision", "HOLD")
    emoji = _DECISION_EMOJI.get(decision, "🟡")
    decision_cn = _DECISION_CN.get(decision, "持有")
    confidence_pct = int(round(float(final.get("confidence", 0.5)) * 100))
    key_reason = final.get("key_reason", "")
    stop_loss = final.get("stop_loss")
    stop_loss_str = f"¥{stop_loss:.2f}" if isinstance(stop_loss, (int, float)) else "见操作建议"

    debate = state.debate_state
    bull_hist = (debate.bull_history or "").strip()
    bear_hist = (debate.bear_history or "").strip()
    market_report = (state.market_report or "(无)").strip()
    sentinel_report = (state.sentinel_report or "(无)").strip()
    verified = state.verified_facts or []
    investment_plan = final.get("investment_plan", "") or judgment.get("investment_plan", "")
    sentinel_conflicts = bool(final.get("sentinel_conflicts"))
    conflict_note = "\n\n> ⚠️ **注意**:Sentinel 新闻面与技术面判断有冲突 · 已下调置信度" if sentinel_conflicts else ""

    parts: list[str] = []

    # ── 标题 · 决策卡 ─────────────────────────────────
    parts.append(f"# {state.stock_name}({state.ticker}) 多专家辩论深度分析")
    parts.append("")
    parts.append(f"**决策**:{emoji} **{decision_cn}** · 置信度 **{confidence_pct}%**  ")
    parts.append(f"**核心理由**:{key_reason}  ")
    parts.append(f"**止损参考**:{stop_loss_str}  ")
    parts.append(f"**生成时间**:{now} · 6 位分析师参与{conflict_note}")

    if user_question:
        parts.append("")
        parts.append(f"> 用户提问:{user_question}")

    parts.append("")
    parts.append("---")
    parts.append("")

    # ── 一、技术面分析 ─────────────────────────────────
    parts.append("## 一、技术面分析")
    parts.append("")
    parts.append(market_report)
    parts.append("")

    # ── 二、新闻情报(Sentinel 5 层过滤) ────────────
    parts.append("## 二、新闻情报(Sentinel 5 层过滤后)")
    parts.append("")
    parts.append(f"**新闻面综合研判**:{state.sentinel_opinion} · 置信度 {int(state.sentinel_confidence * 100)}%")
    parts.append("")
    parts.append(sentinel_report)

    if verified:
        parts.append("")
        parts.append("### 已验证核心事实")
        parts.append("")
        for i, f in enumerate(verified[:8], 1):
            fact = str(f.get("fact", "")).strip()
            src = str(f.get("source", "")).strip()
            weight = float(f.get("weight", 0.0))
            parts.append(f"{i}. [{src}] {fact} · 可信度 {weight:.2f}")
    parts.append("")

    # ── 三、多空辩论实录 ────────────────────────────────
    parts.append("## 三、多空辩论实录")
    parts.append("")
    if debate.history:
        parts.append(debate.history.strip())
    else:
        parts.append("_(本次未走辩论路径 · 直接进入决策)_")
    parts.append("")

    # ── 四、综合裁决 ────────────────────────────────
    parts.append("## 四、综合判官裁决")
    parts.append("")
    parts.append(f"**多头核心**:{judgment.get('bull_summary', '')}")
    parts.append("")
    parts.append(f"**空头核心**:{judgment.get('bear_summary', '')}")
    parts.append("")
    if judgment.get("investment_plan"):
        parts.append("**详细操作计划**:")
        parts.append("")
        parts.append(str(judgment["investment_plan"]))
        parts.append("")

    # ── 五、风控 3 方辩论 (Sprint B3) ────────────────────────
    risk_debate = final.get("risk_debate") or {}
    if risk_debate:
        parts.append("## 五、风控 3 方辩论")
        parts.append("")
        if risk_debate.get("aggressive"):
            parts.append("### 🔥 激进派")
            parts.append("")
            parts.append(str(risk_debate["aggressive"]).strip())
            parts.append("")
        if risk_debate.get("neutral"):
            parts.append("### ⚖️ 中性派")
            parts.append("")
            parts.append(str(risk_debate["neutral"]).strip())
            parts.append("")
        if risk_debate.get("conservative"):
            parts.append("### 🛡️ 保守派")
            parts.append("")
            parts.append(str(risk_debate["conservative"]).strip())
            parts.append("")

    # ── 六、风控委员会最终执行计划 ────────────────────────
    parts.append("## 六、风控委员会最终执行计划")
    parts.append("")
    parts.append(f"**最终决策**:{emoji} **{decision_cn}** · 置信度 {confidence_pct}%")

    consensus = final.get("consensus_level")
    if consensus:
        consensus_map = {"unanimous": "3 方一致", "majority": "多数同意", "split": "意见分歧", "unavailable": "规则版兜底"}
        parts.append(f"**风控共识**:{consensus_map.get(consensus, consensus)}")

    # 新增字段(若 Deep Think 版给了)
    pos_pct = final.get("position_pct")
    if pos_pct is not None:
        parts.append(f"**建议仓位**:{pos_pct}%")
    stop_pct = final.get("stop_loss_pct")
    if stop_pct is not None:
        parts.append(f"**止损百分比**:-{stop_pct}%")
    else:
        parts.append(f"**止损位**:{stop_loss_str}")
    tp_pct = final.get("take_profit_pct")
    if tp_pct is not None:
        parts.append(f"**止盈目标**:+{tp_pct}%")
    horizon = final.get("time_horizon")
    if horizon:
        parts.append(f"**持有周期**:{horizon}")

    if investment_plan:
        parts.append("")
        parts.append("**执行细节**:")
        parts.append("")
        parts.append(investment_plan)
    parts.append("")

    # ── 免责声明 ────────────────────────────────
    parts.append("---")
    parts.append("")
    parts.append(
        "*本报告由 Hunter 多智能体辩论引擎生成 · "
        "覆盖 8 位角色(技术分析师·Sentinel 新闻官·多头研究员·空头研究员·综合判官·"
        "激进/中性/保守 3 方风控) · 综合判官与风控裁决走 Gemini Deep Think · "
        "不构成投资建议 · 仅供参考*"
    )

    return "\n".join(parts)
