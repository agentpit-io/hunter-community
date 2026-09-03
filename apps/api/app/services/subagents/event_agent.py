"""event_interpret sub-agent · 事件解读

支持两种调用形态：
  1. 用户描述了具体 event_text（如"美联储加息 50bp"）→ LLM 直接分析对该股影响
  2. 用户只给 code 让"最近有什么事件影响我"→ 内部走 Sentinel unified_fetcher 抓 48h
     新闻 → 五层防御过滤 → 返回 top 事件 + LLM 影响解读
"""
from __future__ import annotations
import json
import os
import time
from typing import Optional
from loguru import logger

from app.services.agent.tool_registry import ToolCall, ToolRegistry, ToolResult
from app.services.online_analysis.llm_client import get_client


_DEF = {
    "name": "event_interpret",
    "description": "事件解读：分析一条具体新闻/公告对该股的影响，或汇总近期热点事件的影响。适合问「XX 事件对该股影响 / 最近有啥利好利空」。",
    "parameters": {
        "type": "object",
        "properties": {
            "code":       {"type": "string"},
            "stock_name": {"type": "string"},
            "event_text": {"type": "string", "description": "用户描述的具体事件（可空，为空时走近期新闻）"},
        },
        "required": ["code"],
    },
}


_MODEL = os.getenv("AGENT_SUB_EVENT_MODEL", "gemini-3.5-flash")

_EVENT_LLM_SYSTEM = """你是猎鹿人事件影响分析师。给你一条事件 + 一只股票，输出 JSON:
{
  "opinion": "1-3 句结论：利好/利空/中性 + 原因",
  "impact":  "positive|negative|neutral",
  "confidence": 0-1,
  "reasoning": "2-4 条要点，每条 1 句"
}
只输出 JSON，第一个字符是 {。"""


async def invoke_event_interpret(code: str, stock_name: str = "",
                                    event_text: str = "") -> tuple[dict, Optional[dict]]:
    """
    Returns:
        summary = {opinion, impact, confidence, reasoning, source_events}
    """
    # 若用户没给具体事件，先抓近期新闻
    source_events: list[dict] = []
    if not event_text:
        source_events = await _fetch_recent_events(code, stock_name)
        if source_events:
            snippet = "\n".join(
                f"- {e.get('title', '')}（{e.get('source', '')}）"
                for e in source_events[:5]
            )
            event_text = f"最近 48 小时关键事件：\n{snippet}"

    if not event_text:
        return {
            "opinion":    f"近期未捕获影响 {stock_name or code} 的显著事件。",
            "impact":     "neutral", "confidence": 0.3,
            "reasoning":  ["无高置信度事件源"], "source_events": [],
        }, None

    client = get_client()
    if client is None:
        return {"opinion": "LLM 不可用，仅提供原始事件列表",
                "impact": "neutral", "confidence": 0.0,
                "reasoning": [], "source_events": source_events}, None

    user = (f"股票：{stock_name or code}（{code}）\n\n"
             f"事件描述：\n{event_text}")
    try:
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _EVENT_LLM_SYSTEM},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.3, max_tokens=800,
        )
        raw = resp.choices[0].message.content or ""
        parsed = json.loads(raw)
    except Exception as e:
        logger.warning("[event_agent] LLM 失败: {}", e)
        return {"opinion": f"事件解读失败：{e}", "impact": "neutral",
                "confidence": 0.0, "reasoning": [],
                "source_events": source_events}, None

    return {
        "opinion":       str(parsed.get("opinion", ""))[:400],
        "impact":        parsed.get("impact", "neutral"),
        "confidence":    float(parsed.get("confidence", 0.5) or 0.5),
        "reasoning":     list(parsed.get("reasoning", []))[:4],
        "source_events": source_events[:5],
    }, None


async def _fetch_recent_events(code: str, stock_name: str) -> list[dict]:
    """内部：抓 48h 新闻，摘取 title/source/date"""
    try:
        from agents.sentinel.unified_fetcher import UnifiedFetcher
    except Exception:
        return []
    try:
        fr = await UnifiedFetcher().fetch_all(code, hours=48, stock_name=stock_name or code)
        items = getattr(fr, "news_items", []) or []
        return [{
            "title":  getattr(n, "title", "")[:150],
            "source": getattr(n, "source_name", ""),
            "date":   str(getattr(n, "publish_date", "") or ""),
        } for n in items[:8]]
    except Exception as e:
        logger.warning("[event_agent] 抓新闻失败 code={}: {}", code, e)
        return []


@ToolRegistry.register("event_interpret", definition=_DEF, timeout=35)
async def _event_tool(tc: ToolCall, bus) -> ToolResult:
    t0 = time.time()
    code = tc.args.get("code")
    if not code:
        return ToolResult.error_of(tc, "BAD_ARGS", "缺少 code")
    try:
        summary, detail = await invoke_event_interpret(
            str(code),
            stock_name=tc.args.get("stock_name", "") or "",
            event_text=tc.args.get("event_text", "") or "",
        )
    except Exception as e:
        return ToolResult.error_of(
            tc, "INTERNAL", f"event_interpret 失败: {type(e).__name__}: {e}",
            duration_ms=int((time.time() - t0) * 1000),
        )
    return ToolResult(
        tool_call=tc, status="ok",
        duration_ms=int((time.time() - t0) * 1000),
        summary=summary, detail_ref=detail,
    )
