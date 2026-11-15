"""进程级「Worker 已连上 Temporal」标志。

只共享一个布尔量，不共享 Temporal client 对象：client 有 event loop 亲和性，
而 HTTP 线程（uvicorn）与 Worker 主线程（asyncio.run）跑在不同的 loop 里。
`/healthz` 读它来回答「worker 连没连上」，而不是「HTTP 惰性触发路径建没建连」。
"""

from __future__ import annotations

import threading

_connected = threading.Event()


def set_connected() -> None:
    _connected.set()


def clear_connected() -> None:
    _connected.clear()


def is_connected() -> bool:
    return _connected.is_set()
