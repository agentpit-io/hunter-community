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

from app.services.lang_guard import ZH_ONLY_RULE
from openai import OpenAI

from .prompts import parse_llm_json


# 30s 对 DeepSeek/Kimi 等推理型模型 + 长上下文经常不够(reasoning tokens 一多就到 40-60s),
# 抬到 120s 避免 APITimeoutError 把结果吞成 502。上游本身也有超时兜底,不会无限阻塞。
_TIMEOUT  = 120


def _resolve() -> tuple[str, str, str]:
    """(base_url, api_key, model) · 每次调用现取,**不能缓存成模块级常量**。

    配置现在可以来自数据库(初始化向导写的),而数据库里的值是运行时可变的 ——
    写成模块级 `os.getenv(...)` 的话,向导保存完 api 进程里还是旧值,要重启容器
    才生效,而「不用重启」正是向导的卖点。

    ONE_API_* 是内部 SaaS 网关的历史命名,开源版用户没有那个网关,留着是为了不
    改内部部署的行为;缺它时走 runtime_config(环境变量非空 → 数据库)。

    ⚠️ **不给 base_url / model 任何默认值。** 这里原来的默认地址是我们自己演示站
    的网关(104.197.139.51:3000)—— 开源用户没填 LLM_BASE_URL 时,他的数据会被
    发到我们的服务器上。模型名同理:猜一个 gemini-3.5-flash 只会让配了 DeepSeek
    的用户收到看不懂的 404。没配就是没配。
    """
    from app.services.runtime_config import llm as _runtime_llm

    cfg = _runtime_llm()
    base_url = (os.getenv("ONE_API_BASE_URL") or "").strip() or cfg.base_url
    api_key = (os.getenv("ONE_API_KEY") or "").strip() or cfg.api_key
    model = (os.getenv("ONE_API_MODEL") or "").strip() or cfg.model
    return base_url, api_key, model


def get_client() -> OpenAI | None:
    base_url, api_key, model = _resolve()
    if not (base_url and api_key and model):
        logger.warning("online_analysis llm: 大模型尚未配置(base_url {} · key {} · model {})"
                       " · 请在 .env 里填 LLM_BASE_URL / LLM_API_KEY / LLM_DEFAULT_MODEL,"
                       "或在首页完成初始化向导",
                       "有" if base_url else "无", "有" if api_key else "无",
                       "有" if model else "无")
        return None
    return OpenAI(api_key=api_key, base_url=base_url, timeout=_TIMEOUT)


def default_model() -> str:
    """当前生效的模型名。没配返回空串(此时 get_client() 也拿不到 client)。"""
    return _resolve()[2]


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

    base_url, _, cfg_model = _resolve()
    use_model = model or cfg_model
    t0 = time.time()
    try:
        completion = client.chat.completions.create(
            model=use_model,
            messages=[
                {"role": "system", "content": system + ZH_ONLY_RULE},
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
                    {"role": "system", "content": system + ZH_ONLY_RULE},
                    {"role": "user",   "content": user},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as e2:
            lat = int((time.time() - t0) * 1000)
            try:
                from app.services import llm_trace
                llm_trace.record_call(
                    caller="online_analysis.llm_json_call",
                    base_url=base_url,
                    req_model=use_model,
                    status=502,
                    latency_ms=lat,
                    error=str(e2),
                )
            except Exception:  # noqa: BLE001
                pass
            return None, {"error": str(e2), "tokens_in": 0, "tokens_out": 0,
                          "latency_ms": lat}

    raw   = completion.choices[0].message.content or ""
    usage = completion.usage
    resp_model = getattr(completion, "model", None) or use_model
    meta = {
        "model":      use_model,
        "resp_model": resp_model,
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
        strict_system = system + ZH_ONLY_RULE + "\n\n严格要求：你的整个回答必须是合法 JSON。第一个字符必须是 {，最后一个字符必须是 }。不要添加任何 markdown 包装或解释。"
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

    if parsed is not None:
        # 语言守卫（2026-09-07 事故）：gemini-flash 偶发用英文写正文字段。
        # 只净化自然语言字段，结构键（decision/impact/code 等）与 meta 原样保留。
        try:
            from app.services.lang_guard import sanitize_json_values

            def _hit(key, raw):
                logger.warning("llm_json_call: 字段 {} 含英文散文 · 已净化 · raw={}",
                               key, raw[:120])

            parsed = sanitize_json_values(parsed, on_hit=_hit)
        except Exception as e:  # noqa: BLE001
            logger.warning("llm_json_call: 语言守卫失败 · 原样透传: {}", e)

    try:
        from app.services import llm_trace
        llm_trace.record_call(
            caller="online_analysis.llm_json_call",
            base_url=base_url,
            req_model=use_model,
            resp_model=str(meta.get("resp_model") or ""),
            status=200 if not meta.get("error") else 422,
            latency_ms=int(meta.get("latency_ms") or 0),
            tokens_in=int(meta.get("tokens_in") or 0),
            tokens_out=int(meta.get("tokens_out") or 0),
            error=str(meta.get("error") or ""),
        )
    except Exception:  # noqa: BLE001
        pass

    return parsed, meta


# ─── 成本估算（粗略，按 oneapi 计费 0.1 元 / 1k tokens 估）──────────────
_COST_PER_1K_TOKENS_CNY = 0.05


def estimate_cost_cny(tokens_in: int, tokens_out: int) -> float:
    """粗略估算单次调用人民币成本"""
    total = tokens_in + tokens_out
    return round(total / 1000 * _COST_PER_1K_TOKENS_CNY, 4)
