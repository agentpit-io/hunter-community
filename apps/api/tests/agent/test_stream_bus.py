"""stream_bus 单测：multiplex 合流顺序 + sentinel 终结"""
import asyncio
import pytest

from app.services.agent.stream_bus import SSEEvent, StreamBus


@pytest.mark.asyncio
async def test_emit_encodes_sse_format():
    bus = StreamBus()
    evt = bus.emit("tool_use", {"tool_id": "t1", "name": "x"})
    encoded = evt.encode().decode("utf-8")
    assert encoded.startswith("event: tool_use\n")
    assert 'data: {"tool_id": "t1"' in encoded
    assert encoded.endswith("\n\n")


@pytest.mark.asyncio
async def test_multiplex_all_generators_yield():
    bus = StreamBus()

    async def gen(name, count):
        for i in range(count):
            yield SSEEvent("tool_use", {"tool_id": name, "seq": i})

    events = [evt async for evt in bus.multiplex([gen("a", 3), gen("b", 2)])]
    tool_ids = [e.data["tool_id"] for e in events]
    assert tool_ids.count("a") == 3
    assert tool_ids.count("b") == 2


@pytest.mark.asyncio
async def test_multiplex_empty_generator_terminates():
    bus = StreamBus()

    async def gen():
        if False:
            yield None

    events = [evt async for evt in bus.multiplex([gen(), gen()])]
    assert events == []


@pytest.mark.asyncio
async def test_emit_progress_async():
    bus = StreamBus()

    async def sub_agent():
        await bus.emit_progress("t1", "hold_judge", 50, "debate round 1")
        # 也发一个自己的完成事件
        yield SSEEvent("tool_result", {"tool_id": "t1"})

    # multiplex 只 forward gen yield 出的事件；emit_progress 走的是 bus 内部队列
    # 但 multiplex 的 forward loop 里也会取到 emit_progress 塞进 bus 的事件
    events = [evt async for evt in bus.multiplex([sub_agent()])]
    names = [e.name for e in events]
    # tool_progress 由 emit_progress 塞进 bus._q，也会被 multiplex loop 取到
    assert "tool_progress" in names
    assert "tool_result" in names
