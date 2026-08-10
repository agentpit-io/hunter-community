"""Orchestrator 契约测试 · 事件序列/顺序保证

通过 mock ToolRegistry + LLM decision，验证 ChatOrchestrator.run()
的事件流严格符合 doc/codex/03 §3.2 契约。

不依赖真 LLM / 真 sub-agent，纯单进程内。

Note: orchestrator 通过 llm_client → online_analysis.source_registry
链路会用到 py 3.10+ 的 `X | None` 语法；py 3.9 环境自动 skip。
生产环境（py ≥ 3.10）正常跑。
"""
from __future__ import annotations
import asyncio
import json
import sys
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="orchestrator 依赖 online_analysis 使用 py3.10+ 语法（生产环境已满足）",
)

from app.services.agent.tool_registry import (
    ToolCall, ToolResult, ToolRegistry, new_tool_call,
)


# ────────────────────────── helpers ──────────────────────────
def _register_stub(name: str, delay: float = 0.01, fail: bool = False):
    @ToolRegistry.register(
        name, definition={"name": name, "description": name,
                          "parameters": {"type": "object", "properties": {}}},
        timeout=5,
    )
    async def _stub(tc: ToolCall, bus):
        await asyncio.sleep(delay)
        if fail:
            raise RuntimeError("stub-fail")
        return ToolResult(tool_call=tc, status="ok",
                          duration_ms=int(delay * 1000),
                          summary={"stubbed": True, "name": name})


def _fake_openai_msg(tool_calls):
    """构造一个 openai completion 返回对象（只带 tool_calls / usage）"""
    m = MagicMock()
    m.choices = [MagicMock()]
    m.choices[0].message.content = "分派中"
    m.choices[0].message.tool_calls = tool_calls or None
    m.usage = MagicMock(prompt_tokens=100, completion_tokens=50)
    return m


def _fake_tc(name, args=None, tid=None):
    """构造一个模仿 openai tool_call 对象"""
    tc = MagicMock()
    tc.id = tid or f"t-{name}"
    tc.function.name = name
    tc.function.arguments = json.dumps(args or {})
    return tc


class _FakeStreamChunk:
    def __init__(self, content: Optional[str]):
        self.choices = [MagicMock()]
        self.choices[0].delta.content = content
        self.usage = None


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks
    def __iter__(self):
        return iter(self._chunks)


# ────────────────────────── fixtures ──────────────────────────
@pytest.fixture
def clean_registry():
    ToolRegistry.clear()
    yield
    ToolRegistry.clear()


# ────────────────────────── tests ──────────────────────────
@pytest.mark.asyncio
async def test_zero_tool_path_produces_session_and_end(clean_registry):
    """无 tool_call → 应发出 session + router_decision + message_delta* + message_end"""
    from app.services.agent import orchestrator as orch_mod

    _register_stub("research")

    fake_client = MagicMock()
    # 决策：无 tool_calls
    fake_client.chat.completions.create.side_effect = [
        _fake_openai_msg(tool_calls=None),
        _FakeStream([_FakeStreamChunk("你好"), _FakeStreamChunk("世界"), _FakeStreamChunk(None)]),
    ]

    with patch.object(orch_mod, "get_client", return_value=fake_client), \
         patch.object(orch_mod.ChatOrchestrator, "_persist", new=lambda self, q: _noop()):
        oc = orch_mod.ChatOrchestrator(user_id="u", session_id="s", stock_code="600519")
        events = [e async for e in oc.run("你好", [])]

    names = [e.name for e in events]
    assert names[0] == "session"
    assert "router_decision" in names
    assert names[-1] == "message_end"
    # 没有 tool_use / tool_result
    assert "tool_use" not in names
    assert "tool_result" not in names


@pytest.mark.asyncio
async def test_three_parallel_tools_events_shape(clean_registry):
    """3 个并行 tool_call → 3 组 tool_use + 3 组 tool_result 都能收到"""
    from app.services.agent import orchestrator as orch_mod

    _register_stub("research", delay=0.02)
    _register_stub("scout", delay=0.01)
    _register_stub("quant_predict", delay=0.03)

    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = [
        _fake_openai_msg(tool_calls=[
            _fake_tc("research", {"code": "600519"}, tid="t1"),
            _fake_tc("scout", {"code": "600519"}, tid="t2"),
            _fake_tc("quant_predict", {"code": "600519", "horizon": 10}, tid="t3"),
        ]),
        _FakeStream([_FakeStreamChunk("汇总"), _FakeStreamChunk(None)]),
    ]

    with patch.object(orch_mod, "get_client", return_value=fake_client), \
         patch.object(orch_mod.ChatOrchestrator, "_persist", new=lambda self, q: _noop()):
        oc = orch_mod.ChatOrchestrator(user_id="u", session_id="s", stock_code="600519")
        events = [e async for e in oc.run("茅台能买吗", [])]

    use_ids  = [e.data["tool_id"] for e in events if e.name == "tool_use"]
    res_ids  = [e.data["tool_id"] for e in events if e.name == "tool_result"]
    assert set(use_ids) == {"t1", "t2", "t3"}
    assert set(res_ids) == {"t1", "t2", "t3"}
    assert events[0].name == "session"
    assert events[-1].name == "message_end"


@pytest.mark.asyncio
async def test_tool_error_propagates_as_tool_result(clean_registry):
    """一个 sub-agent 抛异常 → 收到 tool_result{status: error}，整体不失败"""
    from app.services.agent import orchestrator as orch_mod

    _register_stub("research", delay=0.01)
    _register_stub("quant_predict", delay=0.01, fail=True)

    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = [
        _fake_openai_msg(tool_calls=[
            _fake_tc("research", {"code": "600519"}, tid="t1"),
            _fake_tc("quant_predict", {"code": "600519"}, tid="t2"),
        ]),
        _FakeStream([_FakeStreamChunk("综合"), _FakeStreamChunk(None)]),
    ]

    with patch.object(orch_mod, "get_client", return_value=fake_client), \
         patch.object(orch_mod.ChatOrchestrator, "_persist", new=lambda self, q: _noop()):
        oc = orch_mod.ChatOrchestrator(user_id="u", session_id="s", stock_code="600519")
        events = [e async for e in oc.run("能买吗", [])]

    results = {e.data["tool_id"]: e.data for e in events if e.name == "tool_result"}
    assert results["t1"]["status"] == "ok"
    assert results["t2"]["status"] == "error"
    assert results["t2"]["error"]["code"] == "INTERNAL"
    # 整体仍 message_end
    assert events[-1].name == "message_end"


@pytest.mark.asyncio
async def test_fallback_when_decision_raises(clean_registry):
    """LLM function_call 抛异常 → 进入 fallback，发 error + router_decision + tool_use/result"""
    from app.services.agent import orchestrator as orch_mod

    _register_stub("research", delay=0.01)

    fake_client = MagicMock()
    # 第一次调用（decision）抛异常
    fake_client.chat.completions.create.side_effect = RuntimeError("gemini 挂了")

    with patch.object(orch_mod, "get_client", return_value=fake_client), \
         patch.object(orch_mod.ChatOrchestrator, "_persist", new=lambda self, q: _noop()):
        oc = orch_mod.ChatOrchestrator(user_id="u", session_id="s", stock_code="600519")
        events = [e async for e in oc.run("茅台怎么样", [])]

    names = [e.name for e in events]
    assert names[0] == "session"
    # fallback 首先发 error
    assert "error" in names
    err_evt = next(e for e in events if e.name == "error")
    assert err_evt.data["code"] == "LLM_FAILED"
    assert err_evt.data.get("fallback") == "keyword_route"
    # 然后是 router_decision + tool_use + tool_result + message_end
    assert names[-1] == "message_end"


# ────────────────────────── helpers ──────────────────────────
async def _noop():
    return None
