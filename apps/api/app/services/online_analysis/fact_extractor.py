"""F7 Stage A · 事实提取（抗投毒核武器）

把多源新闻 + 资金/行情数据 喂给 LLM，强制只保留可验证事实。

输入: FetchResult (来自 UnifiedFetcher)
输出: {
  "verifiable_facts": [{fact, source, weight, type}, ...],
  "rejected_as_opinion": [{text, reason}, ...],
  "stats": {input_count, kept_count, rejected_count, drop_rate}
}
"""
from loguru import logger

from .llm_client import llm_json_call, estimate_cost_cny
from .prompts import (
    FACT_EXTRACTION_SYSTEM,
    build_fact_extraction_user_prompt,
)
from .unified_fetcher import FetchResult


async def extract_facts(stock_name: str, fetch_result: FetchResult) -> dict:
    """运行 F7 Stage A 事实提取

    Args:
        stock_name:   股票中文名
        fetch_result: UnifiedFetcher 的输出

    Returns:
        dict: { verifiable_facts, rejected_as_opinion, stats, llm_meta }
    """
    news_dicts = [n.to_dict() for n in fetch_result.news_items]
    user_prompt = build_fact_extraction_user_prompt(
        stock_name    = stock_name,
        news_items    = news_dicts,
        market_data   = fetch_result.market_data,
        capital_flow  = fetch_result.capital_flow_data,
    )

    parsed, meta = llm_json_call(
        system     = FACT_EXTRACTION_SYSTEM,
        user       = user_prompt,
        max_tokens = 8000,    # F7 可能输出较长事实清单，给足以防截断
        temperature= 0.2,     # 严格事实，不要发挥
    )

    if parsed is None:
        logger.warning("fact_extractor: LLM 解析失败，降级返回空 (error={})", meta.get("error"))
        return {
            "verifiable_facts":    [],
            "rejected_as_opinion": [],
            "stats": {
                "input_count":    len(news_dicts),
                "kept_count":     0,
                "rejected_count": 0,
                "drop_rate":      0.0,
                "error":          meta.get("error", "unknown"),
                "llm_failure":    True,    # 标记是 LLM 失败，让上层 UI 显示「AI 临时不可用」而不是「信号不足」
            },
            "llm_meta": meta,
        }

    facts    = parsed.get("verifiable_facts", []) or []
    rejected = parsed.get("rejected_as_opinion", []) or []

    # 数据消毒：保证每条 fact 都有必要字段
    # 回填 source_url：根据 LLM 输出的 source_index 关联到原始新闻
    news_list = [n.to_dict() for n in fetch_result.news_items]

    cleaned_facts = []
    for f in facts:
        if not isinstance(f, dict) or not f.get("fact"):
            continue
        src_idx = f.get("source_index", 0)
        try:
            src_idx = int(src_idx) if src_idx else 0
        except (TypeError, ValueError):
            src_idx = 0
        # source_index 是 1-based
        source_url = ""
        source_title = ""
        if 1 <= src_idx <= len(news_list):
            ref = news_list[src_idx - 1]
            source_url = ref.get("url") or ""
            source_title = ref.get("title") or ""

        cleaned_facts.append({
            "fact":         str(f.get("fact"))[:500],
            "source":       str(f.get("source", "未知"))[:32],
            "source_index": src_idx,
            "source_url":   source_url,
            "source_title": source_title,
            "weight":       float(f.get("weight", 0.5)),
            "type":         str(f.get("type", "其他"))[:16],
        })

    cleaned_rejected = []
    for r in rejected:
        if not isinstance(r, dict) or not r.get("text"):
            continue
        src_idx = r.get("source_index", 0)
        try:
            src_idx = int(src_idx) if src_idx else 0
        except (TypeError, ValueError):
            src_idx = 0
        source_url = ""
        source_title = ""
        if 1 <= src_idx <= len(news_list):
            ref = news_list[src_idx - 1]
            source_url = ref.get("url") or ""
            source_title = ref.get("title") or ""

        cleaned_rejected.append({
            "text":         str(r.get("text"))[:200],
            "reason":       str(r.get("reason", ""))[:200],
            "source_index": src_idx,
            "source_url":   source_url,
            "source_title": source_title,
        })

    stats = {
        "input_count":    len(news_dicts),
        "kept_count":     len(cleaned_facts),
        "rejected_count": len(cleaned_rejected),
        "drop_rate":      (len(cleaned_rejected) / max(1, len(news_dicts))) if news_dicts else 0.0,
    }

    cost = estimate_cost_cny(meta.get("tokens_in", 0), meta.get("tokens_out", 0))
    logger.info(
        "F7 Stage A {}: input={} kept={} rejected={} cost=¥{}",
        stock_name, stats["input_count"], stats["kept_count"],
        stats["rejected_count"], cost,
    )

    return {
        "verifiable_facts":    cleaned_facts,
        "rejected_as_opinion": cleaned_rejected,
        "stats":               stats,
        "llm_meta":            meta,
    }
