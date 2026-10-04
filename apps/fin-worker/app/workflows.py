"""每个市场一组时点工作流 + K 线 ETL 触发工作流（N4 · `plan/N4.md` §一.1）。

**时点工作流按「角色」分类型**（`fin.point_preopen` / `_decide` / `_match_a` /
`_match_b` / `_match_c` / `_close`），**市场经 Schedule 的 `args` 传进来** ——
三个市场共用同一套角色类型，每个市场的时点各是一个 Schedule（`schedules.point_specs`）。
Temporal UI 的 Schedule 列表即状态：每个 Schedule 的 id（`fin-point-HK-0930`）一眼看出市场。

**市场是一等参数，贯穿该市场当日全流程**：交易日按**该市场时区**切、日历按
`(market, 当地日期)` 读、活跃项目按 `market_scope` 过滤。

**三个市场互不影响**：某市场非交易日 → 该市场时点空跑并记明原因（`non_trading_day`），
其他市场照常；某市场日历缺失 → 该市场空跑并**告警**（`calendar_unknown`），
其他市场照常。这就是本期最核心的用户价值 —— **总有市场在跑**。

**可持久化 · 可恢复 · 可幂等重试** 三个词各自的落点：

| 词 | 靠什么 |
|---|---|
| 可持久化 | Temporal 的事件历史 —— Worker 崩了，工作流状态在 Temporal 服务端 |
| 可恢复 | 重启后 Worker 重新领取任务，从历史恢放（replay），未完成的 Activity 重试 |
| 可幂等重试 | Activity 是 at-least-once，所以**每个写操作都带业务幂等键**（`bridge.idem`），paper 侧 `fin_idempotency` 认键返原回执 |

**工作流代码里不出现任何 IO、时钟（除 `workflow.now()`）、随机数** ——
它们是确定性的前提，也是 replay 能对上历史的原因。
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app import activities, gating
    from app.channels import canonical
    from app.points import POINT_SHAPE

_ROLE_KIND: dict[str, str] = {role: kind for role, kind in POINT_SHAPE}


class _PausableWorkflow:
    """给工作流加「暂停 / 恢复信号 + 状态查询」与「当前长任务」两样东西（L08 · 控制通道）。

    **为什么是协作式的**：Temporal 没有原生的「暂停一个正在跑的 workflow」；唯一能动的
    状态变化是 `cancel`（终态）。所以暂停 = 往工作流发 `pause` 信号，工作流在下一个
    **检查点**（`_gate`）停下并 `wait_condition` 等 `resume`（`runtime_control.pause_impl`）。
    这与 Temporal 官方「信号改变工作流状态」的用法一致，且 `pause` 期间工作流停在
    `RUNNING`、不再往下走 —— 是真的停住了，不是挂个标记。

    **两处保证**：

    1. **不加命令、不改历史**：`_gate` 在未暂停时 `wait_condition` 立刻返回（谓词为真时不发命令），
       所以既有历史 replay 逐字节不变（新增的只是信号 / 查询处理器，它们不产生命令）。
    2. **长任务可取消**：`_track_job` 把「当前 `fin_job`」记在实例上，经 `current_job` 查询
       暴露给控制通道 —— `cancel` 据此把那个 job 一并取消（`§10.4` 的「可请求取消」）。
    """

    def __init__(self) -> None:
        self._paused = False
        self._current_job_id: str | None = None

    @workflow.signal(name="pause")
    def _on_pause(self) -> None:
        self._paused = True

    @workflow.signal(name="resume")
    def _on_resume(self) -> None:
        self._paused = False

    @workflow.query(name="paused")
    def _query_paused(self) -> bool:
        return self._paused

    @workflow.query(name="current_job")
    def _query_current_job(self) -> dict | None:
        if self._current_job_id is None:
            return None
        return {"job_id": self._current_job_id}

    async def _gate(self) -> None:
        """暂停检查点：暂停时停在这里等 `resume`。未暂停时**零开销、零命令**。"""
        if self._paused:
            await workflow.wait_condition(lambda: not self._paused)

    def _track_job(self, job_id: str | None) -> None:
        self._current_job_id = job_id


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


def projects_for_market(projects: list[dict], market: str) -> list[dict]:
    """只保留**选中集合包含该市场**的项目（P2 · 方案 §4.1）。

    判据是 `fin_project_market` 那一组市场（paper 的 `list_projects` 带出的 `markets`），
    **不是** `market_scope == market`（那表达不了 {港股,美股} 这种子集，而且 `MULTI`
    在三个市场都不匹配 = 空转）。「自由组合」要的就是：选了几个市场，就在那几个市场的
    时点各驱动一次 —— `MULTI`（集合含三个）因此**三个市场各自跑一次**，不再空转。

    **向后兼容**：`markets` 缺失（老 paper 响应）时退回单值语义（`market_scope`），
    A 股单市场项目的行为逐字节不变。`markets` 为空列表 = 「没声明过市场」→ 不驱动
    任何市场（**不回落** `market_scope` —— 回落就会把「没声明」当成 A 股）。
    """
    out = []
    for p in projects:
        mk = p.get("markets")                       # P2：paper 带出的集合
        if mk is None:                              # 兼容：老响应只有 market_scope
            mk = [p.get("market_scope") or "CN_A"]
        # paper 的 `markets` 是 `fin_project_market` 的行（对象：market/currency/…），
        # 这里取 `market` 那一列；同时容忍纯字符串（老调用方 / 测试夹手）。
        codes = [m.get("market") if isinstance(m, dict) else m for m in mk]
        if market in codes:
            out.append(p)
    return out


# 市场当地时间（交易日按**该市场时区**切）在**工作流里算不出来** —— 见
# `activities.market_clock` 的说明：Temporal 的沙箱会把 `ZoneInfo` 包成
# `_RestrictedProxy`，`astimezone()` 当场抛错（失败只出现在 `WorkflowTaskFailed`
# 事件里，HTTP 触发接口照样回 200）。所以时区换算整体挪进 Activity，
# 结果进事件历史、replay 读历史那份，确定性不受影响。
async def _market_clock(market: str) -> dict:
    return await _exec(activities.market_clock, {"market": market})


async def _run_point(role: str, req: dict, ctl: "_PausableWorkflow") -> dict:
    """六个角色共用的执行体。返回一份可读的摘要（进 Temporal 历史，也进 job checkpoint）。

    `ctl` 是所属工作流实例（L08）：`_gate` 是暂停检查点，`_track_job` 记当前长任务。
    """
    market = canonical(req.get("market") or "CN_A")
    kind = _ROLE_KIND[role]
    # 市场当地日期 / 时刻：补跑可显式给；调度路径不给 → 经 Activity 现取（沙箱外才有时区数据）。
    if req.get("trade_date") and req.get("now"):
        trade_date, now_iso = req["trade_date"], req["now"]
    else:
        clock = await _market_clock(market)
        trade_date = req.get("trade_date") or clock["trade_date"]
        now_iso = req.get("now") or clock["now"]
    at = req.get("at") or ""
    point_key = req.get("point") or f"{market}-{at.replace(':', '')}"
    summary: dict = {"point": point_key, "market": market, "at": at,
                     "trade_date": trade_date, "kind": kind}

    # ① 交易日历（preopen 先同步一次，其余时点直接读）—— **按该市场**
    if kind == "preopen":
        await ctl._gate()
        summary["calendar_sync"] = await _exec(
            activities.sync_calendar,
            {"trade_date": trade_date, "market": market,
             "lookback_days": req.get("lookback_days", 5),
             "lookahead_days": req.get("lookahead_days", 12)},
        )

    await ctl._gate()
    cal = await _exec(activities.read_calendar, {"trade_date": trade_date, "market": market})
    summary["calendar"] = cal
    action, why = gating.gate(cal)
    if action == gating.SKIP_UNKNOWN:
        # 日历缺数据 → **不当交易日**并如实告警（`05 §3.2` M-14 验收项）。
        summary["status"] = action
        summary["warning"] = f"{market} {trade_date} {why}"
        workflow.logger.warning(summary["warning"])
        return summary
    if action == gating.SKIP_NON_TRADING:
        summary["status"] = action
        summary["note"] = f"{market} {trade_date} 非交易日（{why}），未触发"
        workflow.logger.info(summary["note"])
        return summary

    # ② 对该市场进行中的项目跑这个时点
    await ctl._gate()
    projects = await _exec(activities.list_active_projects, {})
    projects = projects_for_market(projects, market)
    # 只跑指定项目：**补跑 / 故障注入验收**用（调度永远不带这个字段，
    # 所以生产路径逐字节不变）。
    only = req.get("project_id")
    if only:
        projects = filter_projects(projects, only)
        summary["filtered_project"] = only
    summary["projects"] = []
    for project in projects:
        summary["projects"].append(
            await _run_for_project(kind, point_key, at, market, project,
                                   trade_date, now_iso, req, ctl)
        )
    summary["status"] = "ok"
    summary["active_projects"] = len(projects)
    return summary


async def _run_for_project(kind, point_key, at, market, project, trade_date, now_iso, req,
                           ctl: "_PausableWorkflow") -> dict:
    project_id = project["project_id"]
    out: dict = {"project_id": project_id, "point": point_key, "market": market}

    # 业务检查点：登记 + 标 RUNNING（同键幂等，重启重放拿到同一个 job_id）
    await ctl._gate()
    job = await _exec(activities.begin_point_job, {
        "project_id": project_id, "trade_date": trade_date,
        "point": point_key, "at": at, "now": now_iso,
    })
    job_id = job["job_id"]
    # L08：把「当前长任务」记在工作流实例上 —— 控制通道 `cancel` 会先查它、再一并取消（§10.4）。
    ctl._track_job(job_id)
    out["job_id"] = job_id

    await ctl._gate()                       # 暂停检查点：暂停时停在这里（工作流仍 RUNNING）
    if kind == "preopen":
        out["confirm_t1"] = await _exec(activities.confirm_t1, {
            "project_id": project_id, "market": market,
        })
        await _exec(activities.write_checkpoint, {
            "job_id": job_id,
            "checkpoint": {"point": point_key, "phase": "t1_confirmed",
                           "positions_made_sellable": out["confirm_t1"].get("positions_made_sellable")},
        })
    elif kind == "decide":
        # ⓪ R3 · **决策前先冻结经验集**（只读 Activity，走 `memory.query(freeze=true)`）。
        #    结果（`memory_snapshot_id` + 命中的 items）进 Temporal 历史 —— 重放读的是
        #    历史里那份，所以「这笔决定当时看到的是哪一版经验」逐字节可复现（§10.3）。
        #    ⚠️ fail-closed：读不到经验集就让这一步失败重试，**不放过**这道闸门。
        mem = await _exec(activities.freeze_memory, {
            "project_id": project_id, "market": market, "trade_date": trade_date,
            "point": point_key, "now": now_iso, "purpose": "decision",
        })
        out["memory_snapshot_id"] = mem.get("memory_snapshot_id")
        # ① 出决定（只读 Activity）。结果进 Temporal 历史 —— 重试时读的是历史里那份。
        built = await _exec(activities.build_decision, {
            "project_id": project_id, "trade_date": trade_date, "point": point_key,
            "now": now_iso, "code": req.get("code"), "market": market,
            "memory": mem,
        })
        if built.get("halted"):
            # ── M6 · 总开关关闭 / R3 · 经验闸门 / 买不起：**这一步就到头，不提交任何委托** ──
            # 三种「如实记不下单」共用这一条路径；R3 多带两样留痕：依据的经验 id 与当时的快照 id。
            out["halted"] = True
            out["reason"] = built.get("reason")
            out["memory"] = built.get("memory")
            await _exec(activities.write_checkpoint, {
                "job_id": job_id,
                "checkpoint": {"point": point_key, "phase": "halted",
                               "reason": built.get("reason"),
                               "memory_snapshot_id": mem.get("memory_snapshot_id"),
                               "memory_blocked_by": (built.get("memory") or {}).get("blocked_by")},
            })
            out["finish"] = await _exec(activities.finish_point_job, {
                "job_id": job_id,
                "result_ref": f"point:{point_key}:{trade_date}:{project_id}:halted",
                # 收尾这一步**覆盖** `fin_job.checkpoint`（只留最后一次），所以依据也带上 ——
                # 事后翻 `fin_job.checkpoint` 一处就能看到「为什么没下单、依据哪一条经验」。
                "checkpoint": {"point": point_key, "trade_date": trade_date,
                               "phase": "done", "halted": True,
                               "reason": built.get("reason"),
                               "memory_snapshot_id": mem.get("memory_snapshot_id"),
                               "memory_blocked_by": (built.get("memory") or {}).get("blocked_by")},
            })
            ctl._track_job(None)
            return out
        out["decision"] = built["decision"]
        out["idempotency_key"] = built["idempotency_key"]
        # ② 提交（有副作用的 Activity）。命令是冻结的，重试逐字节相同 → 幂等重放。
        await ctl._gate()
        submitted = await _exec(activities.submit_decision, {
            "command": built["command"], "idempotency_key": built["idempotency_key"],
            "project_id": project_id, "point": point_key,
            "hold_seconds": req.get("hold_seconds", 0),
        }, timeout=DECIDE_TIMEOUT, heartbeat=DECIDE_HEARTBEAT)
        out["submit"] = submitted
        await _exec(activities.write_checkpoint, {
            "job_id": job_id,
            "checkpoint": {"point": point_key, "phase": "ordered",
                           "order_status": submitted.get("order_status"),
                           "trade_id": submitted.get("trade_id"),
                           "idempotency_key": built["idempotency_key"],
                           # R3 · §10.3「决策上下文补齐 memory_snapshot_id」—— 不新增表，
                           # 就落在 `fin_job.checkpoint` 里（出单与不出单都写）。
                           "memory_snapshot_id": mem.get("memory_snapshot_id"),
                           "memory_experience_count": len(mem.get("items") or [])},
        })
    elif kind == "match":
        await ctl._gate()
        out["match_open"] = await _exec(activities.match_open_orders, {
            "project_id": project_id, "market": market,
        })
    elif kind == "close":
        await ctl._gate()
        out["close"] = await _exec(activities.close_day, {
            "project_id": project_id, "now": now_iso, "market": market,
        })
        # M5 · 报告生成（M-17）接在**收盘估值之后**：报告的事实层读的正是刚写下的
        # 那次收盘估值（`fin_valuation`）。加在 `close` 分支的**末尾**（M4 交接的
        # replay 提示）：只对「部署之后新起的工作流」生效。
        if req.get("report", True):
            out["report"] = await _exec(
                activities.generate_daily_report,
                {"project_id": project_id, "trade_date": trade_date, "now": now_iso},
                timeout=REPORT_TIMEOUT,
            )

    await ctl._gate()
    out["finish"] = await _exec(activities.finish_point_job, {
        "job_id": job_id,
        "result_ref": f"point:{point_key}:{trade_date}:{project_id}",
        "checkpoint": {"point": point_key, "trade_date": trade_date, "phase": "done",
                       "result": out},
    })
    ctl._track_job(None)
    return out


# ── 六个角色（六个 Workflow 类型；市场经 args 传入）─────────────────────────
@workflow.defn(name="fin.point_preopen")
class PointPreopenWorkflow(_PausableWorkflow):
    @workflow.run
    async def run(self, req: dict | None = None) -> dict:
        return await _run_point("preopen", req or {}, self)


@workflow.defn(name="fin.point_decide")
class PointDecideWorkflow(_PausableWorkflow):
    @workflow.run
    async def run(self, req: dict | None = None) -> dict:
        return await _run_point("decide", req or {}, self)


@workflow.defn(name="fin.point_match_a")
class PointMatchAWorkflow(_PausableWorkflow):
    @workflow.run
    async def run(self, req: dict | None = None) -> dict:
        return await _run_point("match_a", req or {}, self)


@workflow.defn(name="fin.point_match_b")
class PointMatchBWorkflow(_PausableWorkflow):
    @workflow.run
    async def run(self, req: dict | None = None) -> dict:
        return await _run_point("match_b", req or {}, self)


@workflow.defn(name="fin.point_match_c")
class PointMatchCWorkflow(_PausableWorkflow):
    @workflow.run
    async def run(self, req: dict | None = None) -> dict:
        return await _run_point("match_c", req or {}, self)


@workflow.defn(name="fin.point_close")
class PointCloseWorkflow(_PausableWorkflow):
    @workflow.run
    async def run(self, req: dict | None = None) -> dict:
        return await _run_point("close", req or {}, self)


POINT_WORKFLOWS = (
    PointPreopenWorkflow, PointDecideWorkflow, PointMatchAWorkflow,
    PointMatchBWorkflow, PointMatchCWorkflow, PointCloseWorkflow,
)


# ── 复核（复盘）回路（R3 · `plan/R3.md` §一.1）──────────────────────────────
# 收盘后（各市场**时段末点 + 可配延迟**，见 `schedules.review_specs`）跑一次：
#
#   ① `review_collect`（只读）→ ② `review_propose`（模型只写文字 + 回读校验）
#   → ③ `review_append`（经 Memory Service 唯一写入口落库）
#
# 三步是**三个 Activity**（不是一个大活动）：只读的两步重放没有代价，
# 有副作用的只有第三步，幂等也只需要在那里守（`activities.review_append`）。
#
# **工作流里读经验 = 只能读 `memory.query` 的结果**（§一.5 的不变量）：
# 这里一行 SQL 都没有、也不 import 任何数据库驱动（`test_no_ledger_access.py` 盯着）。
#
# 它是**全局的、与项目数无关**的 Schedule（同 `二期迭代完善 §4.3` 口径）：
# 一次触发对**该市场所有进行中的项目**各复盘一次，某项目没数据就跳过它。
REVIEW_TIMEOUT = timedelta(minutes=5)


async def _review_for_project(project: dict, market: str, trade_date: str,
                              now_iso: str) -> dict:
    """一个项目的一次复盘。没有可复盘的当日数据（无成交且无报告）→ 如实跳过。"""
    project_id = project["project_id"]
    out: dict = {"project_id": project_id, "market": market, "trade_date": trade_date}

    collected = await _exec(activities.review_collect, {
        "project_id": project_id, "trade_date": trade_date, "market": market,
    })
    out["has_report"] = collected.get("report") is not None
    out["trade_count"] = len(collected.get("trades") or [])
    out["fact_count"] = len(collected.get("facts") or [])
    if not out["trade_count"] and not out["has_report"]:
        # 既没有成交也没有报告 —— **不硬写一条空经验**（「空的比假的好」）。
        out["skipped"] = "no_data"
        workflow.logger.info("[review] {} 无当日数据，跳过", project_id)
        return out

    proposed = await _exec(activities.review_propose, {
        "project_id": project_id, "trade_date": trade_date, "market": market,
    }, timeout=REVIEW_TIMEOUT)
    out["candidates"] = len(proposed.get("candidates") or [])
    out["used_fallback"] = bool(proposed.get("used_fallback"))
    out["fallback_reason"] = proposed.get("reason")
    out["rejected"] = proposed.get("rejected") or []

    appended = await _exec(activities.review_append, {
        "project_id": project_id, "trade_date": trade_date, "market": market,
        "candidates": proposed.get("candidates") or [],
        # 经验的 `as_of` = 复盘那一刻（这条认知是这时形成的）——
        # 于是它进不了比它更早冻结的决策快照（时间边界由服务端执行）。
        "as_of": now_iso,
        "used_fallback": proposed.get("used_fallback"),
        "fallback_reason": proposed.get("reason"),
    })
    out["written"] = appended.get("written") or []
    out["skipped_existing"] = appended.get("skipped") or []
    return out


@workflow.defn(name="fin.review")
class ReviewWorkflow(_PausableWorkflow):
    """收盘后复核工作流（第四段 R3 · 方案 §8 的复盘回路）。

    **市场经 Schedule 的 `args` 传进来**（同六个时点角色，见模块头）：
    每个市场一条 Schedule（`fin-review-<market>`），时点 = 该市场时段末点 + 可配延迟。
    某市场非交易日 / 日历缺失 → 空跑并记明原因，**其他市场照常**。
    """

    @workflow.run
    async def run(self, req: dict | None = None) -> dict:
        req = req or {}
        market = canonical(req.get("market") or "CN_A")
        if req.get("trade_date") and req.get("now"):
            trade_date, now_iso = req["trade_date"], req["now"]
        else:
            clock = await _market_clock(market)
            trade_date = req.get("trade_date") or clock["trade_date"]
            now_iso = req.get("now") or clock["now"]
        summary: dict = {"market": market, "trade_date": trade_date,
                         "point": req.get("point") or "review", "kind": "review"}

        cal = await _exec(activities.read_calendar, {"trade_date": trade_date, "market": market})
        summary["calendar"] = cal
        action, why = gating.gate(cal)
        if action == gating.SKIP_UNKNOWN:
            summary["status"] = action
            summary["warning"] = f"{market} {trade_date} {why}"
            workflow.logger.warning(summary["warning"])
            return summary
        if action == gating.SKIP_NON_TRADING:
            summary["status"] = action
            summary["note"] = f"{market} {trade_date} 非交易日（{why}），未复核"
            workflow.logger.info(summary["note"])
            return summary

        await self._gate()
        projects = await _exec(activities.list_active_projects, {})
        projects = projects_for_market(projects, market)
        only = req.get("project_id")
        if only:
            projects = filter_projects(projects, only)
            summary["filtered_project"] = only
        summary["projects"] = []
        for project in projects:
            summary["projects"].append(
                await _review_for_project(project, market, trade_date, now_iso)
            )
        summary["status"] = "ok"
        summary["active_projects"] = len(projects)
        return summary


# ── K 线 ETL 触发（「谁决定什么时候拉数据」）────────────────────────────────
@workflow.defn(name="fin.market_etl")
class MarketEtlWorkflow(_PausableWorkflow):
    """由 Temporal 决定「什么时候拉 K 线」。**取数实现原样不动**（api 侧复用）。

    **触发前先查该市场自己的交易日历**（N4）：`cron` 只表达「周一到周五」，
    节假日由 `fin_market_calendar` 挡 —— 三个市场各自按自己的日历挡，
    **不再用「周一至周五」近似**（那是拿日历当儿戏，`二期追加规则 §三`）。
    日历缺失（没有那一行）→ 当日不跑并写明原因，**其他市场照常**。
    """

    @workflow.run
    async def run(self, req: dict) -> dict:
        await self._gate()                               # L08 暂停检查点
        market_key = req["market"]                       # cn / hk / us（ETL 通道写法）
        market = canonical(market_key)                   # CN_A / HK / US
        if req.get("trade_date") and req.get("now"):
            trade_date, now_iso = req["trade_date"], req["now"]
        else:
            clock = await _market_clock(market)
            trade_date = req.get("trade_date") or clock["trade_date"]
            now_iso = req.get("now") or clock["now"]

        cal = await _exec(activities.read_calendar, {"trade_date": trade_date, "market": market})
        action, why = gating.gate(cal)
        if action != gating.PROCEED:
            return {"market": market_key, "trade_date": trade_date, "status": action,
                    "warning": f"{market} {trade_date} {why}，未触发 {market_key} ETL"}

        result = await _exec(
            activities.trigger_market_etl,
            {"market": market_key, "limit": req.get("limit"), "bars": req.get("bars")},
            timeout=ETL_TIMEOUT,
        )
        result.update({"trade_date": trade_date, "now": now_iso, "status": "triggered"})
        return result


# ── 标的元数据同步（M7 · M-20）────────────────────────────────────────────
@workflow.defn(name="fin.instrument_sync")
class InstrumentSyncWorkflow(_PausableWorkflow):
    """每晚把某市场标的的元数据同步进 `fin_instrument`。

    独立成一个工作流类型（不是塞进某个时点）：它是**参考数据**的刷新，
    与「今天要不要交易」无关，也不该因为某个交易日不是该市场交易日就跳过 ——
    master 数据随时会变（新股上市、戴帽摘帽）。所以调度是「每天一次」，
    内层自己按市场过滤。
    """

    @workflow.run
    async def run(self, req: dict | None = None) -> dict:
        req = req or {}
        await self._gate()                               # L08 暂停检查点
        return await _exec(
            activities.sync_instruments,
            {"market": req.get("market", "cn"), "codes": req.get("codes")},
            timeout=ACT_TIMEOUT,
        )


# ── 影子验证（R7 · `plan/R7.md` §一.3）─────────────────────────────────────
# 收盘后（各市场**时段末点 + 复核延迟 + 影子延迟**，见 `schedules.shadow_specs`）跑一次：
#
#   对每一个**待验证**的提案（`status ∈ {draft, validating}`）：
#     在该市场**每个已有时点**上 → ① 取一次行情快照 → ② 两臂各跑一次 `build_decision`
#     → ③ 交 paper 影子撮合（只算不记）→ ④ 写两行影子事件（api 落库）
#   → 全部时点跑完后做一次**数据校验与估值判定**。
#
# **两臂同条件**（红线 11）由活动保证：同一个策略、同一个行情快照（paper 侧取一次）、
# 同费率 / 滑点 / 停牌 / 撮合（都复用 `paper` 的实现）。**零订单出口**（红线 12）：
# 影子成交只进影子事件表，`paper` 的影子路径只依赖 `DecisionRecorder`。
#
# 它是**全局的、与项目数无关**的 Schedule（同 `fin.review`）：一次触发对**该市场所有
# 进行中的项目**下每个待验证提案各验证一遍。
SHADOW_TIMEOUT = timedelta(minutes=5)


@workflow.defn(name="fin.shadow")
class ShadowWorkflow(_PausableWorkflow):
    """影子验证工作流（第四段 R7 · 方案 §4-C）。

    **市场经 Schedule 的 `args` 传进来**（同六个时点角色 / `fin.review`）：
    每个市场一条 Schedule（`fin-shadow-<market>`）。某市场非交易日 / 日历缺失 → 空跑
    并记明原因，**其他市场照常**。**不改现有 6 个 point 时点、不改 `fin-review-<market>`。**
    """

    @workflow.run
    async def run(self, req: dict | None = None) -> dict:
        req = req or {}
        market = canonical(req.get("market") or "CN_A")
        if req.get("trade_date") and req.get("now"):
            trade_date, now_iso = req["trade_date"], req["now"]
        else:
            clock = await _market_clock(market)
            trade_date = req.get("trade_date") or clock["trade_date"]
            now_iso = req.get("now") or clock["now"]
        summary: dict = {"market": market, "trade_date": trade_date,
                         "point": req.get("point") or "shadow", "kind": "shadow"}

        cal = await _exec(activities.read_calendar, {"trade_date": trade_date, "market": market})
        summary["calendar"] = cal
        action, why = gating.gate(cal)
        if action == gating.SKIP_UNKNOWN:
            summary["status"] = action
            summary["warning"] = f"{market} {trade_date} {why}"
            workflow.logger.warning(summary["warning"])
            return summary
        if action == gating.SKIP_NON_TRADING:
            summary["status"] = action
            summary["note"] = f"{market} {trade_date} 非交易日（{why}），未验证"
            workflow.logger.info(summary["note"])
            return summary

        points = await _exec(activities.market_points, {"market": market})
        summary["points"] = [p["point"] for p in points]
        await self._gate()
        projects = await _exec(activities.list_active_projects, {})
        projects = projects_for_market(projects, market)
        only = req.get("project_id")
        if only:
            projects = filter_projects(projects, only)
            summary["filtered_project"] = only

        summary["projects"] = []
        for project in projects:
            summary["projects"].append(
                await _shadow_for_project(project, market, trade_date, now_iso, points, req)
            )
        summary["status"] = "ok"
        summary["active_projects"] = len(projects)
        return summary


async def _shadow_for_project(project: dict, market: str, trade_date: str, now_iso: str,
                              points: list[dict], req: dict) -> dict:
    """一个项目下的每个待验证提案各跑一遍影子，并在跑完后做一次判定。"""
    project_id = project["project_id"]
    out: dict = {"project_id": project_id, "proposals": []}
    proposals = await _exec(activities.shadow_proposals, {"project_id": project_id})
    for prop in proposals:
        proposal_id = prop["proposal_id"]
        rec: dict = {"proposal_id": proposal_id, "status": prop.get("status"), "steps": []}
        for p in points:
            rec["steps"].append(await _exec(activities.shadow_step, {
                "project_id": project_id, "proposal_id": proposal_id, "market": market,
                "trade_date": trade_date, "point": p["point"], "now": now_iso,
                "code": req.get("code"),
            }, timeout=SHADOW_TIMEOUT))
        rec["evaluate"] = await _exec(activities.shadow_evaluate, {
            "proposal_id": proposal_id, "market": market, "trade_date": trade_date,
        }, timeout=SHADOW_TIMEOUT)
        out["proposals"].append(rec)
    return out


# ── 自动盯盘（R13 · `plan/R13.md` §A）─────────────────────────────────────
# 收盘后 + 复核 + 影子之后（见 `schedules.observe_specs`）跑一次：
#
#   对每一个**已生效**的提案 → 观察一次（`observe_applied`）：
#     按**原冻结计划**算实盘净值表现 → 越普通线只告警（配置不动）、
#     踩紧急线才自动回滚（回滚一并产出 `polarity='refute'` 失败经验）。
#
# 判断全在服务端 —— 工作流只负责「到点、按市场、逐提案」地调用它。
# **零订单出口**：观察只读模拟盘净值，回滚只动 `fin_param`（系统唯一自主改配置的场合）。
OBSERVE_TIMEOUT = timedelta(minutes=5)


async def _observe_for_project(project: dict, market: str, trade_date: str,
                               now_iso: str, req: dict) -> dict:
    """一个项目下的每个已生效提案各观察一次。

    **单条失败不挂整条工作流**（红线：观察是例行盯盘，一条查询错不该让别的项目不观察）——
    逐提案 catch、把失败记进汇总继续下一个。但**回滚失败必须可见**：回滚是系统唯一
    自主改 `fin_param` 的场合（红线 9），所以回滚失败额外打 `ERROR` 日志并标 `rollback_error`。
    """
    project_id = project["project_id"]
    out: dict = {"project_id": project_id, "market": market, "proposals": []}
    proposals = await _exec(activities.observe_proposals, {"project_id": project_id})
    as_of = req.get("as_of") or trade_date
    for prop in proposals:
        proposal_id = prop["proposal_id"]
        rec: dict = {"proposal_id": proposal_id, "status": prop.get("status")}
        try:
            obs = await _exec(activities.observe_applied, {
                "proposal_id": proposal_id, "market": market, "as_of": as_of,
            }, timeout=OBSERVE_TIMEOUT)
        except Exception as exc:                        # noqa: BLE001 —— 单条失败不挂工作流
            # api 侧把「回滚核验失败」翻成 409、把「进化未启用 / 硬开关违规」翻成 503；
            # 这两类都只在**回滚那一支**出现，标出来让人一眼看到「回滚没成功」。
            status = getattr(exc, "status", None)
            rec["observed"] = False
            rec["error"] = f"{type(exc).__name__}: {exc}"
            if status in (409, 503):
                rec["rollback_error"] = True
                workflow.logger.error(
                    "[observe] {} 回滚未成功（HTTP {}）—— 必须人工确认：{}",
                    proposal_id, status, exc)
            else:
                workflow.logger.error("[observe] {} 观察失败：{}", proposal_id, exc)
            out["proposals"].append(rec)
            continue
        rec["observed"] = obs.get("observed")
        rec["action"] = obs.get("action")
        rec["reason"] = obs.get("reason")
        rec["live"] = obs.get("live")
        rec["rollback_line"] = obs.get("rollback_line")
        rec["fail_line"] = obs.get("fail_line")
        if obs.get("action") == "rolled_back":
            rec["rollback"] = obs.get("rollback")
        out["proposals"].append(rec)
    return out


@workflow.defn(name="fin.observe")
class ObserveWorkflow(_PausableWorkflow):
    """自动盯盘工作流（第四段 R13 · 方案 §A）。

    **市场经 Schedule 的 `args` 传进来**（同六个时点角色 / `fin.review` / `fin.shadow`）：
    每个市场一条 Schedule（`fin-observe-<market>`），时点 = 该市场时段末点 + 复核延迟
    + 影子延迟 + 观察延迟（排在 `fin-shadow-<market>` 之后，**不改现有任何一条 Schedule**）。
    某市场非交易日 / 日历缺失 → 空跑并记明原因，**其他市场照常**。

    内层对「该市场**所有进行中的项目**下每个**已生效**的提案」各观察一次。
    **观察对象是「已生效」，不是「待验证」** —— 待验证由 `fin.shadow` 管，两者不重叠。
    行为（只告警 / 才回滚）**逐字复用服务层**，这里一个字都不改判据。
    """

    @workflow.run
    async def run(self, req: dict | None = None) -> dict:
        req = req or {}
        market = canonical(req.get("market") or "CN_A")
        if req.get("trade_date") and req.get("now"):
            trade_date, now_iso = req["trade_date"], req["now"]
        else:
            clock = await _market_clock(market)
            trade_date = req.get("trade_date") or clock["trade_date"]
            now_iso = req.get("now") or clock["now"]
        summary: dict = {"market": market, "trade_date": trade_date,
                         "point": req.get("point") or "observe", "kind": "observe"}

        cal = await _exec(activities.read_calendar, {"trade_date": trade_date, "market": market})
        summary["calendar"] = cal
        action, why = gating.gate(cal)
        if action == gating.SKIP_UNKNOWN:
            summary["status"] = action
            summary["warning"] = f"{market} {trade_date} {why}"
            workflow.logger.warning(summary["warning"])
            return summary
        if action == gating.SKIP_NON_TRADING:
            summary["status"] = action
            summary["note"] = f"{market} {trade_date} 非交易日（{why}），未观察"
            workflow.logger.info(summary["note"])
            return summary

        await self._gate()
        projects = await _exec(activities.list_active_projects, {})
        projects = projects_for_market(projects, market)
        only = req.get("project_id")
        if only:
            projects = filter_projects(projects, only)
            summary["filtered_project"] = only

        summary["projects"] = []
        for project in projects:
            summary["projects"].append(
                await _observe_for_project(project, market, trade_date, now_iso, req)
            )
        summary["status"] = "ok"
        summary["active_projects"] = len(projects)
        return summary


# ── 自动提案（L01 · `plan/L01.md` §3.1）────────────────────────────────────
# 收盘后（各市场**时段末点 + 复核延迟 + 提案延迟**，见 `schedules.propose_specs`）跑一次：
#
#   对每一个**进行中的项目**：① 读候选（`propose_candidates`，只读；开关 / 预算 / 证据闸门
#   都在 api 侧算）→ ② 逐条走**唯一写入口**（`propose_submit`）落库。
#
# **顺序**：在 `fin.review`（产经验）**之后**、`fin.shadow`（验提案）**之前** ——
# 复盘产经验 → 提案 → 影子验提案，这是整条链子的时间序。
#
# **确定性编排，零 LLM**（方案 §5.2）：工作流只负责「到点、按市场、逐项目」地调用与留痕；
# 聚什么经验、提哪个字段全在 api 的 `services/fin/evolution.py`。
# **不自动生效**：`FIN_AUTO_APPLY` 恒 0 —— 这一步只产出 `draft` 提案，生效仍要人工 `confirm`。
PROPOSE_TIMEOUT = timedelta(minutes=5)


async def _propose_for_project(project: dict, market: str, trade_date: str,
                               now_iso: str) -> dict:
    """一个项目的一次自动提案。**逐条 catch**：一条候选失败（撞唯一索引 / 闸门拒绝）
    不该挂掉别的候选，也不该挂掉别的项目 —— 失败如实记进汇总。"""
    project_id = project["project_id"]
    out: dict = {"project_id": project_id, "market": market, "proposals": []}

    cand = await _exec(activities.propose_candidates,
                       {"project_id": project_id, "market": market}, timeout=PROPOSE_TIMEOUT)
    out["action"] = cand.get("action")
    out["reason"] = cand.get("reason")
    out["budget"] = cand.get("budget")
    if cand.get("action") != "propose":
        # 关着 / 超预算 / 证据不足 —— **如实记「不提」与原因**（不是静默跳过）。
        workflow.logger.info("[propose] {} 不提：{}", project_id, cand.get("reason"))
        return out

    for draft in cand.get("drafts") or []:
        rec: dict = {"proposal_id": draft.get("proposal_id"),
                     "evidence_count": len(draft.get("evidence_refs") or [])}
        try:
            res = await _exec(activities.propose_submit, {"draft": draft},
                              timeout=PROPOSE_TIMEOUT)
        except Exception as exc:                            # noqa: BLE001 —— 单条失败不挂工作流
            rec["ok"] = False
            rec["error"] = f"{type(exc).__name__}: {exc}"
            workflow.logger.warning("[propose] {} 候选 {} 落库失败：{}",
                                    project_id, draft.get("proposal_id"), exc)
            out["proposals"].append(rec)
            continue
        prop = res.get("proposal") or {}
        rec["ok"] = True
        rec["saved_id"] = prop.get("proposal_id")
        rec["target"] = prop.get("target")
        rec["direction"] = prop.get("direction")
        out["proposals"].append(rec)
    return out


@workflow.defn(name="fin.propose")
class ProposeWorkflow(_PausableWorkflow):
    """自动提案工作流（L01 · 把「经验 → 提案」这一节接上）。

    **市场经 Schedule 的 `args` 传进来**（同 `fin.review` / `fin.shadow` / `fin.observe`）：
    每个市场一条 Schedule（`fin-propose-<market>`），时点 = 该市场时段末点 + 复核延迟
    + 提案延迟 —— 排在 `fin-review-<market>`（产经验）之后、`fin-shadow-<market>`（验提案）之前。
    某市场非交易日 / 日历缺失 → 空跑并记明原因，**其他市场照常**。

    内层对「该市场**所有进行中的项目**」各读一次候选、逐条经唯一写入口落库。
    **只自动「提」，不自动「生效」**（`FIN_AUTO_APPLY` 恒 0）。
    """

    @workflow.run
    async def run(self, req: dict | None = None) -> dict:
        req = req or {}
        market = canonical(req.get("market") or "CN_A")
        if req.get("trade_date") and req.get("now"):
            trade_date, now_iso = req["trade_date"], req["now"]
        else:
            clock = await _market_clock(market)
            trade_date = req.get("trade_date") or clock["trade_date"]
            now_iso = req.get("now") or clock["now"]
        summary: dict = {"market": market, "trade_date": trade_date,
                         "point": req.get("point") or "propose", "kind": "propose"}

        cal = await _exec(activities.read_calendar, {"trade_date": trade_date, "market": market})
        summary["calendar"] = cal
        action, why = gating.gate(cal)
        if action == gating.SKIP_UNKNOWN:
            summary["status"] = action
            summary["warning"] = f"{market} {trade_date} {why}"
            workflow.logger.warning(summary["warning"])
            return summary
        if action == gating.SKIP_NON_TRADING:
            summary["status"] = action
            summary["note"] = f"{market} {trade_date} 非交易日（{why}），未提案"
            workflow.logger.info(summary["note"])
            return summary

        await self._gate()
        projects = await _exec(activities.list_active_projects, {})
        projects = projects_for_market(projects, market)
        only = req.get("project_id")
        if only:
            projects = filter_projects(projects, only)
            summary["filtered_project"] = only

        summary["projects"] = []
        for project in projects:
            summary["projects"].append(
                await _propose_for_project(project, market, trade_date, now_iso)
            )
        summary["status"] = "ok"
        summary["active_projects"] = len(projects)
        return summary


ALL_WORKFLOWS = POINT_WORKFLOWS + (MarketEtlWorkflow, InstrumentSyncWorkflow,
                                   ReviewWorkflow, ShadowWorkflow, ObserveWorkflow,
                                   ProposeWorkflow)
