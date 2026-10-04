"""控制类 MCP（`scripts/opencode-mcp/runtime_mcp.py`）的**无依赖**回归。

它平时跑在 opencode 容器里（有 `mcp` 包）；本地 venv 没有，所以这里**注入一个桩 `mcp`**
再 import 那个脚本。测的是**跨层一致性**（最能漂的东西）：

1. 四个工具名 = `runtime.workflow_start/get/pause/cancel`；
2. 脚本里给模型看的模板清单 == fin-worker 的 `runtime_control.TEMPLATES`（写歪了模型会照错名试）；
3. `call_tool` 把每个工具路由到正确的 `/internal/runtime/*` 路径与方法。

（真机「用 MCP 客户端调通一个工具」的读数见成果文档；这条是防漂的常驻守门。）
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
MCP_PATH = REPO / "scripts" / "opencode-mcp" / "runtime_mcp.py"


def _install_mcp_stub():
    """把 `mcp` 相关模块替换成桩，只保留 import 时用到的符号。"""
    captured: dict = {}

    class _Server:
        def __init__(self, name):
            captured["name"] = name

        def list_tools(self):
            def deco(fn):
                captured["list_tools"] = fn
                return fn
            return deco

        def call_tool(self):
            def deco(fn):
                captured["call_tool"] = fn
                return fn
            return deco

        async def run(self, *a, **k):
            return None

        @staticmethod
        def create_initialization_options():
            return {}

    class _Tool:
        def __init__(self, name, description, inputSchema):
            self.name = name
            self.description = description
            self.inputSchema = inputSchema

    class _TextContent:
        def __init__(self, type, text):
            self.type = type
            self.text = text

    mods = {
        "mcp": types.ModuleType("mcp"),
        "mcp.server": types.ModuleType("mcp.server"),
        "mcp.server.stdio": types.ModuleType("mcp.server.stdio"),
        "mcp.types": types.ModuleType("mcp.types"),
    }
    mods["mcp.server"].Server = _Server
    mods["mcp.server.stdio"].stdio_server = lambda *a, **k: None
    mods["mcp.types"].Tool = _Tool
    mods["mcp.types"].TextContent = _TextContent
    for name, mod in mods.items():
        # 每次 _load 都**覆盖**（不能用 setdefault）—— 否则第二次拿到的是上一次的桩，
        # 新 captured 永远填不上（`call_tool` KeyError）。
        sys.modules[name] = mod
    return captured


def _load():
    captured = _install_mcp_stub()
    spec = importlib.util.spec_from_file_location("runtime_mcp_under_test", MCP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, captured


def test_four_control_tools_are_registered():
    mod, captured = _load()
    tools = asyncio.run(captured["list_tools"]())
    names = [t.name for t in tools]
    assert names == [
        "runtime_workflow_start", "runtime_workflow_get",
        "runtime_workflow_pause", "runtime_workflow_cancel",
    ]
    # start 必须声明 template 为必填（模型才知道要传模板名）
    start = next(t for t in tools if t.name == "runtime_workflow_start")
    assert "template" in start.inputSchema["required"]


def test_template_list_matches_fin_worker_whitelist():
    """脚本里给模型看的模板清单必须与 fin-worker 的最新白名单一致（防漂）。"""
    from app import runtime_control as rc

    mod, _ = _load()
    assert set(mod._TEMPLATES) == set(rc.TEMPLATES)
    assert len(mod._TEMPLATES) == 14


def test_call_tool_routes_to_right_endpoints(monkeypatch):
    mod, captured = _load()
    seen: list[tuple] = []

    async def _fake(method, path, *, params=None, body=None):
        seen.append((method, path, params, body))
        return '{"ok":true}'

    monkeypatch.setattr(mod, "_call", _fake)

    async def drive():
        await captured["call_tool"]("runtime_workflow_start",
                                    {"template": "fin.instrument_sync", "params": {"market": "us"}})
        await captured["call_tool"]("runtime_workflow_get", {"workflow_id": "w1"})
        await captured["call_tool"]("runtime_workflow_pause", {"schedule_id": "s1", "resume": True})
        await captured["call_tool"]("runtime_workflow_cancel", {"workflow_id": "w1"})
    asyncio.run(drive())

    assert seen[0][0] == "POST" and seen[0][1] == "/internal/runtime/workflow_start"
    assert seen[0][3] == {"template": "fin.instrument_sync", "params": {"market": "us"}}
    assert seen[1][0] == "GET" and seen[1][1] == "/internal/runtime/workflow_get"
    assert seen[1][2] == {"workflow_id": "w1"}
    assert seen[2][1] == "/internal/runtime/workflow_pause" and seen[2][3]["resume"] is True
    assert seen[3][1] == "/internal/runtime/workflow_cancel" and seen[3][3] == {"workflow_id": "w1"}


def test_unknown_tool_returns_structured_failure():
    mod, captured = _load()
    res = asyncio.run(captured["call_tool"]("runtime_nope", {}))
    text = res[0].text
    assert "unknown_tool" in text and "instruction" in text       # 失败对象带 type/code/instruction
