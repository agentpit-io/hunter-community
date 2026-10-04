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
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Optional

from loguru import logger
from temporalio import activity

from app import config
from app.bridge.contracts import StrategyDecision
from app.bridge.hunter_api import ApiError, HunterApiClient
from app.bridge.idem import order_key, point_job_key, report_job_key
from app.bridge.paper import PaperClient, PaperError
from app.channels import CAL_MARKET
from app.market_time import canonical_market
from app.strategy import shadow as shadow_strategy
from app.strategy.sample import SampleMemoryBlocked, SampleNoBudget
from app.strategy.sample import build_decision as build_sample_decision

# A 股交易日按上海时间切。用 IANA 时区名（不是固定偏移）—— 镜像装 tzdata（N2）。
SHANGHAI = ZoneInfo("Asia/Shanghai")

# 各市场的常设时段（本地时刻）。这不是「编」——
# `fin_market_calendar.sessions` 就是给撮合判时段用的，时段是交易所公开口径
# （A 股 09:30-11:30/13:00-15:00、港股 09:30-12:00/13:00-16:00、美股 09:30-16:00），
# 与 `fin_market_rule.sessions` 的种子逐项一致；同步时优先用市场规则行里的那一份。
A_SESSIONS = [{"open": "09:30", "close": "11:30"}, {"open": "13:00", "close": "15:00"}]
DEFAULT_SESSIONS: dict[str, list[dict[str, str]]] = {
    "CN_A": A_SESSIONS,
    # HK **必须和 `fin_market_rule.sessions` 逐字一致**（含 16:00–16:10 收市竞价）。
    # 2026-10-03 演示站实测：兜底少了收市竞价那一段，凡是「读市场规则失败 → 走兜底」
    # 那一次同步写出来的日历，16:00–16:10 的报价（港股收市竞价那根 tick，实测
    # `00700` 的 `event_time` 就是 16:08:10）会被判「不在交易时段」→ 委托永远拒。
    # 两份时段是**同一个事实**，改一处必须改另一处（`db/migrations/0030`/`0034` 喂
    # `fin_market_rule`）。
    "HK": [{"open": "09:30", "close": "12:00"}, {"open": "13:00", "close": "16:00"},
           {"open": "16:00", "close": "16:10"}],
    "US": [{"open": "09:30", "close": "16:00"}],
}


def _market_sessions(market: str) -> list[dict[str, str]]:
    """该市场的常设时段。优先 `fin_market_rule.sessions`（经 HTTP 读），取不到用兜底。"""
    fallback = DEFAULT_SESSIONS.get(market, A_SESSIONS)
    try:
        for row in PaperClient().market_rules():
            if row.get("market") == market and row.get("sessions"):
                return list(row["sessions"])
    except Exception as exc:  # noqa: BLE001 —— 读不到就用兜底，不阻断日历同步
        logger.warning("[calendar] 读市场规则时段失败（用兜底 {}）：{}", market, exc)
    return fallback


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


def _resolve_market(market_arg: Optional[str], view: dict, project: dict,
                    project_id: str) -> str:
    """该时点要驱动哪个子账户。

    优先级：**请求带的 `market`**（调度路径恒定有）→ paper 项目视图解析出的单市场
    （`view["market"]`，单市场项目）→ 项目 `market_scope`（老响应兼容）。
    **`MULTI` 一律拒绝** —— 那是「有多个市场」，拿它当市场名就是拿 A 股顶替港美股。
    """
    scope = project.get("market_scope")
    raw = market_arg or view.get("market") or scope
    if raw is None:
        # 响应里没有任何市场信息（老 paper / 老 fixtures）：沿用一期缺省。
        raw = "CN_A"
    if str(raw).strip().upper() == "MULTI":
        raise ValueError(
            f"项目 {project_id} 是多市场（market_scope=MULTI），无法从项目本身定市场——"
            "请显式传 market（调度路径由时点带进来）"
        )
    return canonical_market(raw)


def _market_order_supported(paper: PaperClient, market: str) -> bool:
    """该市场是否支持市价单（`fin_market_rule.market_order_supported`，0037）。

    读不到规则行 / 列缺失 → 退回旧口径「只有 A 股支持市价单」，与 `matching/engine.py`
    的同名判定**同一条规则**（那边判拒绝，这边判出什么价型）。示例策略按它决定
    出市价单还是限价单 —— 这就是「按市场参数化」，不是写死 `market == "CN_A"`。
    """
    try:
        rows = paper.market_rules()
    except Exception as exc:  # noqa: BLE001 —— 读不到就按最保守口径（只有 A 股有盘口）
        logger.warning("[decide] 读市场规则失败，市价单能力按旧口径：{}", exc)
        rows = []
    rule = next((r for r in rows if r.get("market") == market), None)
    if not rule:
        return market == "CN_A"
    flag = rule.get("market_order_supported")
    return market == "CN_A" if flag is None else bool(flag)


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
    # 市场可能是本仓三值（CN_A/HK/US）或旧写法（a/cn）—— 统一归一。
    market = canonical_market(req.get("market", "CN_A"))
    # 交易日历端点的 market 参数是旧写法（A 股用 'a'，港美股 'hk'/'us'）—— 集中转换。
    sessions = _market_sessions(market)

    api = HunterApiClient()
    try:
        days = api.trading_days(CAL_MARKET[market], window_start.isoformat(),
                                window_end.isoformat())
    except ApiError as exc:
        logger.warning("[calendar] 交易日历拉取失败 · 不当交易日处理：{}", exc)
        return {"ok": False, "error": str(exc), "market": market,
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
            sessions=sessions if is_trading else [],
            note=("akshare 交易日历" if is_trading
                  else "非交易日（不在 akshare A 股交易日历中）"),
            market=market,
        )
        written += 1
        cursor += timedelta(days=1)
    logger.info("[calendar] 同步 {} 天（{} ~ {}）· market={} · 交易日 {} 天",
                written, window_start, window_end, market, len(trading))
    return {"ok": True, "market": market, "written": written, "trading_days": len(trading),
            "window": [window_start.isoformat(), window_end.isoformat()]}


@activity.defn
def market_clock(req: dict[str, Any]) -> dict[str, Any]:
    """按市场时区给出「当地交易日 + 当地时刻」。**在工作流里算不出来，只能在这里算。**

    ⚠️ 为什么是 Activity 而不是工作流内的纯函数（2026-10-03 踩的坑）：
    Temporal 的工作流沙箱会把模块级的 `ZoneInfo` 实例包成 `_RestrictedProxy`，
    `astimezone()` 直接抛 `tzinfo argument must be None or of a tzinfo subclass`。
    **在调用点现建 `ZoneInfo` 也一样被包**（试过，见 workflows 模块头）。
    Activity 跑在沙箱外，是这里唯一能拿到真 `ZoneInfo` 的地方；结果进事件历史，
    replay 时读的是历史里那份，确定性不受影响。

    时区名一律走 `market_time.MARKET_TZ`（IANA 名，夏令时自动跟随），
    **不写死 ±N 偏移**（M7 的 12 小时根因）。
    """
    from app.market_time import MARKET_TZ

    market = canonical_market(req.get("market", "CN_A"))
    tz = ZoneInfo(MARKET_TZ[market])
    now = datetime.now(tz)
    return {"market": market, "trade_date": now.date().isoformat(), "now": now.isoformat()}


@activity.defn
def read_calendar(req: dict[str, Any]) -> dict[str, Any]:
    """读**某市场某一天**的交易日历。**三种结果**，缺哪一种是哪一种：

    | 返回 | 含义 | 工作流怎么办 |
    |---|---|---|
    | `known=True, trading=True` | 交易日 | 继续 |
    | `known=True, trading=False` | 非交易日（日历上明写） | 跳过，不触发 |
    | `known=False` | **没有这一天的日历** | 跳过 + **告警**（不许默认当交易日） |

    日历按 `(market, trade_date)` 读（0029 起主键就是这两列）；`market` 缺省 `CN_A`
    保持旧调用方行为 —— **港美股必须显式传市场**（拿 A 股日历冒充就是编数据）。
    """
    trade_date = req["trade_date"]
    market = canonical_market(req.get("market", "CN_A"))
    row = PaperClient().get_calendar(trade_date, market=market)
    if row is None:
        logger.warning("[calendar] {}/{} 没有交易日历 —— 按「未知」处理，**不触发**。"
                       "先跑该市场 preopen 时点的日历同步。", market, trade_date)
        return {"known": False, "trading": False, "sessions": [], "trade_date": trade_date,
                "market": market, "note": "交易日历缺失：未知 ≠ 交易日"}
    return {
        "known": True,
        "trading": bool(row.get("is_trading")),
        "sessions": row.get("sessions") or [],
        "trade_date": trade_date,
        "market": market,
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
    `market` 一路带下去 —— 多市场项目在**每个市场的 preopen** 各日切各的子账户
    （P2：不这样的话晚开的市场会把早开市场当天的买入也转成可卖，绕过 T+1）。
    """
    return PaperClient().confirm_t1(req["project_id"], market=req.get("market"))


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
    # 市场**先定**（P2）：多市场（`MULTI`）项目调账本接口必须显式带 `market`
    # （P1 起 paper 不再静默落到 A 股）。调度路径由时点把 `market` 带进来。
    market_arg = (req.get("market") or "").strip() or None
    try:
        view = paper.get_project(project_id, market=market_arg)
    except PaperError as exc:
        if market_arg is None and exc.status == 400:
            raise ValueError(
                f"无法确定项目 {project_id} 的子账户：请求没带 market 且项目是多市场"
                "（paper 回 400）—— 调度路径由时点带 market，不在这里猜"
            ) from exc
        raise
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

    # 市场：该时点所属市场（N4 起市场是一等参数）。示例标的按市场取。
    # **`MULTI` 不许被当成市场**（P1 遗留：老代码 `or project["market_scope"]` 会得到
    # 字面量 "MULTI" → `canonical_market` 抛 UnknownMarket）。这里显式拒绝。
    market = _resolve_market(market_arg, view, project, project_id)

    code = req.get("code") or config.sample_code(market)
    # 市价单能力**按市场取表**（`fin_market_rule.market_order_supported`，0037）。
    mos = _market_order_supported(paper, market)
    reference_price: Optional[str] = None
    lot_size: Any = None
    available_cash: Any = None
    if not mos:
        # 只接限价单的市场（港美股）—— 限价单必须带价、按每手定量，三者都是**真实输入**：
        #   · 参考价 = 行情源的当前报价（api 内部只读端点，不落库）；
        #   · 每手   = `fin_instrument.lot_size`（账本里的标的元数据）；
        #   · 可用资金 = 该市场子账户余额（paper 的项目视图）。
        # 拿不到任一 → 不出委托（`SampleNoBudget` / 缺元数据由 paper 风控拒），不猜数字。
        inst = paper.get_instrument(code)
        if inst is not None:
            lot_size = inst.get("lot_size")
        quote = HunterApiClient().quote(code)
        reference_price = (quote or {}).get("last_price")
        available_cash = (view.get("cash") or {}).get("available")

    try:
        decision_dict = build_sample_decision(
            project=project,
            param=param,
            trade_date=req["trade_date"],
            point=req["point"],
            now=_parse_iso(req["now"]),
            code=code,
            strategy_key=strategy_key,
            strategy_version=strategy_version,
            ttl_seconds=config.contract_timeout_seconds(),
            market=market,
            market_order_supported=mos,
            reference_price=reference_price,
            lot_size=lot_size,
            available_cash=available_cash,
            # R3 · 决策前冻结的经验集（工作流传进来）。策略据它做**只收紧**的拦截。
            memory=req.get("memory"),
        )
    except SampleMemoryBlocked as exc:
        # 冻结经验集里有命中本标的的已验证结论 —— 本时点不出委托（R3 · §4.3）。
        # 形状与「总开关关闭」「买不起」一致，但**多带两样留痕**：依据的 experience_id
        # 与当时冻结的 memory_snapshot_id（工作流把它们写进 checkpoint）。
        logger.info("[decide] {} {} 经验闸门命中，本时点不出委托：{}", project_id, market, exc)
        return {"halted": True, "project_id": project_id, "reason": str(exc),
                "decision": None, "idempotency_key": None, "command": None,
                "memory": {"memory_snapshot_id": exc.memory_snapshot_id,
                           "blocked_by": exc.blocked}}
    except SampleNoBudget as exc:
        # 买不起 / 算不出 —— 本时点**不出委托**（不是错误）。形状与「总开关关闭」一致，
        # 工作流据此走 halted 分支并如实记原因（`_run_for_project` 的 decide 分支）。
        logger.info("[decide] {} {} 本时点不出委托：{}", project_id, market, exc)
        return {"halted": True, "project_id": project_id, "reason": str(exc),
                "decision": None, "idempotency_key": None, "command": None}
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
    """拿新快照再撮一遍挂单（`11:30 / 13:00 / 14:55` 三个时点）。

    **按该市场子账户**（P2）：只撮本市场的挂单，不拿别市场的隔夜快照撮合。
    """
    return PaperClient().match_open(req["project_id"], market=req.get("market"))


@activity.defn
def close_day(req: dict[str, Any]) -> dict[str, Any]:
    """收盘三件事，顺序不能反：**先撤单 → 再估值 → 最后对账**。

    对账有一项是「无挂单时冻结归零」——先对账会把还没撤的挂单算成不平。
    """
    project_id = req["project_id"]
    at_iso = req["now"]
    # 收盘三件事都按**该市场子账户**做（缺省取项目 market_scope，A 股行为不变）。
    market = req.get("market")
    paper = PaperClient()
    expired = paper.expire_open(project_id, at_iso, reason="close", market=market)
    valuation = paper.make_valuation(project_id, at_iso, market=market)
    recon = paper.run_recon(project_id, at_iso, market=market)
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
    """把标的元数据同步进 `fin_instrument`（`05 §3.3` M-20）—— **按市场**。

    | market | 同步什么 | 每手股数来源 |
    |---|---|---|
    | `cn` | 涨跌停幅度 / ST 判定（`company_master` / `stock_universe` + 腾讯简称） | 交易所规则 100 |
    | `hk` | 交易所 / 每手 / 名称 | 港交所官方 `data/hk_master.csv` |
    | `us` | 交易所 / 名称 | `1`（事实） |

    三条不能破：

    1. **由 fin-worker 定时任务做，不是迁移做**（`09 §七` 跨库一行）。日历同步已经
       走这条路（`sync_calendar` 也是这里），标的元数据同理 —— 它是**运行期数据**，
       不是 schema。
    2. **同步不上就不写**。api 侧返回的每条带 `available`；`available=false`（板块
       判不出 / master 里没有 / 港美股每手或交易所拿不到）的那条**跳过**，
       `fin_instrument` 里就没有这一行，于是风控第 4 条会**拒绝该标的** ——
       「绝不猜涨跌幅 / 每手 / 交易所」的兑现点在这里。
    3. **失败要如实报**：一条都拿不到（api 挂了 / master 空）时返回 `ok=False` 与原因，
       工作流记下来；下一轮再试。
    """
    req = req or {}
    market = (req.get("market") or "cn").strip().lower()
    api = HunterApiClient()
    try:
        payload = api.fin_instruments(codes=req.get("codes"), market=market)
    except ApiError as exc:
        logger.warning("[instruments] 标的元数据拉取失败：{}", exc)
        return {"ok": False, "error": str(exc), "written": 0, "skipped": 0, "market": market}

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
            "board": item["board"], "is_st": item.get("is_st", False),
            # 港美股没有涨跌幅限制 → `None`（api 如实给 None，这里不填 0）。
            "limit_up_pct": item.get("limit_up_pct"),
            "limit_down_pct": item.get("limit_down_pct"),
            "lot_size": item["lot_size"], "listed_at": None, "is_active": True,
            # 市场 / 币种按 api 返回的走；api 没给（老形状）就按本次同步的市场。
            "market": item.get("market") or _SYNC_MARKET.get(market, "CN_A"),
            "currency": item.get("currency") or _SYNC_CURRENCY.get(market, "CNY"),
            "source": item.get("source") or "company_master/stock_universe",
        })
        written += 1

    logger.info("[instruments] 同步完成 · market={} · 写入 {} · 跳过 {} · 源 {} 条",
                market, written, len(skipped), payload.get("source_count"))
    return {"ok": True, "market": market, "written": written, "skipped": len(skipped),
            "skipped_detail": skipped[:20], "source_count": payload.get("source_count")}


# 同步通道用的市场写法（`cn` / `hk` / `us`）→ 本仓统一三值 / 币种。
_SYNC_MARKET = {"cn": "CN_A", "hk": "HK", "us": "US"}
_SYNC_CURRENCY = {"cn": "CNY", "hk": "HKD", "us": "USD"}


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


# ── R3 · 决策上下文注入经验集（`freeze_memory`）────────────────────────────
#
# **只能读 `memory.query` 的结果**（§一.5 的不变量）：这里一行 SQL 都没有，
# 也读不到 `fin_experience`（守护测试盯着）。时间过滤（`as_of`）也是**服务端**的事，
# 这里只把「决策那一刻」传下去 —— 于是这份冻结集合里不会有事后来形成的经验。

@activity.defn
def freeze_memory(req: dict[str, Any]) -> dict[str, Any]:
    """决策前冻结经验集：`POST /internal/fin/memory/query {freeze: true, for_decision: true}`。

    `as_of` 取**决策那一刻**（工作流传的 `now`）—— 这是回放基准：用同一个
    `memory_snapshot_id` 重放，读到的**永远**是那一刻已形成的经验，
    后来新增 / 改可见性都不进去。

    ⚠️ **失败即失败（fail-closed）**：读不到经验集就不做这项决策，让 Temporal 重试。
    反过来（读不到就跳过经验闸门照常下单）正是「只收紧不放松」要防的事 ——
    一个读不到的故障不该变成一次没有刹车下的单。
    """
    out = HunterApiClient().memory_query(
        req["project_id"], market=req.get("market"),
        for_decision=True, freeze=True, purpose=req.get("purpose") or "decision",
        trade_date=req.get("trade_date"), point=req.get("point"), as_of=req.get("now"),
    )
    items = out.get("items") or []
    logger.info("[memory] 冻结经验集 project={} market={} → {} 条 · snapshot={}",
                req.get("project_id"), req.get("market"), len(items),
                out.get("memory_snapshot_id"))
    return out


# ── R3 · 复核（复盘）回路的三个活动 ────────────────────────────────────────

@activity.defn
def review_collect(req: dict[str, Any]) -> dict[str, Any]:
    """**只读**：取当日 `fin_report`（含 `self_review` 三问）+ `fin_report_fact` + 当日 `fin_trade`。

    fin-worker 不碰账本库 —— 这三样都经 api 的 `/internal/fin/review/collect` 拿。
    """
    out = HunterApiClient().review_collect(
        req["project_id"], req["trade_date"], req.get("market"))
    logger.info("[review] collect project={} date={} market={} → report={} facts={} trades={}",
                req.get("project_id"), req.get("trade_date"), req.get("market"),
                out.get("report") is not None, len(out.get("facts") or []),
                len(out.get("trades") or []))
    return out


@activity.defn
def review_propose(req: dict[str, Any]) -> dict[str, Any]:
    """调模型产出**候选**经验（只写文字，数字走 `fact` 引用；回读校验不过即作废）。

    **本活动不写任何经验** —— 写是下一个活动的事（`review_append`），
    这样「写经验只有一个入口」在复核这条链路上也成立。
    """
    out = HunterApiClient().review_propose(
        req["project_id"], req["trade_date"], req.get("market"))
    logger.info("[review] propose project={} date={} → {} 条候选 · fallback={}（{}）· 作废 {}",
                req.get("project_id"), req.get("trade_date"),
                len(out.get("candidates") or []), out.get("used_fallback"),
                out.get("reason"), len(out.get("rejected") or []))
    return out


def _candidate_key(cand: dict[str, Any]) -> tuple[str, frozenset]:
    """候选 / 已存在经验的业务身份：`(statement, {(evidence_kind, ref_id)})`。

    没有 id 可用（经验 id 是服务端生成的随机段），所以用**内容**当键 ——
    这正是「同一个候选重复提交」要判等的东西。
    """
    return (
        str(cand.get("statement") or "").strip(),
        frozenset((str(e.get("evidence_kind") or ""), str(e.get("ref_id") or ""))
                  for e in cand.get("evidence") or [] if isinstance(e, dict)),
    )


@activity.defn
def review_append(req: dict[str, Any]) -> dict[str, Any]:
    """把候选经验经 **Memory Service 唯一写入口**写进去（`/internal/fin/memory/evidence`）。

    **幂等**：Activity 是 at-least-once，重试不能写第二遍。经验 id 由服务端生成，
    所以这里先用 `memory.query` 把该项目已有的经验读回来，按
    `(statement, 证据集合)` 建一张已存在表，**已存在的候选直接跳过**。
    读不回来 → **抛错让 Temporal 重试**，不硬写（硬写就等于放弃幂等）。

    返回 `{written: [...], skipped: [...], used_fallback, reason}`：写了哪几条、
    跳了哪几条、模型那一步是不是降级了 —— 都进 Temporal 历史与检查点。

    ⚠️ `review_collect` 与 `review_propose` 都是**只读**的，所以重放它们没有代价；
    只有这个活动有副作用，幂等也只需要在这里守住。
    """
    project_id = req["project_id"]
    market = req.get("market")
    candidates = [c for c in (req.get("candidates") or []) if isinstance(c, dict)]
    api = HunterApiClient()

    existing: set[tuple[str, frozenset]] = set()
    seen = api.memory_query(project_id, market=market)   # 失败即抛 → Temporal 重试
    for item in seen.get("items") or []:
        existing.add(_candidate_key(item))

    written: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for cand in candidates:
        key = _candidate_key(cand)
        if key in existing:
            skipped.append({"statement": cand.get("statement"), "reason": "already_appended"})
            continue
        out = api.memory_append(
            project_id=project_id, kind=cand.get("kind"),
            statement=cand.get("statement"), evidence=cand.get("evidence") or [],
            market=market, applicability=cand.get("applicability"),
            invalidation_condition=cand.get("invalidation_condition"),
            as_of=req.get("as_of"),
            # R5 · 复核在 propose 一步打好的结构化标签（`symbols` / regime），
            # 原样经唯一写入口落库 —— 本活动**不重新计算**（判定口径只有一份，在 api 侧）。
            memory_layer=cand.get("memory_layer"),
            polarity=cand.get("polarity"),
            symbols=cand.get("symbols"),
            strategy_keys=cand.get("strategy_keys"),
            regime_tags=cand.get("regime_tags"),
            regime_source=cand.get("regime_source"),
        )
        row = out.get("experience") or {}
        existing.add(key)
        written.append({
            "experience_id": row.get("experience_id"),
            "kind": row.get("kind"), "status": row.get("status"),
            "source": row.get("source"), "statement": row.get("statement"),
        })
    logger.info("[review] append project={} date={} → 写入 {} · 跳过 {}（已是同一条）",
                project_id, req.get("trade_date"), len(written), len(skipped))
    return {"written": written, "skipped": skipped,
            "used_fallback": bool(req.get("used_fallback")),
            "fallback_reason": req.get("fallback_reason")}


# ── R7 · 影子验证（`fin.shadow` 工作流的三个活动）─────────────────────────────
#
# 三个活动对应「准备 → 记账 → 判定」三步。**两臂同条件**在这里落地：
# 两臂调同一个 `strategy.shadow.build_for_arm`（同一个策略、同一个行情快照），
# 只有「该臂的配置」与「该臂自己的现金 / 持仓」不同。影子成交经 paper 的
# `simulate_arms` **只算不记**（红线 12：那里只依赖 DecisionRecorder，从不碰执行端），
# 落库由 api 的 `/shadow/record` 做（影子事件表只有 api 能写）。

@activity.defn
def market_points(req: dict[str, Any]) -> list[dict[str, Any]]:
    """该市场的**一组调度时点**（影子在每个时点各跑一次两臂）。

    来源与调度一致：优先 `fin_market_rule.points`（经 paper HTTP），读不到用
    `points.DEFAULT_POINTS` 兜底（与 `0030` 的种子逐项一致）。
    """
    market = canonical_market(req.get("market") or "CN_A")
    times: list[str] = []
    try:
        for row in PaperClient().market_rules():
            if row.get("market") == market and row.get("points"):
                times = list(row["points"])
                break
    except Exception as exc:  # noqa: BLE001 —— 读不到就用兜底，不阻断影子
        logger.warning("[shadow] 读市场时点失败（用兜底）：{}", exc)
    if not times:
        from app.points import DEFAULT_POINTS
        times = list(DEFAULT_POINTS.get(market) or DEFAULT_POINTS["CN_A"])
    return [{"at": at, "point": f"{market}-{at.replace(':', '')}"} for at in times]


@activity.defn
def shadow_proposals(req: dict[str, Any]) -> list[dict[str, Any]]:
    """某项目下**待验证**的提案（`status ∈ {draft, validating}`）。**只读。**"""
    items = HunterApiClient().evolution_proposals(req["project_id"])
    return [p for p in items if str(p.get("status")) in ("draft", "validating")]


@activity.defn
def shadow_step(req: dict[str, Any]) -> dict[str, Any]:
    """影子一步：取一次快照 → 两臂各出一次决策 → 交 paper 影子撮合 → 写两行影子事件。

    **只读 + 只写影子事件表**：不写 `fin_trade` / `fin_order` / `fin_param`。
    某臂买不起（`SampleNoBudget`）→ `decided=False`、`signal=null` —— 于是它**不计入
    可比样本**（红线 11）。行情缺口时两臂都记 `filled=false`（`inconclusive` 的依据）。
    """
    project_id = req["project_id"]
    proposal_id = req["proposal_id"]
    market = canonical_market(req.get("market") or "CN_A")
    trade_date = req["trade_date"]
    point = req["point"]
    now_iso = req.get("now")
    api = HunterApiClient()
    paper = PaperClient()

    prep = api.evolution_shadow_prepare(proposal_id, market)
    # 第一次跑到某提案时把它推进到 `validating` 并记下窗口起点（幂等：已 validating 就跳过）。
    api.evolution_shadow_validate(proposal_id, market, trade_date)

    view = paper.get_project(project_id, market=market) or {}
    project = view.get("project") or {}
    code = req.get("code") or config.sample_code(market)
    mos = _market_order_supported(paper, market)
    reference_price: Optional[str] = None
    lot_size: Any = None
    if not mos:
        inst = paper.get_instrument(code)
        if inst is not None:
            lot_size = inst.get("lot_size")
        quote = HunterApiClient().quote(code)
        reference_price = (quote or {}).get("last_price")
    active = _active_strategy(view.get("param")) or {}
    strategy_key = active.get("key") or config.sample_strategy_key()
    strategy_version = str(active.get("version") or config.sample_strategy_version())
    now = _parse_iso(now_iso) if now_iso else datetime.now(SHANGHAI)
    project_for_strategy = {"project_id": project_id, "tier": project.get("tier"), "version": 0}

    arms_payload: list[dict[str, Any]] = []
    for arm_name, cfg_key in (("incumbent", "base_config"), ("candidate", "candidate_config")):
        cfg = prep.get(cfg_key) or {}
        state = (prep.get("states") or {}).get(arm_name) or {}
        order = shadow_strategy.build_for_arm(
            project=project_for_strategy, config=cfg, trade_date=trade_date, point=point,
            now=now, code=code, strategy_key=strategy_key,
            strategy_version=strategy_version,
            ttl_seconds=config.contract_timeout_seconds(), market=market,
            market_order_supported=mos, reference_price=reference_price,
            lot_size=lot_size, available_cash=state.get("cash_available"),
        )
        payload: dict[str, Any] = {"arm": arm_name, "decided": order is not None, "state": state}
        if order is not None:
            payload.update(order)
        arms_payload.append(payload)

    sim = paper.shadow_simulate({
        "project_id": project_id, "market": market, "symbol": code,
        "trade_date": trade_date, "point": point,
        "initial_capital": str(prep.get("initial_capital") or "0"), "arms": arms_payload,
    })

    records: list[dict[str, Any]] = []
    for r in sim.get("results") or []:
        rec = dict(r)
        rec["validation_id"] = prep["validation_id"]
        records.append(rec)
    written = api.evolution_shadow_record(records) if records else {"written": 0, "skipped": 0}
    summary = {
        "proposal_id": proposal_id, "trade_date": trade_date, "point": point,
        "quote_as_of": sim.get("quote_as_of"), "gap": bool(sim.get("gap")),
        "written": written.get("written"), "skipped": written.get("skipped"),
        "arms": [{"arm": r["arm"], "decided": r["signal"] is not None, "filled": r["filled"]}
                 for r in sim.get("results") or []],
    }
    logger.info("[shadow] {}/{} {} · {} → 写 {} 跳过 {}", proposal_id, trade_date, point,
                "行情缺口" if sim.get("gap") else f"quote_as_of={sim.get('quote_as_of')}",
                written.get("written"), written.get("skipped"))
    return summary


@activity.defn
def shadow_evaluate(req: dict[str, Any]) -> dict[str, Any]:
    """验收到期判定：算两臂指标 + 按冻结计划判 verdict（终局才追加状态事件）。"""
    out = HunterApiClient().evolution_shadow_evaluate(
        req["proposal_id"], canonical_market(req.get("market") or "CN_A"), req["trade_date"])
    logger.info("[shadow] evaluate {} → verdict={}（{}）", req.get("proposal_id"),
                out.get("verdict"), out.get("reason"))
    return out


# ── R13 · 自动盯盘 · 观察（`fin.observe`）──────────────────────────────────
#
# 与 `fin.shadow` 同构：一次触发对**该市场所有进行中的项目**下每个**已生效**的提案
# 各观察一次。判断全在 api 的 `evolution.observe_applied`（**只读净值 + 按冻结计划比**）——
# 这里**一行 SQL 都没有**，也不 import 任何数据库驱动（`test_no_ledger_access.py` 盯着）。

@activity.defn
def observe_proposals(req: dict[str, Any]) -> list[dict[str, Any]]:
    """某项目下**已生效**的提案（`status == 'applied'`）。**只读。**

    `status` 是事件表的**可重建投影**（`applied` / `alert` 都投影成 `applied`）——
    所以观察对象 = 处于生效 / 观察态的提案，正是 `observe_applied` 认的那一种
    （它要求最后事件是 `applied` 或 `alert`，否则如实报「不是已生效态」）。
    """
    items = HunterApiClient().evolution_proposals(req["project_id"])
    return [p for p in items if str(p.get("status")) == "applied"]


@activity.defn
def observe_applied(req: dict[str, Any]) -> dict[str, Any]:
    """观察一条已生效提案：**只告警 / 只回滚**，判据全在服务端。

    **读 + 有副作用（告警 / 回滚）都在 api 侧完成**（`services/fin/evolution.py`）：
    这里只把 `proposal_id` / `market` / `as_of` 传下去，把结果原样带回。

    - 越普通线（提前停止线 / 观察期结束净收益 ≤ 失败线）→ `action='alert'`，**配置不动**；
    - 踩紧急线（冻结的 `rollback_line`）→ `action='rolled_back'`，服务端按**版本链**
      回滚到上一个已验证版本，并产出一条 `polarity='refute'` 的失败经验（回灌失败留在
      api 侧的回灌重试表，由 `rollback.reinject.status == 'pending'` 报出）。

    失败（含**回滚被拒**）按 HTTP 码抛 `ApiError`。**调用方（工作流）逐提案 catch**
    —— 单条失败不挂整条工作流，但**回滚失败必须可见**（工作流记进汇总 + ERROR 日志）。
    """
    proposal_id = req["proposal_id"]
    out = HunterApiClient().evolution_observe(
        proposal_id, market=req.get("market"), as_of=req.get("as_of"))
    action = out.get("action")
    live = out.get("live") or {}
    logger.info("[observe] {} → action={} · 回撤={} · 净收益={}（{}）",
                proposal_id, action, live.get("max_drawdown"), live.get("net_return"),
                out.get("reason") or "无告警")
    if action == "rolled_back":
        rb = (out.get("rollback") or {}).get("reinject") or {}
        logger.warning("[observe] {} 触发紧急线，已自动回滚 → 回灌状态 {}",
                       proposal_id, rb.get("status"))
    return out


# ── L01 · 自动提案（`fin.propose` 工作流的两个活动）──────────────────────────
#
# 「经验 → 提案」这一节此前**全仓没有调用方**（接口写好了没人调）。这里补上**确定性编排**：
# 先读候选（`propose_candidates`，只读）→ 再逐条走**唯一写入口**（`propose_submit`）。
#
# **不出现任何 LLM 调用**（方案 §5.2：固定流程不依赖大模型逐步决策）——
# 开关 / 预算 / 证据聚合 / 候选生成全在 api 侧（`services/fin/evolution.py`），
# 这里只负责「到点、按市场、逐项目」地搬运与留痕。fin-worker 依旧**一行 SQL 都不碰**。

@activity.defn
def propose_candidates(req: dict[str, Any]) -> dict[str, Any]:
    """**只读**：给项目产出候选清单（开关 / 预算 / 证据闸门都在 api 侧算）。

    `action='skip'` 是合法终局（关着 / 超预算 / 证据不足），工作流照实记下来，
    **不当错误**、更不硬造一条提案。
    """
    out = HunterApiClient().evolution_propose_candidates(
        req["project_id"], market=req.get("market"))
    logger.info("[propose] candidates project={} market={} → action={} drafts={}（{}）",
                req.get("project_id"), req.get("market"), out.get("action"),
                len(out.get("drafts") or []), out.get("reason"))
    return out


@activity.defn
def propose_submit(req: dict[str, Any]) -> dict[str, Any]:
    """把一条候选交**唯一写入口**落库。闸门拒绝（400）/ 同 base 已有待验证提案（409）→ 抛。

    抛出由工作流**逐条 catch**（一条撞唯一索引不该挂掉别的组 / 别的项目）—— 见
    `workflows._propose_for_project`。
    """
    draft = req["draft"]
    out = HunterApiClient().evolution_propose(draft)
    prop = out.get("proposal") or {}
    logger.info("[propose] submit → proposal={} target={} direction={}",
                prop.get("proposal_id"), prop.get("target"), prop.get("direction"))
    return {"proposal": prop, "ok": bool(out.get("ok"))}
