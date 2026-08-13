"""共享 LLM 客户端 — 在线分析全部 LLM 调用走这里

封装:
  - oneapi 网关地址 / api_key / 模型选择
  - 统一 JSON 模式 + 4 层 fallback 解析
  - Token 统计
  - 错误兜底
"""
import os
from typing import Optional

from loguru import logger
from openai import OpenAI

from .prompts import parse_llm_json


# ONE_API_* 是内部 SaaS 网关的历史命名,开源版用户没有那个网关。
# 缺 ONE_API_KEY 时自动退回到 .env 里的统一 LLM_* 三件套 —— 这样 chat 走通了
# 深度分析也就跟着走通,不需要用户再单独维护一套 key。
_BASE_URL = os.getenv("ONE_API_BASE_URL") or os.getenv("LLM_BASE_URL", "http://104.197.139.51:3000/v1")
_API_KEY  = os.getenv("ONE_API_KEY")     or os.getenv("LLM_API_KEY", "")
_MODEL    = os.getenv("ONE_API_MODEL")   or os.getenv("LLM_DEFAULT_MODEL", "gemini-3-flash-preview")
# 30s 对 DeepSeek/Kimi 等推理型模型 + 长上下文经常不够(reasoning tokens 一多就到 40-60s),
# 抬到 120s 避免 APITimeoutError 把结果吞成 502。上游本身也有超时兜底,不会无限阻塞。
_TIMEOUT  = 120


def get_client() -> OpenAI | None:
    if not _API_KEY:
        logger.warning("online_analysis llm: 无可用 key · 请在 .env 里填 LLM_API_KEY(或 ONE_API_KEY)")
        return None
    return OpenAI(api_key=_API_KEY, base_url=_BASE_URL, timeout=_TIMEOUT)


def llm_json_call(system: str, user: str, *,
                  model: str | None = None,
                  max_tokens: int = 2000,
                  temperature: float = 0.3,
                  retry_on_parse_fail: bool = True) -> tuple[dict | None, dict]:
    """统一 LLM JSON 调用。

    Returns:
        (parsed_dict, meta)
        meta = {tokens_in, tokens_out, model, latency_ms, raw_text, error}
    """
    import time

    client = get_client()
    if client is None:
        return None, {"error": "no_api_key", "tokens_in": 0, "tokens_out": 0}

    use_model = model or _MODEL
    t0 = time.time()
    try:
        completion = client.chat.completions.create(
            model=use_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.warning("llm_json_call (json_object mode) failed: {}, fallback to plain", e)
        # 退回普通模式（网关不支持 response_format 时）
        try:
            completion = client.chat.completions.create(
                model=use_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as e2:
            return None, {"error": str(e2), "tokens_in": 0, "tokens_out": 0,
                          "latency_ms": int((time.time()-t0)*1000)}

    raw   = completion.choices[0].message.content or ""
    usage = completion.usage
    meta = {
        "model":      use_model,
        "tokens_in":  usage.prompt_tokens if usage else 0,
        "tokens_out": usage.completion_tokens if usage else 0,
        "latency_ms": int((time.time() - t0) * 1000),
        "raw_text":   raw,
        "error":      None,
    }

    parsed = parse_llm_json(raw)
    if parsed is None and retry_on_parse_fail:
        # 一次重试：明确指出 JSON 格式要求
        logger.warning("llm_json_call parse failed, retrying with stricter prompt")
        strict_system = system + "\n\n严格要求：你的整个回答必须是合法 JSON。第一个字符必须是 {，最后一个字符必须是 }。不要添加任何 markdown 包装或解释。"
        try:
            completion = client.chat.completions.create(
                model=use_model,
                messages=[
                    {"role": "system", "content": strict_system},
                    {"role": "user",   "content": user},
                ],
                max_tokens=max_tokens,
                temperature=0.1,    # 更严格
                response_format={"type": "json_object"},
            )
            raw2   = completion.choices[0].message.content or ""
            usage2 = completion.usage
            meta["tokens_in"]  += (usage2.prompt_tokens if usage2 else 0)
            meta["tokens_out"] += (usage2.completion_tokens if usage2 else 0)
            meta["latency_ms"]  = int((time.time() - t0) * 1000)
            meta["raw_text"]    = raw2
            parsed = parse_llm_json(raw2)
        except Exception as e:
            meta["error"] = f"retry_failed: {e}"

    if parsed is None:
        meta["error"] = "json_parse_failed"
        logger.warning("llm_json_call: parse failed after retry, raw={}", meta["raw_text"][:300])

    return parsed, meta


# ─── 成本估算（粗略，按 oneapi 计费 0.1 元 / 1k tokens 估）──────────────
_COST_PER_1K_TOKENS_CNY = 0.05


def estimate_cost_cny(tokens_in: int, tokens_out: int) -> float:
    """粗略估算单次调用人民币成本"""
    total = tokens_in + tokens_out
    return round(total / 1000 * _COST_PER_1K_TOKENS_CNY, 4)
