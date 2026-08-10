"""列 2 · 普通 AI（套壳基线）pipeline

故意"无防御"地处理新闻 — 证明对比价值。

实现策略：
  - 把全部 12+ 条新闻不加权重地拼进 prompt
  - 不做事实提取、不做对立面、不做信源加权
  - 让 LLM 自然被 PR 软文 + 模板水军带跑
  - 返回 4 步骤展示 + 最终结论
"""
from loguru import logger

from .llm_client import llm_json_call, estimate_cost_cny
from .prompts import NAIVE_SYSTEM, build_naive_user_prompt
from .unified_fetcher import FetchResult


async def run_naive_pipeline(stock_name: str, change_pct: float,
                              fetch_result: FetchResult) -> dict:
    """跑套壳 AI 管道，返回展示用的 4 步骤 + 最终结论

    Returns:
        {
          "steps": [
            {"name": "...", "status": "done|warn|error", "detail": "..."}
          ],
          "conclusion": {
            "title":         "...",
            "user_message":  "...",
            "recommendation":"买入|持有|卖出",
            "is_poisoned":   bool,    // 是否被投毒了
          },
          "llm_meta": {...}
        }
    """
    news_dicts = [n.to_dict() for n in fetch_result.news_items]

    # ─── 4 步流水线展示 ───
    steps = [
        {
            "name":   "Step 1 · 接收新闻",
            "status": "done",
            "detail": f"不加区分地接收全部 {len(news_dicts)} 条新闻",
        },
        {
            "name":   "Step 2 · 直接喂给 LLM",
            "status": "warn",
            "detail": "没有信源加权 / 没有事实校验 / 没有异常检测",
        },
    ]

    # 真实 LLM 调用
    user_prompt = build_naive_user_prompt(stock_name, change_pct, news_dicts)
    parsed, meta = llm_json_call(
        system     = NAIVE_SYSTEM,
        user       = user_prompt,
        max_tokens = 1500,
        temperature= 0.5,    # 套壳 AI 反而要给点温度让它发挥
    )

    # 套壳 prompt 没要求 JSON 输出，所以 parsed 可能为 None
    raw_text = meta.get("raw_text", "")

    # 兜底：如果 LLM 不听话还是输出了 JSON，提取所有 string value 拼成段落
    raw_stripped = raw_text.strip()
    if raw_stripped.startswith("{") or raw_stripped.startswith("```"):
        from .prompts import parse_llm_json
        parsed_naive = parse_llm_json(raw_text)
        if parsed_naive and isinstance(parsed_naive, dict):
            # 把 dict 里的所有 string value 拼接成自然段落
            sentences = []
            for k, v in parsed_naive.items():
                if isinstance(v, str) and len(v) > 5:
                    sentences.append(v)
                elif isinstance(v, dict):
                    for sub_v in v.values():
                        if isinstance(sub_v, str) and len(sub_v) > 5:
                            sentences.append(sub_v)
            if sentences:
                raw_text = "  ".join(sentences)

    steps.append({
        "name":   "Step 3 · LLM 内部推理",
        "status": "error",
        "detail": "LLM 同时接收了 PR 软文 + 模板水军 + 权威源，无法区分",
    })

    # 简单从 raw_text 里抽 conclusion（不依赖 JSON）
    conclusion_text = raw_text.strip()[:500]

    # 判断是否被投毒（启发式）：
    # - 文本里出现"看好/买入/利好/前景广阔"等关键词且没有平衡的风险提示 → 被带跑
    naive_keywords = ["看好", "建议买入", "前景广阔", "买入", "强烈推荐", "目标价"]
    has_naive_bullish = any(k in raw_text for k in naive_keywords)
    has_risk_balance = any(k in raw_text for k in ["风险", "谨慎", "对立面", "做空", "减持"])
    is_poisoned = has_naive_bullish and not has_risk_balance

    steps.append({
        "name":   "Step 4 · 形成结论",
        "status": "error" if is_poisoned else "done",
        "detail": ("被 PR 软文 + 模板水军成功带跑，判定为利好" if is_poisoned
                   else "勉强保持中性，但无可验证依据"),
    })

    # 标题：把第一行作为标题
    title_line = (conclusion_text.split("\n", 1)[0]).strip()[:80]
    if not title_line:
        title_line = f"{stock_name} 综合分析"

    recommendation = "买入" if is_poisoned else "持有"
    if "卖出" in raw_text or "减仓" in raw_text:
        recommendation = "卖出"

    conclusion = {
        "title":          title_line,
        "user_message":   conclusion_text,
        "recommendation": recommendation,
        "is_poisoned":    is_poisoned,
    }

    cost = estimate_cost_cny(meta.get("tokens_in", 0), meta.get("tokens_out", 0))
    logger.info(
        "naive_pipeline {}: rec={} poisoned={} cost=¥{}",
        stock_name, recommendation, is_poisoned, cost,
    )

    return {
        "steps":      steps,
        "conclusion": conclusion,
        "llm_meta":   meta,
    }
