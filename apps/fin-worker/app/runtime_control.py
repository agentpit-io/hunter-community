"""运行时控制通道 —— `runtime.workflow_start/get/pause/cancel`（L08 · 五期方案 §5.1 / §6.3 / §10.1 / §10.4）。

**这是什么**：Temporal 的唯一调度权威（`01方案 §5.3`）不变，本模块只是**操作 Temporal 的手** ——
启动 / 查询 / 暂停 / 取消工作流。**它自己一行调度都不另起**（不建 cron、不建线程定时器），
**也不自己维护工作流状态**（状态从 Temporal 现查）。

**为什么它不是后门**（本段第一红线）：

- `workflow_start` **只认白名单模板名**（`TEMPLATES` 的键 = 已注册的工作流类型），
  **参数按类型逐个校验**（未知参数名一律拒绝）。**不接受**任何形式的
  「任意代码 / 任意 shell / 任意工作流名」—— 模板名不在白名单里就是 404，参数越界就是 400。
- 三个非启动入口都要求目标**已经存在**（Temporal 里查得到），不存在就 404。
- 四个入口一律走 fin-worker 的**内部口令**（`X-Hunter-Internal-Key` == 读取凭证），
  端口只绑 `127.0.0.1`（`docker-compose.yml` fin-worker 的 `ports`），**不放公网**。

**`pause` 是「暂停正在跑的工作流」**（协作式）：往工作流发 `pause` 信号，工作流在下一个
检查点（`workflows._PausableWorkflow._gate`）停下并 `wait_condition` 等 `resume`。
**`cancel` 是 Temporal 原生的取消**（`handle.cancel()`），并把该工作流当前登记的
长任务（`fin_job`）一并取消（`paper` 的 `/api/v1/jobs/{id}/cancel`）——
这就是 `§10.4` 的「可请求取消」补上的那一半。

**幂等**（`§1.1` 要求）：`cancel` 两次第二次不报错（Temporal 原生对已结束的工作流重复取消是空操作，
本模块再显式判终态）；`pause` 重复发就是重复置位；对**不存在**的目标才报 404。
"""

from __future__ import annotations

import re
from typing import Any, Optional

# ── 模板白名单 ────────────────────────────────────────────────────────────
# 键 = Temporal 工作流类型名（与 `workflows.ALL_WORKFLOWS` 逐一对应）。**这就是允许启动的全部。**
# 值 = 该模板允许的参数：`名称 -> (类型, 是否必填)`。类型见 `_check_value`。
#
# 加新模板时：先在 `workflows.py` 注册工作流类型，再在这里登记一行（并想清楚哪些参数能开给外部）。
_MARKET = ("CN_A", "HK", "US")
_ETL_MARKET = ("cn", "hk", "us")          # K 线 ETL / 标的同步通道用的是小写三字母（见 MarketEtlWorkflow）

# 六个时点角色共用同一套参数（角色差异只用必填项表达）。
_POINT_PARAMS: dict[str, tuple[str, bool]] = {
    "market": ("market", True),
    "trade_date": ("date", False),
    "at": ("hhmm", False),
    "point": ("str", False),
    "now": ("str", False),
    "project_id": ("str", False),
    "code": ("str", False),
    "hold_seconds": ("int", False),
    "lookback_days": ("int", False),
    "lookahead_days": ("int", False),
    "report": ("bool", False),
}

TEMPLATES: dict[str, dict[str, tuple[str, bool]]] = {
    "fin.point_preopen": dict(_POINT_PARAMS),
    "fin.point_decide": dict(_POINT_PARAMS),
    "fin.point_match_a": dict(_POINT_PARAMS),
    "fin.point_match_b": dict(_POINT_PARAMS),
    "fin.point_match_c": dict(_POINT_PARAMS),
    "fin.point_close": dict(_POINT_PARAMS),
    "fin.market_etl": {
        "market": ("etl_market", True),
        "trade_date": ("date", False),
        "limit": ("int", False),
        "bars": ("int", False),
        "now": ("str", False),
    },
    "fin.instrument_sync": {
        "market": ("etl_market", False),
        "codes": ("str_list", False),
    },
    "fin.review": {
        "market": ("market", True),
        "trade_date": ("date", False), "at": ("hhmm", False), "point": ("str", False),
        "now": ("str", False), "project_id": ("str", False),
    },
    "fin.shadow": {
        "market": ("market", True),
        "trade_date": ("date", False), "at": ("hhmm", False), "point": ("str", False),
        "now": ("str", False), "project_id": ("str", False), "code": ("str", False),
    },
    "fin.observe": {
        "market": ("market", True),
        "trade_date": ("date", False), "at": ("hhmm", False), "point": ("str", False),
        "now": ("str", False), "project_id": ("str", False), "as_of": ("date", False),
    },
    "fin.propose": {
        "market": ("market", True),
        "trade_date": ("date", False), "at": ("hhmm", False), "point": ("str", False),
        "now": ("str", False), "project_id": ("str", False),
    },
    # L09 · 采集补齐。市场用 ETL 那套小写三字母（`etl_market`）；`codes` 留空 = api 按
    # 「自选股 ∪ 系统标的」算清单，显式给用于排障 / 补采（**这是控制通道的正当用法**：
    # 只认模板名 + 类型化参数，不是「任意执行」）。
    "fin.news_collect": {
        "market": ("etl_market", False), "limit": ("int", False),
        "per_code": ("int", False), "codes": ("str_list", False),
    },
    "fin.fundamental_collect": {
        "market": ("etl_market", False), "limit": ("int", False),
        "keep_raw": ("bool", False), "codes": ("str_list", False),
    },
}

# 启动出来的工作流 id 前缀 —— 与调度（`fin-point-…`）/ 手工补跑（`fin-…`）一眼可分。
START_PREFIX = "fin-ctl-"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HHMM_RE = re.compile(r"^\d{2}:\d{2}$")


class ControlError(Exception):
    """控制通道的**预期拒绝**（白名单外 / 参数非法 / 目标不存在）。带 HTTP 状态码。"""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ── 参数校验（纯函数，不碰 Temporal，能单独测）──────────────────────────────
def _check_value(name: str, kind: str, value: Any) -> Any:
    if kind == "market":
        if value not in _MARKET:
            raise ControlError(400, f"参数 {name} 必须是 {list(_MARKET)} 之一，收到 {value!r}")
        return value
    if kind == "etl_market":
        if value not in _ETL_MARKET:
            raise ControlError(400, f"参数 {name} 必须是 {list(_ETL_MARKET)} 之一，收到 {value!r}")
        return value
    if kind == "date":
        if not isinstance(value, str) or not _DATE_RE.match(value):
            raise ControlError(400, f"参数 {name} 必须是 YYYY-MM-DD，收到 {value!r}")
        return value
    if kind == "hhmm":
        if not isinstance(value, str) or not _HHMM_RE.match(value):
            raise ControlError(400, f"参数 {name} 必须是 HH:MM，收到 {value!r}")
        return value
    if kind == "int":
        # bool 是 int 的子类 —— 显式挡掉，否则 True 会被当成 1
        if isinstance(value, bool) or not isinstance(value, int):
            raise ControlError(400, f"参数 {name} 必须是整数，收到 {value!r}")
        if value < 0 or value > 10_000_000:
            raise ControlError(400, f"参数 {name} 超出允许范围，收到 {value!r}")
        return value
    if kind == "bool":
        if not isinstance(value, bool):
            raise ControlError(400, f"参数 {name} 必须是布尔值，收到 {value!r}")
        return value
    if kind == "str_list":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ControlError(400, f"参数 {name} 必须是字符串数组，收到 {value!r}")
        if len(value) > 1000:
            raise ControlError(400, f"参数 {name} 元素过多（>{1000}）")
        return value
    if kind == "str":
        if not isinstance(value, str) or not value or len(value) > 200:
            raise ControlError(400, f"参数 {name} 必须是非空字符串（≤200 字），收到 {value!r}")
        return value
    raise ControlError(500, f"未知参数类型 {kind}")            # 代码写错，不是用户输入


def validate_start(template: str, params: Optional[dict]) -> dict:
    """校验一次 `workflow_start` 请求。**白名单 + 类型化参数**，两件事都在这里做。

    返回**归一化后的参数**（只含白名单里声明过的键）。任何越界都抛 `ControlError`。
    """
    spec = TEMPLATES.get(template)
    if spec is None:
        raise ControlError(
            404,
            f"未知工作流模板 {template!r}；允许的模板只有：{sorted(TEMPLATES)}",
        )
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ControlError(400, "params 必须是对象")
    unknown = sorted(set(params) - set(spec))
    if unknown:
        # 未知参数名一律拒绝 —— 静默丢弃等于「用户以为传了、其实没传」。
        raise ControlError(400, f"模板 {template} 不接受参数 {unknown}；允许的参数：{sorted(spec)}")
    out: dict[str, Any] = {}
    for name, (kind, required) in spec.items():
        if name not in params or params[name] is None:
            if required:
                raise ControlError(400, f"模板 {template} 缺必填参数 {name}")
            continue
        out[name] = _check_value(name, kind, params[name])
    return out


def derive_workflow_id(template: str, params: dict, workflow_id: Optional[str]) -> str:
    """没有显式给 id 时按「模板 + 市场 + 交易日」推一个稳定的 id。

    稳定 = 同一天同一模板重复启动会撞上同一个 id（`ALLOW_DUPLICATE_FAILED_ONLY` 会把
    重复启动拒成 409）—— 这是「同一件事不重复开跑」在编排层的一层保护，不是随机 id。
    """
    if workflow_id:
        return str(workflow_id)[:200]
    parts = [START_PREFIX + template]
    for key in ("market", "trade_date", "point"):
        if params.get(key):
            parts.append(str(params[key]))
    return "-".join(parts)


# ── Temporal 操作（异步；下面四个 `*_impl` 是控制通道的全部动作）──────────────
_STATUS_NAME = {
    1: "RUNNING", 2: "COMPLETED", 3: "FAILED", 4: "CANCELED",
    5: "TERMINATED", 6: "CONTINUED_AS_NEW", 7: "TIMED_OUT",
}
_TERMINAL_NAMES = frozenset({"COMPLETED", "FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"})


def _status_of(describe) -> str:
    return _STATUS_NAME.get(int(describe.status), f"STATUS_{int(describe.status)}")


async def start_impl(client, template: str, params: dict, workflow_id: str) -> dict:
    """启动一个白名单里的工作流。同 id 已在跑/已跑成功 → 409（与手工补跑同一口径）。"""
    from temporalio.common import WorkflowIDReusePolicy
    from temporalio.exceptions import WorkflowAlreadyStartedError
    from temporalio.service import RPCError

    from app import config

    try:
        handle = await client.start_workflow(
            template, params, id=workflow_id,
            task_queue=config.temporal_task_queue(),
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
        )
    except WorkflowAlreadyStartedError as exc:
        raise ControlError(409, f"工作流 {workflow_id} 已在跑或已跑过") from exc
    except RPCError as exc:
        raise ControlError(409, f"工作流 {workflow_id} 无法启动：{exc}") from exc
    return {"started": True, "template": template, "workflow_id": workflow_id,
            "run_id": handle.result_run_id, "params": params}


async def get_impl(client, *, workflow_id: Optional[str] = None,
                   run_id: Optional[str] = None, schedule_id: Optional[str] = None) -> dict:
    """查状态。**状态从 Temporal 现查**（不维护第二份）。

    给 `workflow_id` 查工作流执行；给 `schedule_id` 查调度（含 `paused`）。
    """
    from temporalio.service import RPCError

    if schedule_id:
        try:
            desc = await client.get_schedule_handle(schedule_id).describe()
        except RPCError as exc:
            raise ControlError(404, f"调度不存在：{schedule_id}（{exc}）") from exc
        st = desc.schedule.state
        return {"kind": "schedule", "schedule_id": schedule_id, "paused": bool(st.paused),
                "note": st.note or None, "num_actions": desc.info.num_actions,
                "next_action_times": [t.isoformat() for t in (desc.info.next_action_times or [])],
                "workflow": desc.schedule.action.workflow if hasattr(desc.schedule.action, "workflow") else None}

    if not workflow_id:
        raise ControlError(400, "必须给 workflow_id 或 schedule_id 之一")

    handle = client.get_workflow_handle(workflow_id, run_id=run_id)
    try:
        desc = await handle.describe()
    except RPCError as exc:
        raise ControlError(404, f"工作流不存在：{workflow_id}（{exc}）") from exc

    out: dict = {
        "kind": "workflow", "workflow_id": workflow_id, "run_id": desc.run_id,
        "status": _status_of(desc), "task_queue": desc.task_queue,
        "start_time": desc.start_time.isoformat() if desc.start_time else None,
        "close_time": desc.close_time.isoformat() if desc.close_time else None,
        "history_length": desc.history_length,
    }
    # 协作式暂停状态 / 当前长任务：工作流支持才查（老镜像 / 非本段覆盖的工作流没有这些 query）。
    if out["status"] == "RUNNING":
        for qname in ("paused", "current_job"):
            try:
                out[qname] = await handle.query(qname)
            except Exception:                                   # noqa: BLE001 —— 没有该 query 就当没有
                out.setdefault(qname, None)
        out["paused"] = bool(out.get("paused"))
    else:
        out["paused"] = False
        out.setdefault("current_job", None)
    return out


async def pause_impl(client, *, schedule_id: Optional[str] = None,
                     workflow_id: Optional[str] = None, run_id: Optional[str] = None,
                     resume: bool = False) -> dict:
    """暂停 / 恢复。**幂等**：重复暂停不改判据、不报错。

    · 给 `schedule_id` → 暂停 **Temporal Schedule**（该计划不再触发；这就是 §5.1 的「暂停计划」）。
    · 给 `workflow_id` → 给**正在跑的工作流**发 `pause` / `resume` 信号（协作式，下一个检查点生效）。
    """
    from temporalio.service import RPCError

    if schedule_id:
        handle = client.get_schedule_handle(schedule_id)
        try:
            await (handle.unpause(note="runtime_control") if resume
                   else handle.pause(note="runtime_control"))
            desc = await handle.describe()
        except RPCError as exc:
            raise ControlError(404, f"调度不存在：{schedule_id}（{exc}）") from exc
        return {"kind": "schedule", "schedule_id": schedule_id,
                "paused": bool(desc.schedule.state.paused), "resumed": bool(resume)}

    if not workflow_id:
        raise ControlError(400, "必须给 workflow_id 或 schedule_id 之一")

    handle = client.get_workflow_handle(workflow_id, run_id=run_id)
    try:
        desc = await handle.describe()
    except RPCError as exc:
        raise ControlError(404, f"工作流不存在：{workflow_id}（{exc}）") from exc
    status = _status_of(desc)
    if status in _TERMINAL_NAMES:
        # 已结束的工作流 —— 暂停无意义，但**不是错误**（幂等）。
        return {"kind": "workflow", "workflow_id": workflow_id, "status": status,
                "paused": False, "resumed": bool(resume), "changed": False}
    try:
        await handle.signal("resume" if resume else "pause")
    except RPCError as exc:
        raise ControlError(409, f"工作流 {workflow_id} 无法接收暂停信号：{exc}") from exc
    return {"kind": "workflow", "workflow_id": workflow_id, "status": status,
            "paused": not resume, "resumed": bool(resume), "changed": True}


async def cancel_impl(client, *, workflow_id: str, run_id: Optional[str] = None,
                      job_id: Optional[str] = None, paper=None) -> dict:
    """取消一个工作流，并把它的长任务（`fin_job`）一并取消（`§10.4` 的「可请求取消」）。

    顺序：**先问它当前的长任务 → 原生取消 Temporal → 调 paper 取消那个 job**。
    先取 job 再取消，是为了在取消（工作流随即停住）之前拿到 job_id；取消之后工作流不会再走到
    `finish_point_job`，所以 job 不会反过来被标成 SUCCEEDED。

    **幂等**：已经结束的工作流 → 返回 `already_ended`（200），不报错、不重复取消。
    """
    from temporalio.service import RPCError

    handle = client.get_workflow_handle(workflow_id, run_id=run_id)
    try:
        desc = await handle.describe()
    except RPCError as exc:
        raise ControlError(404, f"工作流不存在：{workflow_id}（{exc}）") from exc
    status = _status_of(desc)
    if status in _TERMINAL_NAMES:
        return {"cancelled": False, "already_ended": True, "workflow_id": workflow_id,
                "run_id": desc.run_id, "status": status}

    if not job_id:                                              # 没显式给就从工作流现查
        try:
            cur = await handle.query("current_job")
            if isinstance(cur, dict):
                job_id = cur.get("job_id")
        except Exception:                                       # noqa: BLE001 —— 没有该 query 就没有 job
            job_id = None

    try:
        await handle.cancel()
    except RPCError as exc:
        raise ControlError(409, f"工作流 {workflow_id} 取消失败：{exc}") from exc

    job_status = None
    if job_id:
        if paper is None:                                       # 允许注入假 client（单测不连网）
            from app.bridge.paper import PaperClient
            paper = PaperClient()
        try:
            row = paper.cancel_job(job_id)
            job_status = (row or {}).get("status")
        except Exception as exc:                                # noqa: BLE001 —— 取消失败也要返回（已在取消工作流）
            job_status = f"cancel_failed:{type(exc).__name__}"

    return {"cancelled": True, "already_ended": False, "workflow_id": workflow_id,
            "run_id": desc.run_id, "status": "CANCEL_REQUESTED",
            "job_id": job_id, "job_status": job_status}
