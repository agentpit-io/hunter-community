"""fin-worker 进程入口：**内部 HTTP（线程） + Temporal Worker（主线程）**。

为什么合在一个进程里：fin-worker 的职责就是「Runtime Bridge + Workers」
（`08 §3.3`），HTTP 只用来接 `api` 的触发，量极小；拆成两个容器会让
「谁能触发工作流」多一个部署面，而收益为零。

- HTTP 线程：`uvicorn app.api:app`，绑 `0.0.0.0`（宿主机只映射 127.0.0.1）。
- 主线程：asyncio 跑 Temporal Worker，连不上 Temporal 时退避重试（见 `worker.py`）。
"""

from __future__ import annotations

import asyncio
import threading

import uvicorn
from loguru import logger

from app import config


def _serve_http() -> None:
    uvicorn.run(
        "app.api:app",
        host="0.0.0.0",
        port=config.http_port(),
        log_level="info",
    )


def main() -> None:
    logger.info("[fin-worker] 启动 · paper={} api={} temporal={}",
                config.paper_base_url(), config.api_base_url(), config.temporal_address())
    if not config.internal_key():
        # 不退出：健康检查与只读端点照常；但触发端点会一律 401（缺口令 = 没人能过）。
        logger.warning("[fin-worker] HUNTER_INTERNAL_KEY 未设置 —— /internal/* 将一律 401")

    http_thread = threading.Thread(target=_serve_http, name="fin-worker-http", daemon=True)
    http_thread.start()

    from app.worker import run_worker_forever

    try:
        asyncio.run(run_worker_forever())
    except KeyboardInterrupt:
        logger.info("[fin-worker] 收到中断，退出")


if __name__ == "__main__":
    main()
