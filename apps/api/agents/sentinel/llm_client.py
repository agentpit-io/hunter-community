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
# 缺 ONE_API_KEY 时回退到 .env 里统一的 LLM_* 三件套 —— chat 走通,深度分析
# (MarketAnalyst / Sentinel / ComprehensiveJudge / FinalRiskJudge / ExitStrategy)
# 也跟着走通,不需要用户再单独维护一套 key。
# 注:online_analysis/llm_client.py 里已做过同样的 fallback,这里补齐 agents/ 侧。
_BASE_URL    = os.getenv("ONE_API_BASE_URL") or os.getenv("LLM_BASE_URL", "http://104.197.139.51:3000/v1")
_API_KEY     = os.getenv("ONE_API_KEY")     or os.getenv("LLM_API_KEY", "")
_MODEL       = os.getenv("ONE_API_MODEL")   or os.getenv("LLM_DEFAULT_MODEL", "gemini-3-flash-preview")
# Deep Think 用 · 综合判官 + 风控裁判走此模型 · 决策质量优先于速度/成本
# 若未配 ONE_API_DEEP_MODEL 则依次回退:LLM_DEEP_MODEL → LLM_DEFAULT_MODEL → _MODEL。
# 开源版用户只有一个 LLM_DEFAULT_MODEL 时,深浅走同模型也能跑通;不至于因为
# 硬写 gemini-3.1-pro-preview 而在 DeepSeek/OpenAI 网关上撞 model-not-found。
_DEEP_MODEL  = (
    os.getenv("ONE_API_DEEP_MODEL")
    or os.getenv("LLM_DEEP_MODEL")
    or os.getenv("LLM_DEFAULT_MODEL")
    or _MODEL
)
# 30-45s 对推理型模型 + 长上下文经常不够(reasoning tokens 一多就到 40-60s),
# 抬到 120s 避免 APITimeoutError 把整条辩论链吞成"暂不可用"。上游 SSE 有自己
# 的心跳,不会因为这里等长而无限阻塞前端。
_TIMEOUT     = 120


def get_client() -> OpenAI | None:
    if not _API_KEY:
        logger.warning("agents.sentinel llm: 无可用 key · 请在 .env 里填 LLM_API_KEY(或 ONE_API_KEY)")
        return None
    return OpenAI(api_key=_API_KEY, base_url=_BASE_URL, timeout=_TIMEOUT)


def llm_json_call(system: str, user: str, *,
                  model: str | None = None,
                  deep: bool = False,
                  max_tokens: int = 8192,
                  temperature: float = 0.3,
                  retry_on_parse_fail: bool = True) -> tuple[dict | None, dict]:
    """统一 LLM JSON 调用。

    Args:
        model: 显式指定模型名 · 优先级最高
        deep:  True 时用 Deep Think 模型(gemini-3.1-pro-preview) · False 用 Flash
               判官/风控裁决类角色应传 deep=True · 分析师/研究员用默认 Flash

    Returns:
        (parsed_dict, meta)
        meta = {tokens_in, tokens_out, model, latency_ms, raw_text, error}
    """
    import time

    client = get_client()
    if client is None:
        return None, {"error": "no_api_key", "tokens_in": 0, "tokens_out": 0}

    use_model = model or (_DEEP_MODEL if deep else _MODEL)
    t0 = time.time()
    # 不用 response_format={"type":"json_object"} · DeepSeek V4-pro / R1 等
    # thinking-型模型开启 json 强制模式后,thinking token 仍会漏进 content,
    # 或者提前 EOS 只吐 `{` 就结束 · 反而更难 parse。改成靠 prompt 约束 +
    # parse_llm_json 5+1 层兜底(含剥 <think>) · 实测 DeepSeek 稳定通过。
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
    except Exception as e:
        return None, {"error": str(e), "tokens_in": 0, "tokens_out": 0,
                      "latency_ms": int((time.time()-t0)*1000)}

    raw   = completion.choices[0].message.content or ""
    usage = completion.usage
    finish = completion.choices[0].finish_reason if completion.choices else "unknown"
    meta = {
        "model":      use_model,
        "tokens_in":  usage.prompt_tokens if usage else 0,
        "tokens_out": usage.completion_tokens if usage else 0,
        "latency_ms": int((time.time() - t0) * 1000),
        "raw_text":   raw,
        "finish_reason": finish,
        "error":      None,
    }
    # content 为空 = 上游多半是 reasoning 型模型把 max_tokens 全吃在推理里。
    # 上一次踩坑 bull/bear 也是这个症状,但那时只在 bull/bear/risk 里加了保护,
    # 忘了 llm_json_call。这里把 finish_reason / tokens_out / model 打出来,
    # length=耗尽 · stop=模型真的返空 · content_filter=被拒。
    if not raw.strip():
        logger.warning(
            "llm_json_call: content 空 · finish={} tokens_in={} tokens_out={} model={} max_tokens={}",
            finish, meta["tokens_in"], meta["tokens_out"], use_model, max_tokens,
        )

    parsed = parse_llm_json(raw)
    if parsed is None and retry_on_parse_fail:
        # 一次重试：明确指出 JSON 格式要求 · 且明说"不要 <think>"以打断 thinking 型模型
        logger.warning(
            "llm_json_call parse failed, retrying · finish={} tokens_out={} raw_head={!r}",
            finish, meta["tokens_out"], raw[:120],
        )
        strict_system = system + (
            "\n\n严格要求："
            "你的整个回答必须是合法 JSON。第一个字符必须是 {，最后一个字符必须是 }。"
            "不要输出 <think>...</think>、reasoning 前言、markdown 包装、解释。"
            "如果你需要推理,请在心里推理,直接给结果。"
        )
        try:
            completion = client.chat.completions.create(
                model=use_model,
                messages=[
                    {"role": "system", "content": strict_system},
                    {"role": "user",   "content": user},
                ],
                max_tokens=max_tokens,
                temperature=0.1,    # 更严格
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
