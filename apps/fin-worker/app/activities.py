"""Temporal Activities —— 每一个都是**幂等**的。

Activity 是 Temporal 里**唯一允许产生副作用**的东西，而它的重试语义是
**at-least-once**（`01方案 §11` 开头：「有副作用的 Activity 在异常情形下可能
再次执行」）。所以这一层里每一处写操作都必须回答同一个问题：

    「这一步被执行两次，会不会多出一笔不该有的东西？」

答案靠两样东西一起给：
- **业务幂等键**（`bridge.idem`）—— 键从业务身份推导，重试拿到同一个键；
- **`fin_idempotency`**（M3）—— paper 认出同键同内容，返回原回执，不再开第二笔。

**读活动**（`read_calendar` / `list_active_projects`）也是 Activity，
因为工作流代码必须是确定性的：连不连得上 external 系统、拿到什么数据，
都不能在 workflow 里做。

**Activity 里没有「连接 hunter_fin」这回事** —— 全部走 `PaperClient` 的 HTTP。
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from loguru import logger
from temporalio import activity

from app import config
from app.bridge.contracts import StrategyDecision
from app.bridge.hunter_api import ApiError, HunterApiClient
from app.bridge.idem import order_key, point_job_key
from app.bridge.paper import PaperClient
from app.strategy.sample import build_decision as build_sample_decision

SHANGHAI = timezone(timedelta(hours=8))

# 交易日历的默认时段（A 股：上午 / 下午各一段）。这不是「编」——
# `fin_market_calendar.sessions` 就是给撮合判时段用的，A 股的时段是公开常识，
# 且逐条与 `paper/risk/session.py` 的口径一致。
A_SESSIONS = [{"open": "09:30", "close": "11:30"}, {"open": "13:00", "close": "15:00"}]


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"时刻必须带时区：{value!r}")
    return dt


def _heartbeat(details: dict[str, Any]) -> None:
    """打一次 Activity 心跳。**不在 Activity 上下文里时静默跳过** ——
    这样直接调用 Activity 函数的单元测试不会被心跳炸掉。"""
    try:
        activity.heartbeat(details)
    except Exception:  # noqa: BLE001 —— 非 Activity 上下文（单测直接调用）
        pass


# ── 交易日历 ──────────────────────────────────────────────────────────────

@activity.defn
def sync_calendar(req: dict[str, Any]) -> dict[str, Any]:
    """把一段日期的交易日历同步进 `fin_market_calendar`（`preopen` 时点做）。

    数据来自 api 的 `GET /api/internal/calendar/trading-days`（akshare A 股交易日历，
    **前视**）。拉不到就**如实返回失败**，让工作流按「日历未知 → 不当交易日」走 ——
    **绝不按星期几自己补一个日历**（那正是 `01方案 §5.3` 要消掉的第二套权威）。
    """
    center = date.fromisoformat(req["trade_date"])
    window_start = center - timedelta(days=int(req.get("lookback_days", 5)))
    window_end = center + timedelta(days=int(req.get("lookahead_days", 12)))
    market = req.get("market", "a")

    api = HunterApiClient()
    try:
        days = api.trading_days(market, window_start.isoformat(), window_end.isoformat())
    except ApiError as exc:
        logger.warning("[calendar] 交易日历拉取失败 · 不当交易日处理：{}", exc)
        return {"ok": False, "error": str(exc),
                "window": [window_start.isoformat(), window_end.isoformat()]}

    trading = set(days)
    paper = PaperClient()
    written = 0
    cursor = window_start
    while cursor <= window_end:
        is_trading = cursor.isoformat() in trading
        paper.upsert_calendar(
            cursor.isoformat(),
            is_trading=is_trading,
            sessions=A_SESSIONS if is_trading else [],
            note=("akshare 交易日历" if is_trading
                  else "非交易日（不在 akshare A 股交易日历中）"),
        )
        written += 1
        cursor += timedelta(days=1)
    logger.info("[calendar] 同步 {} 天（{} ~ {}），其中交易日 {} 天",
                written, window_start, window_end, len(trading))
    return {"ok": True, "written": written, "trading_days": len(trading),
            "window": [window_start.isoformat(), window_end.isoformat()]}


@activity.defn
def read_calendar(req: dict[str, Any]) -> dict[str, Any]:
    """读某一天的交易日历。**三种结果**，缺哪一种是哪一种：

    | 返回 | 含义 | 工作流怎么办 |
    |---|---|---|
    | `known=True, trading=True` | 交易日 | 继续 |
    | `known=True, trading=False` | 非交易日（日历上明写） | 跳过，不触发 |
    | `known=False` | **没有这一天的日历** | 跳过 + **告警**（不许默认当交易日） |
    """
    trade_date = req["trade_date"]
    row = PaperClient().get_calendar(trade_date)
    if row is None:
        logger.warning("[calendar] {} 没有交易日历 —— 按「未知」处理，**不触发交易**。"
                       "先跑 preopen 时点的日历同步，或检查 akshare 是否可用。", trade_date)
        return {"known": False, "trading": False, "sessions": [], "trade_date": trade_date,
                "note": "交易日历缺失：未知 ≠ 交易日"}
    return {
        "known": True,
        "trading": bool(row.get("is_trading")),
        "sessions": row.get("sessions") or [],
        "trade_date": trade_date,
        "note": row.get("note"),
    }


# ── 项目 ──────────────────────────────────────────────────────────────────

@activity.defn
def list_active_projects(req: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """当前进行中的所有项目。工作流对每一个跑一遍时点动作。"""
    return PaperClient().list_projects(status="active")


# ── 业务检查点（fin_job）──────────────────────────────────────────────────

@activity.defn
def begin_point_job(req: dict[str, Any]) -> dict[str, Any]:
    """登记「今天这个时点跑起来了」并落业务检查点。

    先 `submit`（同键返回原 job）再 `mark_running`（已 RUNNING 时吞掉 409）——
    所以 Worker 重启后重放走到这里，拿到的是**同一个 job_id**，不会多出一条 job。
    """
    project_id = req["project_id"]
    trade_date = req["trade_date"]
    point = req["point"]
    key = point_job_key(project_id, trade_date, point)
    paper = PaperClient()
    job = paper.submit_job(
        job_type="trading_point",
        params={"point": point, "trade_date": trade_date, "at": req.get("at")},
        project_id=project_id,
        idempotency_key=key,
    )
    job_id = job["job_id"]
    paper.mark_job_running(job_id)
    checkpoint = {
        "point": point, "trade_date": trade_date,
        "phase": "started", "started_at": req.get("now"),
    }
    paper.set_checkpoint(job_id, checkpoint)
    return {
        "job_id": job_id,
        "created": bool(job.get("created")),
        "idempotency_key": key,
        "checkpoint": checkpoint,
    }


@activity.defn
def write_checkpoint(req: dict[str, Any]) -> dict[str, Any]:
    """把业务检查点写进 `fin_job.checkpoint`（**不改状态**）。

    这是「长计算中断 → 按业务检查点恢复」（`01方案 §11.2`）的落点：
    时点跑到哪一步、有没有下过单，写进 checkpoint，恢复时一眼看得出。
    """
    checkpoint = req.get("checkpoint", {})
    PaperClient().set_checkpoint(req["job_id"], checkpoint)
    return {"job_id": req["job_id"], "checkpoint": checkpoint}


@activity.defn
def finish_point_job(req: dict[str, Any]) -> dict[str, Any]:
    """时点收尾：把 job 标 SUCCEEDED 并写最终检查点。重复调用安全（409 吞掉）。"""
    paper = PaperClient()
    paper.succeed_job(req["job_id"], req.get("result_ref", "ok"), req.get("checkpoint", {}))
    return {"job_id": req["job_id"], "status": "SUCCEEDED"}


@activity.defn
def fail_point_job(req: dict[str, Any]) -> dict[str, Any]:
    PaperClient().fail_job(req["job_id"])
    return {"job_id": req["job_id"], "status": "FAILED"}


# ── 执行区动作（全部经 paper HTTP）─────────────────────────────────────────

@activity.defn
def confirm_t1(req: dict[str, Any]) -> dict[str, Any]:
    """T+1 日切：把昨天买进的持仓转为可卖（`M3 报告 · 遗留 5/6` 交给 M4 的那件事）。

    排在 `preopen`，因为那时**没有挂单**（昨天的已在昨天收盘撤掉、今天的还没下）。
    """
    return PaperClient().confirm_t1(req["project_id"])


@activity.defn
def build_decision(req: dict[str, Any]) -> dict[str, Any]:
    """读项目 → 产出 `StrategyDecision` → 翻译成 Paper 命令。**只读，无副作用。**

    「策略意图 → Paper Service 命令」（M-15）那条链路的入口：
    `build_decision()` 产出 `StrategyDecision` → `to_paper_command()` 翻译。
    桥这一层**不读行情、不做分析**。

    ⚠️ **必须与 `submit_decision` 分开**（M4 验收时用真崩溃测出来的）：
    一次「决定」里读了账户版本（`expected_version`），而账户版本会被上一笔成交改掉。
    如果读版本与提交合并成一个 Activity，那么「提交后 Worker 崩、Temporal 重试」
    的**第二次**执行会读到**新的**版本 → 同一个幂等键配上一份**不同内容** →
    paper 回 409（同键不同内容，拒绝覆盖），工作流永久失败。
    拆开之后：决定只做一次（结果进 Temporal 历史），提交读的是**冻结在历史里**的命令，
    重试时逐字节相同 → paper 认出是同一份内容 → 返回原回执。
    """
    project_id = req["project_id"]
    paper = PaperClient()
    view = paper.get_project(project_id)
    if not view or not view.get("project"):
        raise LookupError(f"项目不存在或不可读：{project_id}")
    project = view["project"]
    param = view.get("param")

    decision_dict = build_sample_decision(
        project=project,
        param=param,
        trade_date=req["trade_date"],
        point=req["point"],
        now=_parse_iso(req["now"]),
        code=req.get("code") or config.sample_code(),
        strategy_key=config.sample_strategy_key(),
        strategy_version=config.sample_strategy_version(),
        ttl_seconds=config.contract_timeout_seconds(),
    )
    decision = StrategyDecision.from_dict(decision_dict)
    key = order_key(project_id, req["trade_date"], req["point"], decision.decision_id)
    body = decision.to_paper_command(project_id=project_id, idempotency_key=key)
    return {"decision": decision.to_dict(), "idempotency_key": key, "command": body}


@activity.defn
def submit_decision(req: dict[str, Any]) -> dict[str, Any]:
    """把 `build_decision` 冻结下来的命令提交给 paper。**这是有副作用的那一步。**

    幂等全靠「命令是冻结的 + 幂等键从业务身份推导」：
    重试时 `req["command"]` 逐字节相同 → 同键同内容 → paper 返回原回执，
    `fin_trade` 不会多出第二笔（`01方案 §11.2`「订单记账后回执丢失 → 返回原订单与成交，不再创建」）。

    `hold_seconds` 是**故障注入开关**（默认 0，调度时永远不传）：置正数时，
    Activity 在下单之后、返回之前保持不返回这么久。在保持期间杀掉 Worker，
    Temporal 靠心跳超时判定它死了并重试 —— 这是崩溃续跑的真实复现。
    """
    receipt = PaperClient().place_order(req["command"])
    logger.info("[submit] {} {} → {} · {}（幂等键 {}）",
                req.get("project_id"), req.get("point"), receipt.get("status"),
                receipt.get("trade_id"), req.get("idempotency_key"))

    hold = int(req.get("hold_seconds") or 0)
    if hold > 0:
        # 故障注入：下单后**不返回**，睡够 hold 秒。睡的时候按 2 秒一跳打心跳 ——
        # Worker 被 kill 后 Temporal 靠心跳超时（`heartbeat_timeout`）在十秒内
        # 判定它死了并重试，而不是等满 start_to_close（那要 10 分钟，测试没法看）。
        logger.warning("[submit] 故障注入：下单后保持 {}s 不返回（验证崩溃续跑不重复下单）", hold)
        remaining = hold
        while remaining > 0:
            _heartbeat({"phase": "holding", "remaining": remaining})
            time.sleep(min(2, remaining))
            remaining -= 2

    return {
        "receipt": receipt,
        "order_status": receipt.get("status"),
        "trade_id": receipt.get("trade_id"),
    }


@activity.defn
def match_open_orders(req: dict[str, Any]) -> dict[str, Any]:
    """拿新快照再撮一遍挂单（`11:30 / 13:00 / 14:55` 三个时点）。"""
    return PaperClient().match_open(req["project_id"])


@activity.defn
def close_day(req: dict[str, Any]) -> dict[str, Any]:
    """收盘三件事，顺序不能反：**先撤单 → 再估值 → 最后对账**。

    对账有一项是「无挂单时冻结归零」——先对账会把还没撤的挂单算成不平。
    """
    project_id = req["project_id"]
    at_iso = req["now"]
    paper = PaperClient()
    expired = paper.expire_open(project_id, at_iso, reason="close")
    valuation = paper.make_valuation(project_id, at_iso)
    recon = paper.run_recon(project_id, at_iso)
    return {"expired": expired, "valuation": valuation, "recon": recon}


# ── 数据面（「谁决定什么时候拉数据」的落点）───────────────────────────────

@activity.defn
def trigger_market_etl(req: dict[str, Any]) -> dict[str, Any]:
    """由 Temporal 触发现有 K 线 ETL。

    ⚠️ 这里**没有重写取数逻辑**：api 侧的 `/api/internal/etl/run-market` 直接调
    原来的 `klines_etl.run_market`。M4 改的是**「谁决定什么时候拉」**，
    不是「怎么拉」（`01方案 §5.3`、M0 报告 §2.6）。
    """
    api = HunterApiClient()
    market = req["market"]
    result = api.run_market_etl(market, limit=req.get("limit"), bars=req.get("bars"))
    logger.info("[etl] market={} 触发完成：{}", market, result)
    return {"market": market, "result": result}
