"""Worker 与 Temporal 的连接状态：进程级共享的一个布尔量，给 `/healthz` 读。

HTTP 跑在 `main.py` 起的 daemon 线程（uvicorn 自己的 event loop），Worker 跑在主线程的
`asyncio.run`（另一个 loop）。Temporal 客户端有 loop 亲和性，不能跨线程共享对象，
所以这里只共享「Worker 已连上」这一个事实：Worker 侧置位 / 清除，`healthz` 只读。
"""

from __future__ import annotations

import threading

_connected = threading.Event()


def mark_connected() -> None:
    _connected.set()


def mark_disconnected() -> None:
    _connected.clear()


def is_connected() -> bool:
    return _connected.is_set()
