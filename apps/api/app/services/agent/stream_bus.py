"""SSE 事件流内部总线。

在 orchestrator 里：
  - `bus.emit(name, data)` 生成 SSEEvent（同步）
  - `bus.emit_progress(...)` sub-agent 内部发进度事件（异步入队）
  - `bus.multiplex([gen1, gen2, ...])` 把多个 async generator 合流成一个事件流
"""
from __future__ import annotations
import asyncio
import json
from dataclasses import dataclass
from typing import AsyncGenerator, Iterable


@dataclass
class SSEEvent:
    name: str
    data: dict

    def encode(self) -> bytes:
        payload = json.dumps(self.data, ensure_ascii=False, default=str)
        return f"event: {self.name}\ndata: {payload}\n\n".encode("utf-8")


class StreamBus:
    """并行 asyncgen 合流工具。

    进度事件 (`tool_progress`) 通常在 sub-agent 内部产生，需要独立通道；
    普通事件由 orchestrator 直接 yield。此类主要给 sub-agent 用 emit_progress
    发进度，同时把 gather 的结果统一转发到 orchestrator 主 stream。
    """

    def __init__(self):
        self._q: asyncio.Queue = asyncio.Queue()

    def emit(self, name: str, data: dict) -> SSEEvent:
        """同步创建一个 SSEEvent 供 orchestrator 直接 yield 出去"""
        return SSEEvent(name=name, data=data)

    async def emit_progress(self, tool_id: str, name: str, percent: int,
                             detail: str = "", phase: str | None = None) -> None:
        """sub-agent 内部调用，异步入队进度事件"""
        payload = {"tool_id": tool_id, "name": name,
                    "percent": percent, "detail": detail}
        if phase is not None:
            payload["phase"] = phase
        await self._q.put(SSEEvent("tool_progress", payload))

    async def multiplex(self, generators: Iterable) -> AsyncGenerator[SSEEvent, None]:
        """把多个 async generator 合流为一个事件流。

        每个 generator 结束时自动放一个 sentinel(None)，全部结束后 multiplex 退出。
        """
        gens = list(generators)
        pending = len(gens)

        async def _forward(g):
            try:
                async for evt in g:
                    await self._q.put(evt)
            finally:
                await self._q.put(None)

        tasks = [asyncio.create_task(_forward(g)) for g in gens]
        try:
            while pending > 0:
                evt = await self._q.get()
                if evt is None:
                    pending -= 1
                    continue
                yield evt
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
