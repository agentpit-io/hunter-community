"""tool_registry 单测：注册 / dispatch / 超时 / 未知工具"""
import asyncio
import pytest

from app.services.agent.tool_registry import (
    ToolCall, ToolResult, ToolRegistry, new_tool_call,
)


@pytest.fixture(autouse=True)
def _clear_registry_before_each():
    ToolRegistry.clear()
    yield
    ToolRegistry.clear()


@pytest.mark.asyncio
async def test_register_and_dispatch_ok():
    @ToolRegistry.register(
        "echo",
        definition={"name": "echo", "description": "回声",
                    "parameters": {"type": "object", "properties": {}}},
        timeout=2,
    )
    async def _echo(tc, bus):
        return ToolResult(tool_call=tc, status="ok",
                          duration_ms=1, summary={"got": tc.args})

    tc = new_tool_call("echo", {"foo": "bar"})
    result = await ToolRegistry.dispatch(tc, bus=None)
    assert result.status == "ok"
    assert result.summary == {"got": {"foo": "bar"}}
    assert result.to_dict()["status"] == "ok"


@pytest.mark.asyncio
async def test_dispatch_unknown_tool():
    tc = new_tool_call("nonexistent", {})
    result = await ToolRegistry.dispatch(tc, bus=None)
    assert result.status == "error"
    assert result.error["code"] == "UNKNOWN_TOOL"


@pytest.mark.asyncio
async def test_dispatch_timeout():
    @ToolRegistry.register(
        "slow", definition={"name": "slow", "description": "",
                             "parameters": {"type": "object", "properties": {}}},
        timeout=1,
    )
    async def _slow(tc, bus):
        await asyncio.sleep(5)
        return ToolResult(tool_call=tc, status="ok")

    tc = new_tool_call("slow", {})
    result = await ToolRegistry.dispatch(tc, bus=None)
    assert result.status == "error"
    assert result.error["code"] == "UPSTREAM_TIMEOUT"


@pytest.mark.asyncio
async def test_dispatch_internal_error():
    @ToolRegistry.register(
        "boom",
        definition={"name": "boom", "description": "",
                    "parameters": {"type": "object", "properties": {}}},
        timeout=2,
    )
    async def _boom(tc, bus):
        raise RuntimeError("intentional boom")

    tc = new_tool_call("boom", {})
    result = await ToolRegistry.dispatch(tc, bus=None)
    assert result.status == "error"
    assert result.error["code"] == "INTERNAL"
    assert "boom" in result.error["message"].lower()


def test_openai_tool_defs_shape():
    @ToolRegistry.register(
        "sample",
        definition={"name": "sample", "description": "d",
                    "parameters": {"type": "object", "properties": {}}},
    )
    async def _s(tc, bus):
        return ToolResult(tool_call=tc, status="ok")

    defs = ToolRegistry.openai_tool_defs()
    assert len(defs) == 1
    assert defs[0]["type"] == "function"
    assert defs[0]["function"]["name"] == "sample"


def test_new_tool_call_injects_ctx_code():
    tc = new_tool_call("research", args={"question": "怎样"}, ctx_code="600519")
    assert tc.args["code"] == "600519"
    assert tc.args["question"] == "怎样"

    # 已有 code 时不覆盖
    tc2 = new_tool_call("research", args={"code": "000001", "question": "x"},
                        ctx_code="600519")
    assert tc2.args["code"] == "000001"
