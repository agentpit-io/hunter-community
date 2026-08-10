"""research sub-agent · P1 版

暂时用一个精简的 Gemini 3.5-Flash JSON 调用做"深度研究"（不重复现有
research_assistant.chat 的多轮/session 逻辑，那些留给 orchestrator）。

产出的 summary 供主 agent 汇总，schema 严格符合 03 §4.3。
"""
from __future__ import annotations
import json
import os
import time
from typing import Optional
from loguru import logger

from app.services.agent.tool_registry import ToolCall, ToolRegistry, ToolResult
from app.services.online_analysis.llm_client import get_client


_MODEL = os.getenv("AGENT_SUB_RESEARCH_MODEL", "gemini-3-flash-preview")

_SYSTEM = """你是猎鹿人 Hunter 的"深度研究"专家。给定一只股票和用户问题，输出结构化 JSON：
{
  "conclusion": "一句话结论：谨慎持有 / 建议加仓 / 建议观望 / 不建议",
  "confidence": 0-1 的浮点，
  "key_points": [3-5 条要点，每条 1 句话，覆盖基本面/技术面/估值],
  "structured_view": {
    "fundamentals": "1-2 句基本面判断",
    "valuation": "1-2 句估值判断",
    "technicals": "1-2 句技术面判断",
    "risks": "1-2 句主要风险"
  }
}
严格要求：只输出 JSON，第一个字符必须是 {，最后一个必须是 }。不要 markdown 包装。"""


_DEF = {
    "name": "research",
    "description": "深度研究：投前认知建立。给定股票代码和用户问题，返回结构化多维分析（基本面/技术面/估值/风险）。3 分钟阅读。",
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "A 股 6 位代码或港股 5 位代码"},
            "question": {"type": "string", "description": "用户原始问题（可选）"},
        },
        "required": ["code"],
    },
}


async def invoke_research(code: str, question: str = "") -> tuple[dict, Optional[dict]]:
    """独立可复用函数：供 orchestrator/其他 sub-agent 直接调"""
    client = get_client()
    if client is None:
        # LLM 不可用，返回极简 fallback
        return {
            "conclusion": "无法完成深度研究",
            "confidence": 0.0,
            "key_points": ["LLM 网关不可用"],
            "structured_view": {"fundamentals": "", "valuation": "",
                                 "technicals": "", "risks": "LLM 不可用"},
        }, None

    user = f"股票代码：{code}\n用户问题：{question or '综合看一下'}"
    try:
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.3, max_tokens=1500,
        )
        raw = resp.choices[0].message.content or ""
    except Exception as e:
        logger.warning("[research_agent] LLM 调用失败: {}", e)
        raise

    try:
        parsed = json.loads(raw)
    except Exception:
        # 二次 fallback：宽松提取 { ... }
        import re
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            parsed = json.loads(m.group(0))
        else:
            raise ValueError(f"research LLM 输出非 JSON: {raw[:200]}")

    # 规整字段（防御 LLM 少字段）
    summary = {
        "conclusion": str(parsed.get("conclusion", "")).strip(),
        "confidence": float(parsed.get("confidence", 0.0) or 0.0),
        "key_points": list(parsed.get("key_points", []))[:5],
        "structured_view": parsed.get("structured_view", {}),
    }
    return summary, None  # P1 detail_ref = None（尚未落库详情报告）


@ToolRegistry.register("research", definition=_DEF, timeout=30)
async def _research_tool(tc: ToolCall, bus) -> ToolResult:
    t0 = time.time()
    code = tc.args.get("code")
    if not code:
        return ToolResult.error_of(tc, "BAD_ARGS", "缺少 code")
    question = tc.args.get("question", "") or ""
    try:
        summary, detail = await invoke_research(str(code), question)
    except Exception as e:
        return ToolResult.error_of(
            tc, "INTERNAL", f"research 失败: {type(e).__name__}: {e}",
            duration_ms=int((time.time() - t0) * 1000),
        )
    return ToolResult(
        tool_call=tc, status="ok",
        duration_ms=int((time.time() - t0) * 1000),
        summary=summary, detail_ref=detail,
    )
