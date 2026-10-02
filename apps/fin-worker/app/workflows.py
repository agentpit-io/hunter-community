"""六个时点工作流 + K 线 ETL 触发工作流（`05 §3.2` M-14）。

**六个时点是六个独立的 Workflow 类型**（不是一个大工作流里 sleep 六次）。
理由：Temporal UI 的 workflow 列表要能一眼看到「今天 09:30 那轮跑没跑」；
一个类型带参数会让六个时点在列表里长得一样，排障时反而要展开历史才知道是哪个。
每个时点一个类型 + 一个 Schedule，列表即状态。

**可持久化 · 可恢复 · 可幂等重试** 三个词各自的落点：

| 词 | 靠什么 |
|---|---|
| 可持久化 | Temporal 的事件历史 —— Worker 崩了，工作流状态在 Temporal 服务端 |
| 可恢复 | 重启后 Worker 重新领取任务，从历史恢放（replay），未完成的 Activity 重试 |
| 可幂等重试 | Activity 是 at-least-once，所以**每个写操作都带业务幂等键**（`bridge.idem`），paper 侧 `fin_idempotency` 认键返原回执 |

固定的重试策略：指数退避、最多 6 次。策略意图带 `valid_until`，过了有效期
paper 会拒（`01方案 §11.2`「信号过期 → 丢弃并重新分析，不补造成交」）——
所以重试**不会**把一张过期单补成成交。

**工作流代码里不出现任何 IO、时钟（除 `workflow.now()`）、随机数** ——
它们是确定性的前提，也是 replay 能对上历史的原因。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app import activities, gating
    from app.points import BY_KEY

SHANGHAI = timezone(timedelta(hours=8))

# Activity 重试：at-least-once。幂等由业务键保证（见模块文档）。
RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=6,
)
ACT_TIMEOUT = timedelta(seconds=120)
# 带故障注入（hold_seconds）的 decide 要睡够，否则会被超时打断 —— 给它更长的预算。
DECIDE_TIMEOUT = timedelta(minutes=10)
ETL_TIMEOUT = timedelta(minutes=60)
# 报告生成要读账本 + 调模型（可能十几秒到几分钟），给足预算。
REPORT_TIMEOUT = timedelta(minutes=5)
# 下单 Activity 会打心跳（`activities._heartbeat`）。心跳超时是**检测 Worker 崩溃**
# 的手段：Worker 被杀后心跳一断，Temporal 在 10 秒内就重试这个 Activity，
# 而不用等满 start_to_close（10 分钟）。生产上这也是对的 —— 一个下单动作
# 卡住超过 10 秒就该被认为是「可能已经挂了」。
DECIDE_HEARTBEAT = timedelta(seconds=10)


async def _exec(fn, arg, *, timeout: timedelta = ACT_TIMEOUT,
                heartbeat: timedelta | None = None) -> object:
    return await workflow.execute_activity(
        fn, arg, start_to_close_timeout=timeout, heartbeat_timeout=heartbeat,
        retry_policy=RETRY,
    )


def filter_projects(projects: list[dict], only: str | None) -> list[dict]:
    """`only` 非空时只保留那一个项目。抽成纯函数是为了能单独测 —— 它决定
    「一次手工触发会给几个账户下单」，验收环境里有一堆测试残留项目时很容易出事。"""
    if not only:
        return list(projects)
    return [p for p in projects if p.get("project_id") == only]


def _today() -> str:
    """交易日按**上海时间**切（A 股）。`workflow.now()` 是确定性的。"""
    return workflow.now().astimezone(SHANGHAI).date().isoformat()


def _now_iso() -> str:
    return workflow.now().astimezone(SHANGHAI).isoformat()


async def _run_point(point_key: str, req: dict) -> dict:
    """六个时点共用的执行体。返回一份可读的摘要（进 Temporal 历史，也进 job checkpoint）。"""
    point = BY_KEY[point_key]
    trade_date = req.get("trade_date") or _today()
    now_iso = req.get("now") or _now_iso()
    market = req.get("market", "a")
    summary: dict = {"point": point.key, "at": point.at, "trade_date": trade_date, "kind": point.kind}

    # ① 交易日历（preopen 先同步一次，其余时点直接读）
    if point.kind == "preopen":
        summary["calendar_sync"] = await _exec(
            activities.sync_calendar,
            {"trade_date": trade_date, "market": market,
             "lookback_days": req.get("lookback_days", 5),
             "lookahead_days": req.get("lookahead_days", 12)},
        )

    cal = await _exec(activities.read_calendar, {"trade_date": trade_date})
    summary["calendar"] = cal
    action, why = gating.gate(cal)
    if action == gating.SKIP_UNKNOWN:
        # 日历缺数据 → **不当交易日**并如实告警（`05 §3.2` M-14 验收项）。
        summary["status"] = action
        summary["warning"] = f"{trade_date} {why}"
        workflow.logger.warning(summary["warning"])
        return summary
    if action == gating.SKIP_NON_TRADING:
        summary["status"] = action
        summary["note"] = f"{trade_date} 非交易日（{why}），未触发"
        workflow.logger.info(summary["note"])
        return summary

    # ② 对每个进行中的项目跑这个时点
    projects = await _exec(activities.list_active_projects, {})
    # 只跑指定项目：**补跑 / 故障注入验收**用（调度永远不带这个字段，
    # 所以生产路径逐字节不变）。没有它，一次手工触发会给**每一个**进行中的
    # 项目下单 —— 验收环境里有一堆测试残留项目时，证据会被冲散。
    only = req.get("project_id")
    if only:
        projects = filter_projects(projects, only)
        summary["filtered_project"] = only
    summary["projects"] = []
    for project in projects:
        summary["projects"].append(
            await _run_for_project(point, project, trade_date, now_iso, req)
        )
    summary["status"] = "ok"
    summary["active_projects"] = len(projects)
    return summary


async def _run_for_project(point, project: dict, trade_date: str, now_iso: str, req: dict) -> dict:
    project_id = project["project_id"]
    out: dict = {"project_id": project_id, "point": point.key}

    # 业务检查点：登记 + 标 RUNNING（同键幂等，重启重放拿到同一个 job_id）
    job = await _exec(activities.begin_point_job, {
        "project_id": project_id, "trade_date": trade_date,
        "point": point.key, "at": point.at, "now": now_iso,
    })
    job_id = job["job_id"]
    out["job_id"] = job_id

    if point.kind == "preopen":
        out["confirm_t1"] = await _exec(activities.confirm_t1, {"project_id": project_id})
        await _exec(activities.write_checkpoint, {
            "job_id": job_id,
            "checkpoint": {"point": point.key, "phase": "t1_confirmed",
                           "positions_made_sellable": out["confirm_t1"].get("positions_made_sellable")},
        })
    elif point.kind == "decide":
        # ① 出决定（只读 Activity）。结果进 Temporal 历史 —— 重试时读的是历史里那份，
        #    不会因为账户版本被上一笔成交改掉而变。
        built = await _exec(activities.build_decision, {
            "project_id": project_id, "trade_date": trade_date, "point": point.key,
            "now": now_iso, "code": req.get("code"),
        })
        if built.get("halted"):
            # ── M6 · 总开关关闭：**这一步就到头，不提交任何委托** ──────────
            # 写一条「停在这」的检查点，然后正常收尾（job SUCCEEDED）。这不是失败：
            # 用户按的就是「停」，工作流该成功结束并在历史上留下「本时点未交易」。
            out["halted"] = True
            out["reason"] = built.get("reason")
            await _exec(activities.write_checkpoint, {
                "job_id": job_id,
                "checkpoint": {"point": point.key, "phase": "halted",
                               "reason": built.get("reason")},
            })
            out["finish"] = await _exec(activities.finish_point_job, {
                "job_id": job_id,
                "result_ref": f"point:{point.key}:{trade_date}:{project_id}:halted",
                "checkpoint": {"point": point.key, "trade_date": trade_date,
                               "phase": "done", "halted": True},
            })
            return out
        out["decision"] = built["decision"]
        out["idempotency_key"] = built["idempotency_key"]
        # ② 提交（有副作用的 Activity）。命令是冻结的，重试逐字节相同 → 幂等重放。
        submitted = await _exec(activities.submit_decision, {
            "command": built["command"], "idempotency_key": built["idempotency_key"],
            "project_id": project_id, "point": point.key,
            "hold_seconds": req.get("hold_seconds", 0),
        }, timeout=DECIDE_TIMEOUT, heartbeat=DECIDE_HEARTBEAT)
        out["submit"] = submitted
        await _exec(activities.write_checkpoint, {
            "job_id": job_id,
            "checkpoint": {"point": point.key, "phase": "ordered",
                           "order_status": submitted.get("order_status"),
                           "trade_id": submitted.get("trade_id"),
                           "idempotency_key": built["idempotency_key"]},
        })
    elif point.kind == "match":
        out["match_open"] = await _exec(activities.match_open_orders, {"project_id": project_id})
    elif point.kind == "close":
        out["close"] = await _exec(activities.close_day, {
            "project_id": project_id, "now": now_iso,
        })
        # M5 · 报告生成（M-17）接在**收盘估值之后**：报告的事实层读的正是刚写下的
        # 那次收盘估值（`fin_valuation`）。
        #
        # 加在 `close` 分支的**末尾**（M4 交接的 replay 提示）：这一步只对
        # 「部署之后新起的工作流」生效 —— 已 COMPLETED 的历史不会被重放；
        # 部署窗口在收盘之后，在途的 15:30 工作流不存在。整体退出走
        # `req.get("report", True)`，排障时可关。
        if req.get("report", True):
            out["report"] = await _exec(
                activities.generate_daily_report,
                {"project_id": project_id, "trade_date": trade_date, "now": now_iso},
                timeout=REPORT_TIMEOUT,
            )

    out["finish"] = await _exec(activities.finish_point_job, {
        "job_id": job_id,
        "result_ref": f"point:{point.key}:{trade_date}:{project_id}",
        "checkpoint": {"point": point.key, "trade_date": trade_date, "phase": "done",
                       "result": out},
    })
    return out


# ── 六个时点（六个 Workflow 类型）──────────────────────────────────────────
@workflow.defn(name="fin.point_0915")
class Point0915Workflow:
    @workflow.run
    async def run(self, req: dict | None = None) -> dict:
        return await _run_point("0915", req or {})


@workflow.defn(name="fin.point_0930")
class Point0930Workflow:
    @workflow.run
    async def run(self, req: dict | None = None) -> dict:
        return await _run_point("0930", req or {})


@workflow.defn(name="fin.point_1130")
class Point1130Workflow:
    @workflow.run
    async def run(self, req: dict | None = None) -> dict:
        return await _run_point("1130", req or {})


@workflow.defn(name="fin.point_1300")
class Point1300Workflow:
    @workflow.run
    async def run(self, req: dict | None = None) -> dict:
        return await _run_point("1300", req or {})


@workflow.defn(name="fin.point_1455")
class Point1455Workflow:
    @workflow.run
    async def run(self, req: dict | None = None) -> dict:
        return await _run_point("1455", req or {})


@workflow.defn(name="fin.point_1530")
class Point1530Workflow:
    @workflow.run
    async def run(self, req: dict | None = None) -> dict:
        return await _run_point("1530", req or {})


POINT_WORKFLOWS = (
    Point0915Workflow, Point0930Workflow, Point1130Workflow,
    Point1300Workflow, Point1455Workflow, Point1530Workflow,
)


# ── K 线 ETL 触发（「谁决定什么时候拉数据」）────────────────────────────────
@workflow.defn(name="fin.market_etl")
class MarketEtlWorkflow:
    """由 Temporal 决定「什么时候拉 K 线」。**取数实现原样不动**（api 侧复用）。

    - A 股：读 `fin_market_calendar`，非交易日跳过（真日历，含节假日）。
    - 港股 / 美股：目前**没有**对应的交易日历数据源，沿用旧口径「周一到周五」，
      并在结果里写明这是近似 —— 拿 A 股日历冒充港股/美股日历就是编数据。
      补上真实日历是 M7 的事。
    """

    @workflow.run
    async def run(self, req: dict) -> dict:
        market = req["market"]
        trade_date = req.get("trade_date") or _today()
        now_iso = req.get("now") or _now_iso()

        if market == "cn":
            cal = await _exec(activities.read_calendar, {"trade_date": trade_date})
            if not cal["known"]:
                return {"market": market, "trade_date": trade_date, "status": "calendar_unknown",
                        "warning": f"{trade_date} 没有交易日历，未触发 {market} ETL"}
            if not cal["trading"]:
                return {"market": market, "trade_date": trade_date, "status": "non_trading_day"}
        else:
            weekday = datetime.fromisoformat(trade_date).weekday()
            if weekday >= 5:
                return {"market": market, "trade_date": trade_date, "status": "weekend"}

        result = await _exec(
            activities.trigger_market_etl,
            {"market": market, "limit": req.get("limit"), "bars": req.get("bars")},
            timeout=ETL_TIMEOUT,
        )
        result.update({"trade_date": trade_date, "now": now_iso, "status": "triggered"})
        return result


# ── 标的元数据同步（M7 · M-20）────────────────────────────────────────────
@workflow.defn(name="fin.instrument_sync")
class InstrumentSyncWorkflow:
    """每晚把 A 股标的的涨跌停 / ST 元数据同步进 `fin_instrument`。

    独立成一个工作流类型（不是塞进某个时点）：它是**参考数据**的刷新，
    与「今天要不要交易」无关，也不该因为某个交易日不是 A 股交易日就跳过 ——
    master 数据随时会变（新股上市、戴帽摘帽）。所以调度是「每天一次」，
    内层自己按市场过滤。
    """

    @workflow.run
    async def run(self, req: dict | None = None) -> dict:
        req = req or {}
        return await _exec(
            activities.sync_instruments,
            {"market": req.get("market", "cn"), "codes": req.get("codes")},
            timeout=ACT_TIMEOUT,
        )


ALL_WORKFLOWS = POINT_WORKFLOWS + (MarketEtlWorkflow, InstrumentSyncWorkflow)
