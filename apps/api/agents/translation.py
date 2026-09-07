"""LLM 输出中文兜底 · 辩论 agent 跑出英文时强制翻成简体中文。

背景: gemini-flash 偶发不听 system prompt 的 "开头必须中文" 硬约束。

2026-09-07 修正判据（重要）:
  旧版触发条件是 `not contains_chinese(text)` —— 只要正文里夹了一个中文词
  （股票名几乎必然出现），整段英文也会被判为"有中文"直接放行，用户就看到了
  英文段落。现在改成看「净化后还有没有英文散文」:
    sanitize_llm_text 先剥思考前言、逐句丢英文散文;
    剩下的中文够用就直接返回，否则才把原文送去翻译。

契约变更: 兜底全部失败时返回 "" (不再透传英文原文)。
  调用方必须自己给中文占位，例如 `ensure_chinese(raw) or "（多头分析暂不可用）"`。
  产品铁律是"空的比假的好" —— 英文正文属于"假的"那一类。
"""
import os
from loguru import logger
from openai import OpenAI

from agents.text_sanitizer import (
    ZH_ONLY_RULE,
    contains_chinese,
    has_english_prose,
    sanitize_llm_text,
    strip_thinking_preamble,
)


_TRANSLATE_SYSTEM = (
    "你是金融领域中英翻译助手。"
    "将用户提供的英文分析原样翻译成简体中文,保留原文的结构、术语、数字、段落划分。"
    "只输出译文本身,不要任何前言、总结、说明或引号包裹,不要输出 JSON。"
    "开头第一个字符必须是中文。"
)


def ensure_chinese(text: str, *, model: str | None = None) -> str:
    """净化 text 里的英文散文；净化不出可用中文时调 LLM 整段翻译。

    Args:
        text:  上游 agent 的原始输出
        model: 翻译模型 · 默认沿用 DEBATE_MODEL/gemini-3.5-flash · 与辩论 agent 一致

    Returns:
        简体中文正文；净化 + 翻译都拿不到中文时返回 ""（调用方给中文占位）。
    """
    if not text:
        return ""

    cleaned = sanitize_llm_text(text)
    if cleaned and not has_english_prose(cleaned):
        return cleaned

    _model = model or os.getenv("DEBATE_MODEL") or os.getenv("LLM_DEFAULT_MODEL", "gemini-3.5-flash")
    # ONE_API_* 是 SaaS 网关历史命名 · 缺 key 时回退 .env 里统一的 LLM_* 三件套
    # (与 hunter-community 同步)。
    api_key  = os.getenv("ONE_API_KEY")      or os.getenv("LLM_API_KEY", "")
    base_url = os.getenv("ONE_API_BASE_URL") or os.getenv("LLM_BASE_URL", "http://104.197.139.51:3000/v1")
    if not api_key:
        logger.warning("ensure_chinese: 无 key · 无法翻译 · 返回净化结果(可能为空)")
        return cleaned
    logger.warning("ensure_chinese: 检测到英文正文 · 触发翻译兜底 · raw={}", text[:120])
    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=45)
        resp = client.chat.completions.create(
            model=_model,
            messages=[
                {"role": "system", "content": _TRANSLATE_SYSTEM + ZH_ONLY_RULE},
                {"role": "user", "content": text},
            ],
            max_tokens=1200,
            temperature=0.2,
        )
        translated = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning("ensure_chinese: 翻译调用失败 · err={}", e)
        return cleaned

    fixed = sanitize_llm_text(translated)
    if not fixed or has_english_prose(fixed):
        logger.warning("ensure_chinese: 翻译后仍不合格 · model={} · sample={}", _model, translated[:120])
        return cleaned
    logger.info("ensure_chinese: 英文回退翻译成功 · model={}", _model)
    return fixed
