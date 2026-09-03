"""看好理由（Thesis）AI 生成 — 用户选好股票后点「AI 生成」一键产生

直接走纯文本 LLM 调用（不用 json_object 模式），避免 Gemini 把段落包成 JSON 数组
导致解析时被截断的问题。
"""
import os
import time

from loguru import logger
from openai import OpenAI

from .prompts import THESIS_GEN_SYSTEM, build_thesis_user_prompt


# 与 sentinel/llm_client.py 保持一致 · ONE_API_* 缺席时回退 LLM_* 三件套。
_BASE_URL = os.getenv("ONE_API_BASE_URL") or os.getenv("LLM_BASE_URL", "http://104.197.139.51:3000/v1")
_API_KEY  = os.getenv("ONE_API_KEY")     or os.getenv("LLM_API_KEY", "")
_MODEL    = os.getenv("ONE_API_MODEL")   or os.getenv("LLM_DEFAULT_MODEL", "gemini-3.8-flash")
_TIMEOUT  = 120


async def generate_thesis(stock_code: str, stock_name: str) -> dict:
    """根据股票生成看好理由

    Returns:
        { "thesis": "中文段落", "llm_meta": {...}, "error": str | None }
    """
    if not stock_name.strip():
        return {"thesis": "", "llm_meta": {}, "error": "stock_name_empty"}

    if not _API_KEY:
        return {"thesis": "", "llm_meta": {}, "error": "no_api_key"}

    user_prompt = build_thesis_user_prompt(stock_name, stock_code)
    client = OpenAI(api_key=_API_KEY, base_url=_BASE_URL, timeout=_TIMEOUT)

    t0 = time.time()
    try:
        # 直接纯文本调用，不用 response_format=json_object
        # max_tokens 3000：中文段落 800 字约需 1800-2400 token，3000 留余量防截断
        resp = client.chat.completions.create(
            model      = _MODEL,
            messages   = [
                {"role": "system", "content": THESIS_GEN_SYSTEM},
                {"role": "user",   "content": user_prompt},
            ],
            max_tokens = 3000,
            temperature= 0.6,
            tools      = [{"type": "google_search"}],
        )
    except Exception as e:
        logger.warning("thesis_gen LLM call failed: {}", e)
        return {"thesis": "", "llm_meta": {"error": str(e)}, "error": str(e)}

    raw = (resp.choices[0].message.content or "").strip()
    finish_reason = resp.choices[0].finish_reason if resp.choices else "unknown"
    usage = resp.usage
    meta = {
        "model":      _MODEL,
        "tokens_in":  usage.prompt_tokens if usage else 0,
        "tokens_out": usage.completion_tokens if usage else 0,
        "latency_ms": int((time.time() - t0) * 1000),
        "raw_text":   raw,
        "finish_reason": finish_reason,
        "error":      None,
    }
    # 如果被 max_tokens 截断，记录警告 — 后面正则要兜底允许无句尾收尾
    if finish_reason == "length":
        logger.warning("thesis_gen {} {}: LLM 输出被 max_tokens 截断（tokens_out={}）",
                       stock_code, stock_name, meta["tokens_out"])

    # 清理可能的 markdown 包装
    if raw.startswith("```"):
        import re
        raw = re.sub(r"^```(?:[\w]*)?\s*", "", raw).rstrip("`").strip()

    # 处理 Gemini 偶尔仍输出 JSON 数组的情况（不应该但兜底）
    if raw.startswith("[") or raw.startswith("{"):
        import json as _json
        try:
            o = _json.loads(raw)
            if isinstance(o, list) and o and isinstance(o[0], str):
                raw = o[0]
            elif isinstance(o, dict):
                for v in o.values():
                    if isinstance(v, str) and len(v) > 20:
                        raw = v; break
        except _json.JSONDecodeError:
            # 完整 JSON 截断 — 用引号配对兜底抓最长字符串
            import re
            matches = re.findall(r'"([^"\\]{15,})"', raw)
            if matches:
                raw = max(matches, key=len)
            else:
                # 实在抓不到，剥离 [/{/" 前缀
                raw = raw.lstrip('["{ \n').rstrip(']"}\n ').strip()

    # 清理"看好理由："等前缀
    for prefix in ["看好理由：", "看好理由:", "理由：", "理由:", "Thesis:"]:
        if raw.startswith(prefix):
            raw = raw[len(prefix):].strip()

    # 剥离 LLM 的英文/markdown 思考过程，提取首段连续中文
    # 不要求句尾收尾（避免 max_tokens 截断时整段被丢弃）— 允许末尾任意字符
    import re
    chinese_paragraphs = re.findall(
        r'[一-龥][一-龥，。、；：！？「」（）()\-+0-9%a-zA-Z· ]{30,}',
        raw
    )
    if chinese_paragraphs:
        # 取最长的中文段落（最可能是正文）
        raw = max(chinese_paragraphs, key=len).strip()
        # 如果末尾不是句号/感叹号/问号 → 说明被截断，补省略号提示
        if raw and raw[-1] not in "。！？.!?）)":
            raw = raw.rstrip("，、；：,;:") + "…"

    if not raw:
        return {"thesis": "", "llm_meta": meta, "error": "empty_output"}

    raw = raw[:500]
    logger.info("thesis_gen {} {}: {} chars, tokens={}",
                stock_code, stock_name, len(raw), meta["tokens_out"])
    return {"thesis": raw, "llm_meta": meta, "error": None}
