"""Temporal Schedules —— 六个时点 + K 线 ETL 的**唯一**触发来源。

`01方案 §5.3`「上层调度只有一个权威」：这里的 Schedule 就是那一个权威。
**不许**再写一条系统 crontab 或进程内 `while True` 当兜底 —— 留了兜底就等于
两套调度器都以为自己是权威（`08 §1.3` 末行、`总控规则 §六-9`）。

Schedule 建在 **Temporal 服务端**，不是 Worker 进程里。所以杀掉并重启 `fin-worker`
不会丢调度：重启后 `ensure_schedules` 发现日历已存在，原样复用。

时点的 cron 只表达「周一到周五」。**节假日由工作流读 `fin_market_calendar` 挡掉**
（见 `workflows._run_point`）—— cron 里没有节假日概念，硬塞进去就会错。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger

from app import config
from app.points import POINTS


@dataclass(frozen=True)
class ScheduleSpecDef:
    schedule_id: str
    workflow: str
    cron: str
    args: dict
    title: str


# K 线 ETL 的三个市场。A 股按真日历挡（工作日数据源不更新）；港股 / 美股暂无
# 日历数据源，沿用旧口径「周一到周五」（见 `workflows.MarketEtlWorkflow`）。
ETL_SCHEDULES: tuple[ScheduleSpecDef, ...] = (
    ScheduleSpecDef("fin-etl-cn", "fin.market_etl", "25 15 * * 1-5", {"market": "cn"},
                    "A 股 K 线 ETL（收盘后）"),
    ScheduleSpecDef("fin-etl-hk", "fin.market_etl", "45 16 * * 1-5", {"market": "hk"},
                    "港股 K 线 ETL（收盘后）"),
    ScheduleSpecDef("fin-etl-us", "fin.market_etl", "25 3 * * 2-6", {"market": "us"},
                    "美股 K 线 ETL（次日凌晨）"),
)


def point_specs() -> list[ScheduleSpecDef]:
    return [
        ScheduleSpecDef(f"fin-point-{p.key}", p.workflow, p.cron, {}, p.title)
        for p in POINTS
    ]


# 标的元数据同步（M7）：每天一次，A 股开盘前（08:40）。
# 放在 preopen（09:15）之前 —— 开盘时风控要读的涨跌停必须已经是最新的。
INSTRUMENT_SCHEDULES: tuple[ScheduleSpecDef, ...] = (
    ScheduleSpecDef("fin-instrument-sync", "fin.instrument_sync", "40 8 * * 1-5",
                    {"market": "cn"}, "A 股标的元数据同步（涨跌停 / ST）"),
)


def all_specs() -> list[ScheduleSpecDef]:
    return point_specs() + list(ETL_SCHEDULES) + list(INSTRUMENT_SCHEDULES)


def build_schedule(spec: ScheduleSpecDef):
    """把一个 `ScheduleSpecDef` 变成 Temporal SDK 的 `Schedule` 对象。

    单独抽出来是为了**能在单测里真构造一次** —— Temporal SDK 的字段名
    （`time_zone_name`，不是 `timezone`）与版本有关，只有真构造才发现得了。
    M4 首次起容器时正是踩在这里：`TypeError: unexpected keyword argument 'timezone'`，
    Schedule 一个都没建上，而错误在容器启动日志里。`tests/test_schedules.py`
    有一条用例盯着这个。
    """
    from temporalio.client import (
        Schedule,
        ScheduleActionStartWorkflow,
        ScheduleOverlapPolicy,
        SchedulePolicy,
        ScheduleSpec,
    )

    return Schedule(
        action=ScheduleActionStartWorkflow(
            spec.workflow,
            spec.args,
            id=f"{spec.schedule_id}-run",
            task_queue=config.temporal_task_queue(),
        ),
        spec=ScheduleSpec(
            cron_expressions=[spec.cron],
            time_zone_name=config.schedule_timezone(),
        ),
        # 上一次还没跑完就再来一次 → 跳过这次，不排队堆积。
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )


async def ensure_schedules(client) -> list[dict]:
    """幂等地建齐所有 Schedule。已存在的原样复用（**不覆盖**）。

    返回每个 schedule 的 `{schedule_id, workflow, cron, created}`，供启动日志与自检使用。
    """
    # 延迟 import：让不需要 Temporal 的单元测试也能 import 本模块。
    from temporalio.client import ScheduleAlreadyRunningError
    from temporalio.service import RPCError, RPCStatusCode

    tz = config.schedule_timezone()
    out: list[dict] = []
    for spec in all_specs():
        created = False
        try:
            await client.create_schedule(spec.schedule_id, build_schedule(spec))
            created = True
        except ScheduleAlreadyRunningError:
            # 重启后重新进这里 —— Schedule 建在 Temporal 服务端，进程重启不丢。
            logger.info("[schedules] {} 已存在，复用（不覆盖）", spec.schedule_id)
        except RPCError as exc:
            if exc.status != RPCStatusCode.ALREADY_EXISTS:
                raise
            logger.info("[schedules] {} 已存在，复用（不覆盖）", spec.schedule_id)
        out.append({
            "schedule_id": spec.schedule_id, "workflow": spec.workflow,
            "cron": spec.cron, "timezone": tz, "created": created, "title": spec.title,
        })
    return out
