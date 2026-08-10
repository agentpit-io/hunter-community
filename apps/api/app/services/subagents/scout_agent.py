"""scout sub-agent · 一手情报

内部使用 hermes 已有的 Sentinel UnifiedFetcher + 五层防御管道。
本 sub-agent 面向助手场景，change_pct/thesis/kill 通常拿不到，
给合理默认值即可（防御管道对这些字段容错良好）。
"""
from __future__ import annotations
import time
from typing import Optional
from loguru import logger

from app.services.agent.tool_registry import ToolCall, ToolRegistry, ToolResult


_DEF = {
    "name": "scout",
    "description": "一手情报：过去 N 天该股的关键事件（机构调研 / 北向净流入 / 大单资金 / 研发扩张 / 公告 / 舆情）汇总。",
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "6 位 A 股代码"},
            "stock_name": {"type": "string"},
            "days": {"type": "integer", "default": 7, "minimum": 1, "maximum": 30},
        },
        "required": ["code"],
    },
}


async def invoke_scout(code: str, stock_name: str = "",
                        days: int = 7) -> tuple[dict, Optional[dict]]:
    """
    Returns:
        summary = {
          "events": [{"category","title","source","date"}],
          "count":   int,
          "period_days": int,
          "opinion": str,     # 综合观点（若拿到）
          "confidence": float
        }
    """
    try:
        from agents.sentinel.unified_fetcher import UnifiedFetcher
        from agents.sentinel.pipeline import run_defense_pipeline
    except Exception as e:
        logger.warning("[scout_agent] Sentinel 模块导入失败: {}", e)
        return _degraded(code, "Sentinel 依赖不可用"), None

    hours = max(24, days * 24)
    fetcher = UnifiedFetcher()
    try:
        fetch_result = await fetcher.fetch_all(code, hours=hours,
                                                  stock_name=stock_name or code)
    except Exception as e:
        logger.warning("[scout_agent] UnifiedFetcher 失败 code={}: {}", code, e)
        return _degraded(code, f"抓取失败: {e}"), None

    try:
        result = await run_defense_pipeline(
            stock_name=stock_name or code,
            change_pct=0.0,
            fetch_result=fetch_result,
            thesis_text="",
            kill_conditions=[],
        )
    except Exception as e:
        logger.warning("[scout_agent] defense_pipeline 失败 code={}: {}", code, e)
        # 若 pipeline 挂了，仍能给出原始新闻列表
        items = getattr(fetch_result, "news_items", [])
        return {
            "events": [{
                "category": getattr(n, "category", "news"),
                "title": getattr(n, "title", "")[:120],
                "source": getattr(n, "source_name", ""),
                "date":  getattr(n, "publish_date", ""),
            } for n in items[:8]],
            "count": len(items),
            "period_days": days,
            "opinion": "pipeline 异常，仅提供原始抓取",
            "confidence": 0.3,
        }, None

    conclusion = result.get("conclusion", {}) or {}
    facts_group = result.get("facts", {}) or {}
    events = []
    for fact in (facts_group.get("verified_facts") or [])[:8]:
        events.append({
            "category": fact.get("type", "verified"),
            "title": fact.get("fact", "")[:150],
            "source": fact.get("source", ""),
            "date":   fact.get("date", ""),
        })
    summary = {
        "events": events,
        "count":  len(events),
        "period_days": days,
        "opinion": conclusion.get("summary", "")[:300],
        "confidence": float(conclusion.get("confidence", 0.5) or 0.5),
    }
    return summary, None


def _degraded(code: str, reason: str) -> dict:
    return {"events": [], "count": 0, "period_days": 0,
            "opinion": f"{code} 情报不可用（{reason}）", "confidence": 0.0}


@ToolRegistry.register("scout", definition=_DEF, timeout=40)
async def _scout_tool(tc: ToolCall, bus) -> ToolResult:
    t0 = time.time()
    code = tc.args.get("code")
    if not code:
        return ToolResult.error_of(tc, "BAD_ARGS", "缺少 code")
    stock_name = tc.args.get("stock_name", "") or ""
    days = int(tc.args.get("days", 7))
    days = max(1, min(30, days))
    try:
        summary, detail = await invoke_scout(str(code), stock_name, days)
    except Exception as e:
        return ToolResult.error_of(
            tc, "INTERNAL", f"scout 失败: {type(e).__name__}: {e}",
            duration_ms=int((time.time() - t0) * 1000),
        )
    return ToolResult(
        tool_call=tc, status="ok",
        duration_ms=int((time.time() - t0) * 1000),
        summary=summary, detail_ref=detail,
    )
