"""SentinelNewsAgent — 新闻情报节点（多智能体第 5 个分析师）

直接调用 hermes-api 内部的 Sentinel 分析管道（run_defense_pipeline），
将 5 层过滤结果格式化为辩论可用的情报报告，注入 Bull/Bear 辩论 prompt。
"""
from loguru import logger

from agents.sentinel.unified_fetcher import UnifiedFetcher
from agents.sentinel.pipeline import run_defense_pipeline


async def run_sentinel_news_agent(
    ticker: str,
    stock_name: str,
    change_pct: float,
    thesis_text: str,
    kill_conditions: list,
) -> dict:
    """
    Returns:
        {
            sentinel_report, sentinel_opinion, sentinel_confidence,
            verified_facts, filtered_facts,
            kill_condition_triggered, kill_condition_desc, debate_mode
        }
    """
    try:
        fetcher      = UnifiedFetcher()
        fetch_result = await fetcher.fetch_all(ticker, hours=48, stock_name=stock_name)

        defense = await run_defense_pipeline(
            stock_name=stock_name,
            change_pct=change_pct,
            fetch_result=fetch_result,
            thesis_text=thesis_text,
            kill_conditions=kill_conditions,
        )
    except Exception as e:
        logger.warning("sentinel_news_agent pipeline failed for {}: {}", ticker, e)
        return _degraded_result()

    facts_result  = defense.get("facts",       {})
    conclusion    = defense.get("conclusion",   {})

    verified = facts_result.get("verifiable_facts",    [])
    rejected = facts_result.get("rejected_as_opinion", [])
    stats    = facts_result.get("stats", {})

    kill_conds     = conclusion.get("kill_conditions", [])
    triggered_list = [kc for kc in kill_conds if kc.get("triggered")]
    kill_triggered = len(triggered_list) > 0
    kill_desc      = "；".join(kc.get("text", "") for kc in triggered_list)

    opinion    = conclusion.get("opinion",    "中性")
    confidence = float(conclusion.get("confidence", 0.5))

    total_news = stats.get("input_count", len(fetch_result.news_items))
    kept_count = stats.get("kept_count", len(verified))
    drop_count = total_news - kept_count

    sentinel_report = _format_report(
        stock_name=stock_name,
        total_news=total_news,
        kept_count=kept_count,
        drop_count=drop_count,
        verified=verified[:5],
        rejected=rejected[:3],
        opinion=opinion,
        confidence=confidence,
        kill_triggered=kill_triggered,
        kill_desc=kill_desc,
    )

    return {
        "sentinel_report":          sentinel_report,
        "sentinel_opinion":         opinion,
        "sentinel_confidence":      confidence,
        "verified_facts":           verified[:5],
        "filtered_facts":           rejected[:3],
        "kill_condition_triggered": kill_triggered,
        "kill_condition_desc":      kill_desc,
        "debate_mode":              "exit_strategy" if kill_triggered else "normal",
    }


def _format_report(
    stock_name, total_news, kept_count, drop_count,
    verified, rejected, opinion, confidence,
    kill_triggered, kill_desc,
) -> str:
    lines = [f"【新闻情报报告 — Sentinel 系统 · {stock_name}】", ""]
    lines.append(f"📊 分析范围：共抓取 {total_news} 条相关新闻")
    lines.append(
        f"🛡️ 过滤结果：过滤 {drop_count} 条投毒/低质内容，"
        f"保留 {kept_count} 条通过核查的有效信息"
    )
    lines.append("")

    if verified:
        lines.append("✅ 已验证核心事实（高可信度）：")
        for i, f in enumerate(verified, 1):
            fact   = f.get("fact", "")
            source = f.get("source", "")
            weight = f.get("weight", 0)
            lines.append(f"  {i}. [{source}] {fact}（可信度：{weight:.2f}）")
        lines.append("")

    if rejected:
        lines.append("⚠️ 被过滤内容摘要（不建议作为投资依据）：")
        for f in rejected:
            text   = f.get("text", "")
            reason = f.get("reason", "")
            lines.append(f"  - {text}（{reason}）")
        lines.append("")

    pct = int(confidence * 100)
    lines.append(f"📉 综合研判：{opinion}（置信度 {pct}%）")

    if kill_triggered:
        lines.append(f"🚨 止损条件已触发：{kill_desc}")
    else:
        lines.append("🟢 止损条件：未触发")

    return "\n".join(lines)


def _degraded_result() -> dict:
    return {
        "sentinel_report":          "新闻情报暂不可用（Sentinel 分析超时），请基于市场面判断。",
        "sentinel_opinion":         "中性",
        "sentinel_confidence":      0.0,
        "verified_facts":           [],
        "filtered_facts":           [],
        "kill_condition_triggered": False,
        "kill_condition_desc":      "",
        "debate_mode":              "normal",
    }
