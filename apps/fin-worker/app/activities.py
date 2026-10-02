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
from app.bridge.idem import order_key, point_job_key, report_job_key
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


def _active_strategy(param: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """`fin_param.strategies` 里当前生效的那一条。

    ⚠️ **与 `apps/api/app/services/fin/control.py::active_strategy` 是同一条规则的两份
    实现**（两个容器、两套依赖，不能互相 import）。规则只有一句：「有一条标了
    `active` 就用它，没有就用第一条」。改这边必须同时改那边，
    `tests/test_strategy_switch.py` 有用例盯着「两份实现同判」。
    """
    items = list((param or {}).get("strategies") or [])
    if not items:
        return None
    for item in items:
        if isinstance(item, dict) and item.get("active"):
            return item
    return items[0] if isinstance(items[0], dict) else None


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

    **M6 · 总开关在这里生效**：`fin_param.auto_enabled` 为 false 时**直接返回
    `halted=True`，不产出意图、不翻译命令 → 下游不会提交任何委托**。这是
    「总开关真的能停」那句承诺的技术兑现点：停的是「产生新委托」这件事本身，
    而不是「下了单再拒单」（后者会在 `fin_order` 里留下一行被拒的委托，
    用户看到的是「它还在动」）。

    ⚠️ 判据放在**出意图之前**，不是「提交前再查一次」——出意图与提交是两个
    Activity，中间可能隔着重试；如果放在提交侧，重试时读到的可能是已经恢复的开关。
    放在这里，Temporal 会把「这一时点不交易」这个结论冻结进历史，重放逐字节一致。

    **M6 · 策略切换也在这里生效**：`strategy_key` 取 `fin_param.strategies` 里
    标了 `active` 的那一条（没有就用第一条），不再是写死的 `FIN_SAMPLE_STRATEGY_KEY`。
    切换之后的下一次决策用的就是新策略 —— 这就是「下一次决策生效」。

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

    # ── M6 · 总开关：关着就不出意图 ──────────────────────────────────────
    # 字段缺失（老库还没补列 / paper 没返回）按「开着」处理 —— 这与默认值
    # `DEFAULT true` 一致，改开关这件事不会因为读不到而静默生效。
    if param is not None and param.get("auto_enabled") is False:
        logger.info("[decide] {} 自动交易总开关关闭，本时点不产生委托", project_id)
        return {
            "halted": True,
            "project_id": project_id,
            "reason": "自动交易总开关已关闭（fin_param.auto_enabled = false）",
            "decision": None, "idempotency_key": None, "command": None,
        }

    # ── M6 · 生效策略：取 fin_param.strategies 里 active 的那一条 ────────
    active = _active_strategy(param) or {}
    strategy_key = active.get("key") or config.sample_strategy_key()
    strategy_version = str(active.get("version") or config.sample_strategy_version())

    decision_dict = build_sample_decision(
        project=project,
        param=param,
        trade_date=req["trade_date"],
        point=req["point"],
        now=_parse_iso(req["now"]),
        code=req.get("code") or config.sample_code(),
        strategy_key=strategy_key,
        strategy_version=strategy_version,
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


# ── 每日报告（M5）────────────────────────────────────────────────────────

@activity.defn
def generate_daily_report(req: dict[str, Any]) -> dict[str, Any]:
    """收盘后为项目生成当日报告（M-17）。

    三件事，全部经 HTTP，**不碰账本库**：

    1. 在 `paper` 登记一条 `report` 长任务（同键幂等，重放拿到同一个 `job_id`）；
    2. 调 api 的 `/api/internal/fin/reports/generate` —— 那边的**确定性指标代码**
       从账本算事实、AI 只写文字、生成后跑回读校验；
    3. 按结果收尾：校验通过 → job SUCCEEDED + 产物引用；校验不通过（数字对不上）
       → job **FAILED** 且**不发布**；无报告日（非交易日 / 缺估值）→ job SUCCEEDED
       并写明原因（不是失败，是「这天没有报告」）。

    ⚠️ 校验不通过**不重试**：数字对不上是确定性的结论，重试只会反复烧 token。
    要重跑由人工处置（重跑 `15:30` 时点或等下一交易日）。
    """
    project_id = req["project_id"]
    trade_date = req["trade_date"]
    paper = PaperClient()
    key = report_job_key(project_id, trade_date)
    job = paper.submit_job(
        job_type="report",
        params={"trade_date": trade_date, "point": "report"},
        project_id=project_id,
        idempotency_key=key,
    )
    job_id = job["job_id"]
    paper.mark_job_running(job_id)

    try:
        out = HunterApiClient().generate_report(project_id, trade_date)
    except ApiError as exc:
        paper.fail_job(job_id)
        logger.error("[report] 生成失败 project={} date={} · {}", project_id, trade_date, exc)
        raise

    status = out.get("status")
    if status == "failed":
        # 回读校验没通过：报告落库为 failed，**不发布**；job 记 FAILED。
        paper.fail_job(job_id)
        logger.warning("[report] 回读校验未通过，不发布 · project={} date={} · 违规 {} 处",
                       project_id, trade_date, len((out.get("check") or {}).get("violations") or []))
    elif status == "no_report":
        paper.succeed_job(job_id, f"report:no_report:{trade_date}",
                          {"status": status, "reason": out.get("reason")})
        logger.info("[report] 无报告日 · project={} date={} · {}", project_id, trade_date,
                    out.get("reason"))
    else:
        paper.succeed_job(job_id, out.get("artifact_ref") or out.get("report_id") or "report",
                          {"status": status, "report_id": out.get("report_id"),
                           "artifact_ref": out.get("artifact_ref"),
                           "checked": (out.get("check") or {}).get("checked"),
                           "used_fallback": out.get("used_fallback")})
        logger.info("[report] 生成并校验通过 · project={} date={} · report={} · 核对 {} 个数字",
                    project_id, trade_date, out.get("report_id"),
                    (out.get("check") or {}).get("checked"))
    return {"job_id": job_id, "idempotency_key": key, **out}


# ── 标的元数据同步（M7 · M-20）─────────────────────────────────────────────

@activity.defn
def sync_instruments(req: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """把 A 股标的的**涨跌停幅度 / ST 判定**从 `company_master` / `stock_universe`
    同步进 `fin_instrument`（`05 §3.3` M-20）。

    三条不能破：

    1. **由 fin-worker 定时任务做，不是迁移做**（`09 §七` 跨库一行）。日历同步已经
       走这条路（`sync_calendar` 也是这里），标的元数据同理 —— 它是**运行期数据**，
       不是 schema。
    2. **同步不上就不写**。api 侧返回的每条带 `available`；`available=false`（板块
       判不出 / master 里没有）的那条**跳过**，`fin_instrument` 里就没有这一行，
       于是风控第 4 条会**拒绝该标的** —— 「绝不猜涨跌幅」的兑现点在这里。
       **不许**拿代码形态硬凑一个幅度顶上（那正是要防的静默错）。
    3. **失败要如实报**：一条都拿不到（api 挂了 / master 空）时返回 `ok=False` 与原因，
       工作流记下来；下一轮再试。
    """
    req = req or {}
    api = HunterApiClient()
    try:
        payload = api.fin_instruments(codes=req.get("codes"), market=req.get("market", "cn"))
    except ApiError as exc:
        logger.warning("[instruments] 标的元数据拉取失败：{}", exc)
        return {"ok": False, "error": str(exc), "written": 0, "skipped": 0}

    items = payload.get("items") or []
    paper = PaperClient()
    written = 0
    skipped: list[dict[str, str]] = []
    for item in items:
        if not item.get("available"):
            # 判不出就**不写**：这条标的在账本里保持「没有元数据」，风控会拒它。
            skipped.append({"code": item.get("code"), "reason": item.get("reason") or "不可用"})
            continue
        paper.upsert_instrument({
            "code": item["code"], "name": item["name"], "exchange": item["exchange"],
            "board": item["board"], "is_st": item["is_st"],
            "limit_up_pct": item["limit_up_pct"], "limit_down_pct": item["limit_down_pct"],
            "lot_size": item["lot_size"], "listed_at": None, "is_active": True,
            "source": item.get("source") or "company_master/stock_universe",
        })
        written += 1

    logger.info("[instruments] 同步完成 · 写入 {} · 跳过 {} · 源 {} 条",
                written, len(skipped), payload.get("source_count"))
    return {"ok": True, "written": written, "skipped": len(skipped),
            "skipped_detail": skipped[:20], "source_count": payload.get("source_count")}


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
