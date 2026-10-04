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
from app.points import DEFAULT_POINTS, Point, cron_of, points_for


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
    """离线 / DB 读不到时的三组市场规则（时区 + 时点 + 时段），与 `0030` 的种子逐项一致。

    `sessions` 也一并给出：复核 Schedule 要用**时段末点**（R3）。时段常量只有一份
    （`activities.DEFAULT_SESSIONS`，与日历同步共用）—— 这里**不另抄一张表**，
    延迟 import 它即可（`activities` 会拉 httpx，没必要在模块加载期就拉）。
    """
    from app.activities import DEFAULT_SESSIONS

    return [
        {"market": m, "timezone": MARKET_TZ[m], "points": list(DEFAULT_POINTS[m]),
         "sessions": [dict(s) for s in DEFAULT_SESSIONS[m]]}
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


def _session_close(sessions: list[dict]) -> str:
    """该市场时段里**最晚的一个收盘时刻**（`HH:MM`）。

    香港有收市竞价那一段（`16:00-16:10`），最晚收盘是 `16:10` 而不是 `16:00` ——
    复核要排在**真的收完**之后。取 `max` 而不是「最后一段」，两种写法这里同值，
    但 `max` 对「时段乱序」也成立。
    """
    closes = [str(s.get("close")) for s in sessions or [] if s.get("close")]
    return max(closes) if closes else "15:00"


def _hhmm_plus(at: str, minutes: int) -> str:
    """`HH:MM` + N 分钟（跨零点回绕）。**具体分钟数来自配置**，这里只是算术。"""
    hh, mm = (int(x) for x in at.split(":"))
    total = (hh * 60 + mm + int(minutes)) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def review_specs(market_rules: Optional[list[dict]] = None) -> list[ScheduleSpecDef]:
    """每个市场一条**收盘后复核** Schedule（R3 · Q4 已拍板）。

    时点 = 该市场 **`fin_market_rule.sessions` 的时段末点** + `FIN_REVIEW_DELAY_MINUTES`
    （默认 `30`，**可配**）。延迟从 `config.review_delay_minutes()` 读 ——
    **不把具体分钟数硬编码进代码**（拍板的就是「可配」，硬编码等于把拍板废掉）。

    **现有 6 个 point 时点一个都不改**（`point_specs` 一字未动）—— 复核是**新加**的一条，
    不是把某个时点挪走。id 形如 `fin-review-HK`，与 `fin-point-HK-0930` 一眼可分。

    Schedule 是**全局的、与项目数无关**（同 `二期迭代完善 §4.3`）：工作流内层对
    「该市场所有进行中的项目」各复盘一次。
    """
    delay = config.review_delay_minutes()
    specs: list[ScheduleSpecDef] = []
    for rule in _rules_by_market(market_rules):
        market = rule["market"]
        tz = rule.get("timezone") or MARKET_TZ.get(market)
        sessions = list(rule.get("sessions") or [])
        if not sessions:                      # DB 行没有 sessions 列（老形状）→ 用兜底那一份
            sessions = next((r.get("sessions") or [] for r in fallback_market_rules()
                             if r.get("market") == market), [])
        at = _hhmm_plus(_session_close(sessions), delay)
        specs.append(ScheduleSpecDef(
            schedule_id=f"fin-review-{market}",
            workflow="fin.review",
            cron=cron_of(at),
            args={"market": market, "at": at, "point": f"{market}-review"},
            title=f"{market} · 收盘后复核（时段末点 + {delay} 分钟）",
            timezone=tz,
        ))
    return specs


def shadow_specs(market_rules: Optional[list[dict]] = None) -> list[ScheduleSpecDef]:
    """每个市场一条**影子验证** Schedule（R7 · `plan/R7.md` §一.3）。

    时点 = 该市场**时段末点** + `FIN_REVIEW_DELAY_MINUTES`（复核延迟）+ `FIN_SHADOW_DELAY_MINUTES`
    （影子延迟，默认 15）—— 排在 `fin-review-<market>` **之后**，不抢它的时点，也不改它。
    id 形如 `fin-shadow-HK`，与 `fin-point-HK-0930` / `fin-review-HK` 一眼可分。

    Schedule 是**全局的、与项目数无关**：工作流内层对「该市场所有进行中的项目」下
    每个待验证提案各验证一遍。**现有 6 个 point 时点一个都不改**（`point_specs` 一字未动）。
    """
    delay = config.review_delay_minutes() + config.shadow_delay_minutes()
    specs: list[ScheduleSpecDef] = []
    for rule in _rules_by_market(market_rules):
        market = rule["market"]
        tz = rule.get("timezone") or MARKET_TZ.get(market)
        sessions = list(rule.get("sessions") or [])
        if not sessions:
            sessions = next((r.get("sessions") or [] for r in fallback_market_rules()
                             if r.get("market") == market), [])
        at = _hhmm_plus(_session_close(sessions), delay)
        specs.append(ScheduleSpecDef(
            schedule_id=f"fin-shadow-{market}",
            workflow="fin.shadow",
            cron=cron_of(at),
            args={"market": market, "at": at, "point": f"{market}-shadow"},
            title=f"{market} · 收盘后影子验证（时段末点 + 复核 {config.review_delay_minutes()} "
                  f"+ 影子 {config.shadow_delay_minutes()} 分钟）",
            timezone=tz,
        ))
    return specs


def observe_specs(market_rules: Optional[list[dict]] = None) -> list[ScheduleSpecDef]:
    """每个市场一条**自动盯盘观察** Schedule（R13 · `plan/R13.md` §A）。

    时点 = 该市场**时段末点** + `FIN_REVIEW_DELAY_MINUTES`（复核）+
    `FIN_SHADOW_DELAY_MINUTES`（影子，默认 15）+ `FIN_OBSERVE_DELAY_MINUTES`
    （观察，默认 30）—— 排在 `fin-shadow-<market>` **之后**，不抢它的时点，也不改它。
    id 形如 `fin-observe-HK`，与 `fin-point-HK-0930` / `fin-review-HK` / `fin-shadow-HK`
    一眼可分。

    延迟分钟数**一律读 `config`**（连复核 / 影子那两个也现读）——「可配」这条对每一段
    都成立，硬编码任何一段都等于把拍板废掉。

    Schedule 是**全局的、与项目数无关**：工作流内层对「该市场所有进行中的项目」下
    每个**已生效**的提案各观察一次。**现有 Schedule 一条都不改**
    （`point_specs` / `ETL_SCHEDULES` / `INSTRUMENT_SCHEDULES` / `review_specs` /
    `shadow_specs` 一字未动）。
    """
    delay = (config.review_delay_minutes() + config.shadow_delay_minutes()
             + config.observe_delay_minutes())
    specs: list[ScheduleSpecDef] = []
    for rule in _rules_by_market(market_rules):
        market = rule["market"]
        tz = rule.get("timezone") or MARKET_TZ.get(market)
        sessions = list(rule.get("sessions") or [])
        if not sessions:
            sessions = next((r.get("sessions") or [] for r in fallback_market_rules()
                             if r.get("market") == market), [])
        at = _hhmm_plus(_session_close(sessions), delay)
        specs.append(ScheduleSpecDef(
            schedule_id=f"fin-observe-{market}",
            workflow="fin.observe",
            cron=cron_of(at),
            args={"market": market, "at": at, "point": f"{market}-observe"},
            title=f"{market} · 收盘后自动观察（时段末点 + 复核 {config.review_delay_minutes()} "
                  f"+ 影子 {config.shadow_delay_minutes()} + 观察 {config.observe_delay_minutes()} 分钟）",
            timezone=tz,
        ))
    return specs


def propose_specs(market_rules: Optional[list[dict]] = None) -> list[ScheduleSpecDef]:
    """每个市场一条**自动提案** Schedule（L01 · `plan/L01.md` §3.2）。

    时点 = 该市场**时段末点** + `FIN_REVIEW_DELAY_MINUTES`（复核）+ `FIN_PROPOSE_DELAY_MINUTES`
    （提案，默认 2）—— 排在 `fin-review-<market>`（产经验）之后、`fin-shadow-<market>`
    （验提案）之前，不抢它们的时点，也不改它们。id 形如 `fin-propose-HK`，与
    `fin-point-HK-0930` / `fin-review-HK` / `fin-shadow-HK` / `fin-observe-HK` 一眼可分。

    延迟分钟数**一律读 `config`**（连复核那个也现读）——「可配」这条对每一段都成立。
    Schedule 是**全局的、与项目数无关**：工作流内层对「该市场所有进行中的项目」各读一次候选。
    **现有 Schedule 一条都不改。**
    """
    delay = config.review_delay_minutes() + config.propose_delay_minutes()
    specs: list[ScheduleSpecDef] = []
    for rule in _rules_by_market(market_rules):
        market = rule["market"]
        tz = rule.get("timezone") or MARKET_TZ.get(market)
        sessions = list(rule.get("sessions") or [])
        if not sessions:
            sessions = next((r.get("sessions") or [] for r in fallback_market_rules()
                             if r.get("market") == market), [])
        at = _hhmm_plus(_session_close(sessions), delay)
        specs.append(ScheduleSpecDef(
            schedule_id=f"fin-propose-{market}",
            workflow="fin.propose",
            cron=cron_of(at),
            args={"market": market, "at": at, "point": f"{market}-propose"},
            title=f"{market} · 收盘后自动提案（时段末点 + 复核 {config.review_delay_minutes()} "
                  f"+ 提案 {config.propose_delay_minutes()} 分钟）",
            timezone=tz,
        ))
    return specs


def all_specs(market_rules: Optional[list[dict]] = None) -> list[ScheduleSpecDef]:
    return (point_specs(market_rules) + list(ETL_SCHEDULES) + list(INSTRUMENT_SCHEDULES)
            + review_specs(market_rules) + propose_specs(market_rules)
            + shadow_specs(market_rules) + observe_specs(market_rules))


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
