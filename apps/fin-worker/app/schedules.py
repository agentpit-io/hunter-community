"""Temporal Schedules —— **每市场一组时点** + K 线 ETL 的**唯一**触发来源（N4）。

`01方案 §5.3`「上层调度只有一个权威」：这里的 Schedule 就是那一个权威。
**不许**再写一条系统 crontab 或进程内 `while True` 当兜底 —— 留了兜底就等于
两套调度器都以为自己是权威（`08 §1.3` 末行、`总控规则 §六-9`）。

Schedule 建在 **Temporal 服务端**，不是 Worker 进程里。所以杀掉并重启 `fin-worker`
不会丢调度：重启后 `ensure_schedules` 发现日历已存在，原样复用。

**三个市场互不影响**（`plan/N4.md` §一.1）：
- 每个市场一组时点（`fin_market_rule.points`），Schedule 的 `time_zone_name` 取该市场时区
  （IANA 名，夏令时自动跟随，**不写死 ±N 偏移**）；
- 某市场非交易日 / 日历缺失 → 该市场当日空跑并记明原因，**其他市场照常**。

时点的 cron 只表达「周一到周五」。**节假日由工作流读 `fin_market_calendar` 挡掉**
（见 `workflows._run_point` / `MarketEtlWorkflow`）—— cron 里没有节假日概念，硬塞进去就会错。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from app import config
from app.market_time import MARKET_TZ
from app.points import DEFAULT_POINTS, Point, points_for


@dataclass(frozen=True)
class ScheduleSpecDef:
    schedule_id: str
    workflow: str
    cron: str
    args: dict
    title: str
    # None → 回落 `config.schedule_timezone()`（一期口径：Asia/Shanghai）。
    timezone: Optional[str] = None


# K 线 ETL 的三个市场。**触发前先查该市场日历**（`workflows.MarketEtlWorkflow`）：
# 三条各自按自己市场的交易日历挡（不再用「周一至周五」近似）。
# cron 仍用上海时间表达（这是**触发的挂钟时刻**，与市场的交易时段无关；
# 例如美股 ETL 排在次日凌晨拉上一交易日的收盘数据）。
ETL_SCHEDULES: tuple[ScheduleSpecDef, ...] = (
    ScheduleSpecDef("fin-etl-cn", "fin.market_etl", "25 15 * * 1-5", {"market": "cn"},
                    "A 股 K 线 ETL（收盘后）"),
    ScheduleSpecDef("fin-etl-hk", "fin.market_etl", "45 16 * * 1-5", {"market": "hk"},
                    "港股 K 线 ETL（收盘后）"),
    ScheduleSpecDef("fin-etl-us", "fin.market_etl", "25 3 * * 2-6", {"market": "us"},
                    "美股 K 线 ETL（次日凌晨）"),
)


# 标的元数据同步（M7 · N3 放开市场）：每天一次，**每个市场各一条**。
#   · A 股放在 preopen（09:15）之前 —— 开盘时风控要读的涨跌停必须已经是最新的；
#   · 港美股没有涨跌幅限制，元数据（每手 / 交易所）是**静态**的，时点只需各市场开盘前；
#     三条各自独立跑，一条失败不影响另一条。
INSTRUMENT_SCHEDULES: tuple[ScheduleSpecDef, ...] = (
    ScheduleSpecDef("fin-instrument-sync", "fin.instrument_sync", "40 8 * * 1-5",
                    {"market": "cn"}, "A 股标的元数据同步（涨跌停 / ST）"),
    ScheduleSpecDef("fin-instrument-sync-hk", "fin.instrument_sync", "42 8 * * 1-5",
                    {"market": "hk"}, "港股标的元数据同步（每手 / 交易所）"),
    ScheduleSpecDef("fin-instrument-sync-us", "fin.instrument_sync", "44 8 * * 1-5",
                    {"market": "us"}, "美股标的元数据同步（每手 = 1 / 交易所）"),
)


def fallback_market_rules() -> list[dict]:
    """离线 / DB 读不到时的三组市场规则（时区 + 时点），与 `0030` 的种子逐项一致。"""
    return [
        {"market": m, "timezone": MARKET_TZ[m], "points": list(DEFAULT_POINTS[m])}
        for m in ("CN_A", "HK", "US")
    ]


def _rules_by_market(market_rules: Optional[list[dict]]) -> list[dict]:
    return list(market_rules) if market_rules else fallback_market_rules()


def point_specs(market_rules: Optional[list[dict]] = None) -> list[ScheduleSpecDef]:
    """每个市场一组时点 Schedule（市场时区各自正确）。"""
    specs: list[ScheduleSpecDef] = []
    for rule in _rules_by_market(market_rules):
        market = rule["market"]
        tz = rule.get("timezone") or MARKET_TZ.get(market)
        times = list(rule.get("points") or DEFAULT_POINTS.get(market) or [])
        for p in points_for(market, times):
            specs.append(ScheduleSpecDef(
                schedule_id=f"fin-point-{market}-{p.at.replace(':', '')}",
                workflow=p.workflow, cron=p.cron,
                args={"market": market, "at": p.at, "point": p.key},
                title=f"{market} · {p.title}", timezone=tz,
            ))
    return specs


def all_specs(market_rules: Optional[list[dict]] = None) -> list[ScheduleSpecDef]:
    return point_specs(market_rules) + list(ETL_SCHEDULES) + list(INSTRUMENT_SCHEDULES)


def build_schedule(spec: ScheduleSpecDef):
    """把一个 `ScheduleSpecDef` 变成 Temporal SDK 的 `Schedule` 对象。

    单独抽出来是为了**能在单测里真构造一次** —— Temporal SDK 的字段名
    （`time_zone_name`，不是 `timezone`）与版本有关，只有真构造才发现得了。
    M4 首次起容器时正是踩在这里：`TypeError: unexpected keyword argument 'timezone'`，
    Schedule 一个都没建上，而错误在容器启动日志里。`tests/test_schedules.py`
    有一条用例盯着这个。

    `time_zone_name` 取该市场的 IANA 时区名（夏令时自动跟随）——
    **不写死 ±N 偏移**（M7 美股 12 小时偏差的根因）。
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
            time_zone_name=spec.timezone or config.schedule_timezone(),
        ),
        # 上一次还没跑完就再来一次 → 跳过这次，不排队堆积。
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )


# 一期遗留的 A 股单市场时点 Schedule：N4 起被「每市场一组」的
# `fin-point-<market>-<HHMM>` 取代。这里显式**退役**它们（删除 Schedule，
# 不动任何账本数据）—— 否则 09:15 会同时触发旧 `fin.point_0915` 与新
# `fin-point-CN_A-0915` 两个 Schedule（幂等键相同不会重复下单，但会白跑一倍）。
LEGACY_POINT_SCHEDULE_IDS: tuple[str, ...] = (
    "fin-point-0915", "fin-point-0930", "fin-point-1130",
    "fin-point-1300", "fin-point-1455", "fin-point-1530",
)


async def _retire_legacy_point_schedules(client) -> None:
    """删掉一期遗留的单市场时点 Schedule（幂等：不存在就跳过）。"""
    from temporalio.service import RPCError, RPCStatusCode

    for sid in LEGACY_POINT_SCHEDULE_IDS:
        try:
            await client.get_schedule_handle(sid).delete()
            logger.warning("[schedules] 退役一期遗留 Schedule {}", sid)
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                logger.warning("[schedules] 退役 {} 失败（继续）：{}", sid, exc)
        except Exception as exc:  # noqa: BLE001 —— 退役失败不该阻断新调度建立
            logger.warning("[schedules] 退役 {} 失败（继续）：{}", sid, exc)


def load_market_rules() -> Optional[list[dict]]:
    """从 paper 读三个市场的规则行（时区 / 调度时点）。读不到 → None（用兜底）。

    fin-worker **没有账本库连接**，只能经 HTTP（`bridge.paper.PaperClient`）。
    Worker 启动时 paper 可能还没就绪 —— 那就用 `fallback_market_rules()`，
    与 `0030` 的种子逐项一致，调度照常建起来。
    """
    try:
        from app.bridge.paper import PaperClient

        rows = PaperClient().market_rules()
        if rows:
            return rows
    except Exception as exc:  # noqa: BLE001 —— 读不到就用兜底，不阻断调度建立
        logger.warning("[schedules] 读 fin_market_rule 失败，用兜底时点：{}", exc)
    return None


async def ensure_schedules(client) -> list[dict]:
    """幂等地建齐所有 Schedule。已存在的原样复用（**不覆盖**）。

    返回每个 schedule 的 `{schedule_id, workflow, cron, timezone, created}`，
    供启动日志与自检使用。
    """
    # 延迟 import：让不需要 Temporal 的单元测试也能 import 本模块。
    from temporalio.client import ScheduleAlreadyRunningError
    from temporalio.service import RPCError, RPCStatusCode

    await _retire_legacy_point_schedules(client)
    market_rules = load_market_rules()
    out: list[dict] = []
    for spec in all_specs(market_rules):
        tz = spec.timezone or config.schedule_timezone()
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
