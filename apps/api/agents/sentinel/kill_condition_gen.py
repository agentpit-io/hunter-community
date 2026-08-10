"""Kill Condition AI 生成 · 两步流程

用户填 thesis → 点「AI 生成」→ 这个模块跑 → 返回 2-3 条建议 →
用户编辑 → 点「开始分析」
"""
from loguru import logger

from .llm_client import llm_json_call, estimate_cost_cny
from .prompts import (
    KILL_CONDITION_SYSTEM,
    build_kill_condition_user_prompt,
)


async def generate_kill_conditions(stock_name: str, thesis_text: str) -> dict:
    """根据 thesis 生成 2-3 条 kill condition 建议

    Returns:
        {
          "suggestions": [{text, type, rationale, confidence}, ...],
          "llm_meta":    {...},
          "error":       str | None
        }
    """
    if not thesis_text or not thesis_text.strip():
        return {
            "suggestions": [],
            "llm_meta": {},
            "error": "thesis_empty",
        }

    user_prompt = build_kill_condition_user_prompt(stock_name, thesis_text)

    parsed, meta = llm_json_call(
        system     = KILL_CONDITION_SYSTEM,
        user       = user_prompt,
        max_tokens = 1500,
        temperature= 0.4,    # 略带创意但还是要专业
    )

    if parsed is None:
        return {
            "suggestions": [],
            "llm_meta":    meta,
            "error":       meta.get("error", "llm_failed"),
        }

    raw_suggestions = parsed.get("suggestions", []) or []
    cleaned = []
    for s in raw_suggestions[:5]:    # 最多取前 5 条
        if not isinstance(s, dict) or not s.get("text"):
            continue
        cleaned.append({
            "text":      str(s.get("text", "")).strip()[:200],
            "type":      str(s.get("type", "其他"))[:16],
            "rationale": str(s.get("rationale", ""))[:300],
            "source":    "ai",       # 标记来源 — 用户编辑后会改成 "user"
        })

    cost = estimate_cost_cny(meta.get("tokens_in", 0), meta.get("tokens_out", 0))
    logger.info(
        "kill_condition_gen {}: thesis_len={} suggestions={} cost=¥{}",
        stock_name, len(thesis_text), len(cleaned), cost,
    )

    return {
        "suggestions": cleaned,
        "llm_meta":    meta,
        "error":       None,
    }
