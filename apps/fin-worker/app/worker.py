"""Temporal Worker 引导。

一个 Worker、一个任务队列（`fin-trading`），注册全部工作流与 Activity。
杀掉它再起来 = Temporal 按执行历史恢复（`01方案 §11.2`「Worker 崩溃」一行）。
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from loguru import logger

from app import activities, config, schedules
from app.workflows import ALL_WORKFLOWS


def activity_list() -> list:
    return [
        activities.sync_calendar,
        activities.market_clock,
        activities.read_calendar,
        activities.list_active_projects,
        activities.begin_point_job,
        activities.write_checkpoint,
        activities.finish_point_job,
        activities.fail_point_job,
        activities.confirm_t1,
        activities.build_decision,
        activities.submit_decision,
        activities.match_open_orders,
        activities.close_day,
        activities.generate_daily_report,
        activities.trigger_market_etl,
        activities.sync_instruments,
        # R3 · 决策上下文注入 + 复核回路。**必须登记在这里** —— 这是一条显式清单
        # （`workflows.py:_exec` 用的就是这些函数对象），漏登记 = 工作流拉起时
        # 报「Activity 未注册」，而且失败只出现在 `WorkflowTaskFailed` 事件里。
        activities.freeze_memory,
        activities.review_collect,
        activities.review_propose,
        activities.review_append,
        # R7 · 影子验证（fin.shadow）。同上，**必须显式登记**。
        activities.market_points,
        activities.shadow_proposals,
        activities.shadow_step,
        activities.shadow_evaluate,
        # R13 · 自动盯盘观察（fin.observe）。同上，**必须显式登记**。
        activities.observe_proposals,
        activities.observe_applied,
        # L01 · 自动提案（fin.propose）。同上，**必须显式登记**。
        activities.propose_candidates,
        activities.propose_submit,
    ]


async def connect_with_retry(attempts: int = 60, delay: float = 2.0):
    """连 Temporal。启动顺序是 temporal 先于 fin-worker（`08 §6.3`），
    但 `depends_on: service_started` 只保证「进程起来了」，不保证「gRPC 能连」——
    所以这里退避重试，而不是让容器一上来就崩。"""
    from temporalio.client import Client

    last: Optional[Exception] = None
    for i in range(1, attempts + 1):
        try:
            client = await Client.connect(
                config.temporal_address(), namespace=config.temporal_namespace()
            )
            logger.info("[worker] 已连接 Temporal {} · namespace={}",
                        config.temporal_address(), config.temporal_namespace())
            return client
        except Exception as exc:  # noqa: BLE001 —— 连不上就是连不上，记录后重试
            last = exc
            if i == 1 or i % 5 == 0:
                logger.warning("[worker] 连接 Temporal 失败（第 {} 次）：{}", i, exc)
            await asyncio.sleep(delay)
    raise RuntimeError(f"无法连接 Temporal（{config.temporal_address()}）：{last}")


async def ensure_schedules(client) -> list[dict]:
    if not config.schedules_enabled():
        logger.warning("[worker] FIN_SCHEDULES_ENABLED=0 · 跳过 Schedule 建立（仅排障用）")
        return []
    made = await schedules.ensure_schedules(client)
    created = sum(1 for m in made if m["created"])
    logger.info("[worker] Schedule 就绪 {} 个（新建 {} · 复用 {}）",
                len(made), created, len(made) - created)
    for m in made:
        logger.info("[worker]   {} → {} [{} {}]", m["schedule_id"], m["workflow"],
                    m["cron"], m["timezone"])
    return made


async def run_worker_forever() -> None:
    """连上 Temporal、建 Schedule、跑 Worker（阻塞直到被取消）。"""
    from temporalio.worker import Worker

    client = await connect_with_retry()
    await ensure_schedules(client)
    # 同步 Activity 必须配一个 executor（Temporal Python SDK 的要求：
    # 「Activity xxx is not async so an activity_executor must be present」）。
    # 用线程池而不是改成 async —— 这些 Activity 里是阻塞 IO（httpx 同步客户端、
    # 以及 `run_decide` 里为验收注入的那个 sleep），异步化会把阻塞带进事件循环。
    executor = ThreadPoolExecutor(max_workers=config.activity_workers(),
                                  thread_name_prefix="fin-activity")
    worker = Worker(
        client,
        task_queue=config.temporal_task_queue(),
        workflows=list(ALL_WORKFLOWS),
        activities=activity_list(),
        activity_executor=executor,
    )
    logger.info("[worker] 开始监听任务队列 {}（Activity 线程池 {}）",
                config.temporal_task_queue(), config.activity_workers())
    try:
        await worker.run()
    finally:
        executor.shutdown(wait=False)
