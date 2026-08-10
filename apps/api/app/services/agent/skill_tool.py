"""skill(name) MCP tool — 加载指定 SKILL.md 全文供主 agent 参考

Phase 3 启用。主 agent 在 system prompt 里看到 manifest 后，
通过 function_call skill({name}) 拉全文，塞回后续 messages。
"""
from __future__ import annotations
import time
from loguru import logger

from .tool_registry import ToolCall, ToolRegistry, ToolResult
from . import skill_loader as _sl


_DEF = {
    "name": "skill",
    "description": ("加载一份专门场景的 Skill 指令模板（如 AH 溢价套利 / 财报预期差 / "
                    "持仓 Kill 检查 等）。若当前问题符合某 Skill 的适用场景，先调它拿到"
                    "分析步骤，再按步骤调其他 tool。"),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill 名称，如 ah-arbitrage-check"},
        },
        "required": ["name"],
    },
}


@ToolRegistry.register("skill", definition=_DEF, timeout=2)
async def _skill_tool(tc: ToolCall, bus) -> ToolResult:
    t0 = time.time()
    name = str(tc.args.get("name", "")).strip()
    if not name:
        return ToolResult.error_of(tc, "BAD_ARGS", "缺少 name 参数")
    skill = _sl.get_skill(name)
    if skill is None:
        return ToolResult.error_of(
            tc, "NOT_FOUND",
            f"未找到 Skill: {name}. 可用: {', '.join(s.name for s in _sl.all_skills())[:200]}",
        )
    return ToolResult(
        tool_call=tc, status="ok",
        duration_ms=int((time.time() - t0) * 1000),
        summary={
            "name": skill.name,
            "description": skill.description,
            "instructions": skill.body,
            "path": skill.path,
        },
        detail_ref={"type": "skill", "name": skill.name},
    )
