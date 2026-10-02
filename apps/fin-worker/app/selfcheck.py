"""启动自检。

**与外部的连通性只告警、不致命**：Temporal 或 paper 一时不可用时，进程应该
留在原地退避重试（Worker 内建重试），而不是「起不来 → 重启 → 再起不来」的循环 ——
后者会让 `docker compose ps` 一直看到 Restarting，排障反而更难。

致命项只有一个：`HUNTER_INTERNAL_KEY` 缺失（缺口令时所有内网调用都会 401，
对着一个「活着但什么都做不了」的实例打日志没有意义）。
"""

from __future__ import annotations

import sys

from loguru import logger

from app import config


def _check_paper() -> str:
    from app.bridge.paper import PaperClient, PaperError

    try:
        h = PaperClient().healthz()
        return f"ok · mode={h.get('mode')} ledger_db={h.get('ledger_db')}"
    except PaperError as exc:
        return f"unreachable（{exc}）"


def _check_temporal() -> str:
    try:
        import asyncio

        from app.worker import connect_with_retry

        asyncio.run(connect_with_retry(attempts=3, delay=1.0))
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return f"unreachable（{exc}）"


def main() -> int:
    logger.info("── fin-worker 自检 ──")
    logger.info("  temporal      : {} (ns={})", config.temporal_address(), config.temporal_namespace())
    logger.info("  任务队列      : {}", config.temporal_task_queue())
    logger.info("  paper         : {}", config.paper_base_url())
    logger.info("  api           : {}", config.api_base_url())
    logger.info("  示例标的      : {}", config.sample_code())

    if not config.internal_key():
        logger.error("  HUNTER_INTERNAL_KEY 未设置 —— 内网接口会一律 401，拒绝启动")
        return 1

    logger.info("  paper 连通    : {}", _check_paper())
    logger.info("  temporal 连通 : {}", _check_temporal())
    logger.info("  （后两项不可用只告警：Worker 会退避重试，不致命）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
