"""hold_judge sub-agent · 持仓研判（多空辩论）

包装 agents.graph.run_price_alert_graph。因该流程 15-40s，本 sub-agent
额外起一个后台任务定时通过 bus emit 25/50/75/100 假进度，避免用户以为卡住。
"""
from __future__ import annotations
import asyncio
import time
from typing import Optional
from loguru import logger

from app.services.agent.tool_registry import ToolCall, ToolRegistry, ToolResult


_DEF = {
    "name": "hold_judge",
    "description": "持仓研判：多空辩论 + 综合裁判 + 风险裁判 → BUY/HOLD/SELL 决策。适合用户问「还该拿吗 / 要不要撤 / 止损」等持仓类问题。",
    "parameters": {
        "type": "object",
        "properties": {
            "code":       {"type": "string", "description": "6 位 A 股代码"},
            "stock_name": {"type": "string"},
            "cost_price": {"type": "number", "description": "持仓成本价（可选）"},
            "thesis":     {"type": "string", "description": "持仓逻辑（可选，无则空串）"},
        },
        "required": ["code"],
    },
}


# 假进度节拍：0s -> 15, 8s -> 40, 20s -> 70, 40s -> 90（100 由真实完成时发）
_PROGRESS_STEPS = [
    (0.5,  15, "启动分析…"),
    (8.0,  40, "多空辩论第 1 轮…"),
    (20.0, 70, "综合裁判中…"),
    (40.0, 90, "风险修正中…"),
]


async def _emit_fake_progress(bus, tool_id: str):
    """后台任务：按节拍发 tool_progress"""
    for delay, pct, detail in _PROGRESS_STEPS:
        try:
            await asyncio.sleep(delay if delay > 0 else 0.1)
            await bus.emit_progress(tool_id, "hold_judge", pct, detail)
        except asyncio.CancelledError:
            return


async def invoke_hold_judge(code: str, stock_name: str = "",
                             cost_price: float | None = None,
                             thesis: str = "") -> tuple[dict, Optional[dict]]:
    """
    Returns:
        summary = {
          "decision": "BUY|HOLD|SELL",
          "confidence": float,
          "key_reason": str,
          "stop_loss": float | None,
          "cost_price": float | None,
        }
        detail_ref = {"type": "hold_report", "code": code}
    """
    try:
        from agents.graph import run_price_alert_graph
    except Exception as e:
        logger.warning("[hold_agent] graph 导入失败: {}", e)
        return _degraded(code, "多空辩论模块不可用"), None

    trigger_desc = f"用户主动询问持仓（成本 {cost_price}）" if cost_price else "用户主动询问持仓"
    try:
        result = await run_price_alert_graph(
            ticker=code,
            stock_name=stock_name or code,
            change_pct=0.0,
            trigger_desc=trigger_desc,
            thesis_text=thesis or "",
            kill_conditions=[],
            current_price=cost_price,
        )
    except Exception as e:
        logger.warning("[hold_agent] 多空辩论失败 code={}: {}", code, e)
        return _degraded(code, f"辩论失败: {e}"), None

    # result 结构由 comprehensive_judge/risk_judge 返回，取常见字段
    decision   = str(result.get("final_decision", result.get("decision", "HOLD"))).upper()
    confidence = float(result.get("final_confidence",
                                    result.get("confidence", 0.5)) or 0.5)
    key_reason = result.get("final_reasoning",
                             result.get("reasoning", ""))[:400]
    stop_loss  = result.get("stop_loss_price") or result.get("suggested_stop")

    summary = {
        "decision":   decision,
        "confidence": confidence,
        "key_reason": key_reason,
        "stop_loss":  float(stop_loss) if stop_loss else None,
        "cost_price": cost_price,
    }
    detail = {"type": "hold_report", "code": code}
    return summary, detail


def _degraded(code: str, reason: str) -> dict:
    return {"decision": "HOLD", "confidence": 0.0,
            "key_reason": f"多空辩论不可用（{reason}）", "stop_loss": None,
            "cost_price": None, "note": reason}


@ToolRegistry.register("hold_judge", definition=_DEF, timeout=60)
async def _hold_tool(tc: ToolCall, bus) -> ToolResult:
    t0 = time.time()
    code = tc.args.get("code")
    if not code:
        return ToolResult.error_of(tc, "BAD_ARGS", "缺少 code")

    # 起假进度背景任务
    prog_task = asyncio.create_task(_emit_fake_progress(bus, tc.tool_id))

    try:
        summary, detail = await invoke_hold_judge(
            str(code),
            stock_name=tc.args.get("stock_name", "") or "",
            cost_price=tc.args.get("cost_price"),
            thesis=tc.args.get("thesis", "") or "",
        )
        # 真实完成，取消假进度并发 100
        prog_task.cancel()
        try:
            await bus.emit_progress(tc.tool_id, "hold_judge", 100, "完成")
        except Exception:
            pass
        return ToolResult(
            tool_call=tc, status="ok",
            duration_ms=int((time.time() - t0) * 1000),
            summary=summary, detail_ref=detail,
        )
    except Exception as e:
        prog_task.cancel()
        return ToolResult.error_of(
            tc, "INTERNAL", f"hold_judge 失败: {type(e).__name__}: {e}",
            duration_ms=int((time.time() - t0) * 1000),
        )
