"""智能炒股 · 受控自进化 · 提案层 API（第四段 `R6`）。

两条通道，**入口不同、鉴权不同** —— 口径照抄 `routers/fin_memory.py` / `fin_report.py`：

| 端点 | 谁调 | 鉴权 |
|---|---|---|
| `POST /api/internal/fin/evolution/proposal` | `fin-worker`（未来 `fin.propose` 工作流） | `X-Hunter-Internal-Key` |
| `GET  /api/v1/fin/evolution/proposals` | 前端（`R9` 的成长页） | JWT，校验项目归属 |

**这一层不做任何闸门判断**：白名单 / 证据 / `param_diff` / 方向 / regime 全在
`services/fin/evolution.py`（唯一入口）。路由只负责（a）判通道、（b）把服务层异常翻成 HTTP 码：

- `EvolutionGateError` / `EvolutionValidationError`（`ValueError` 系）→ **400**
- `EvolutionConflictError` → **409**（同一 base 已有待验证提案）
- `EvolutionDisabledError` → **503**（`FIN_EVOLUTION_MODE=off`，能力没开不是请求写错了）
- `LookupError` → **404**（项目 / 快照不存在，或跨用户 —— 不区分两者）

⚠️ 请求体里带 `direction` / `base_config_hash` / `candidate_config_hash` **一律不读** ——
方向与两个哈希**都由服务端算**（红线：不采信写入方）。**不实现放开参数，就不可能被误开。**
"""

from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel

from app.services.fin import evolution as evolution_svc

router = APIRouter(tags=["fin-evolution"])

_INTERNAL_KEY = os.getenv("HUNTER_INTERNAL_KEY", "")


def _auth_internal(request: Request) -> None:
    """内网口令（与 `fin_memory.py:40` / `fin_report.py` 同一条），**不新增密钥**。"""
    key = request.headers.get("X-Hunter-Internal-Key", "")
    if not _INTERNAL_KEY or key != _INTERNAL_KEY:
        raise HTTPException(401, "internal auth failed")


def _uid(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(status_code=401, detail={"message": "请先登录", "need_login": True})
    return str(uid)


# ════════════════════════════════════════════════════════════════════════
# 入参模型 —— `direction` / 两个 hash 收都不收（服务端算）
# ════════════════════════════════════════════════════════════════════════

class ProposalIn(BaseModel):
    """`evolution.propose` 的入参。

    ⚠️ **没有** `direction` / `base_config_hash` / `candidate_config_hash` /
    `proposal_algo_version` 这些字段 —— 它们是服务端算出来的，写进来也无处可放。
    """

    project_id: str
    evidence_refs: Any                      # 证据 id 数组（≥1；refute 同样可引用）
    candidate_config: Any                   # 候选**完整配置**（字段 → 数值）
    param_diff: Any                         # 写入方声称的 diff（服务端会重算并比对）
    rationale: str
    evidence_snapshot_id: Optional[str] = None
    target: Optional[str] = None
    regime_tags: Optional[list[str]] = None
    created_by: Optional[str] = None
    proposal_id: Optional[str] = None
    plan_overrides: Optional[dict] = None


# ════════════════════════════════════════════════════════════════════════
# 内网口令通道（fin-worker）
# ════════════════════════════════════════════════════════════════════════

@router.post("/internal/fin/evolution/proposal")
async def propose_internal(body: ProposalIn, request: Request):
    """**唯一写入口**（内网通道）—— 提交一条提案（含冻结计划与首条事件，同一事务）。"""
    _auth_internal(request)
    try:
        out = evolution_svc.propose(
            project_id=body.project_id,
            evidence_refs=body.evidence_refs,
            evidence_snapshot_id=body.evidence_snapshot_id,
            candidate_config=body.candidate_config,
            param_diff=body.param_diff,
            target=body.target,
            regime_tags=body.regime_tags,
            rationale=body.rationale,
            created_by=body.created_by or "fin-worker",
            proposal_id=body.proposal_id,
            plan_overrides=body.plan_overrides,
            user_id=None,
        )
    except evolution_svc.EvolutionDisabledError as exc:
        raise HTTPException(503, str(exc)) from exc
    except evolution_svc.EvolutionConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except evolution_svc.EvolutionGateError as exc:
        raise HTTPException(400, str(exc)) from exc
    except evolution_svc.EvolutionValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    logger.info("[fin.evolution] proposal(created) id={} target={} direction={}",
                out.get("proposal_id"), out.get("target"), out.get("direction"))
    return {"ok": True, "proposal": out}


# ════════════════════════════════════════════════════════════════════════
# JWT 通道（前端 / R9）
# ════════════════════════════════════════════════════════════════════════

@router.get("/v1/fin/evolution/proposals")
async def list_proposals(
    request: Request,
    project_id: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
):
    """列出一个项目下的提案（含冻结计划）。跨用户 / 不存在一律 **404**（不泄露存在性）。"""
    uid = _uid(request)
    try:
        items = evolution_svc.list_proposals(project_id=project_id, user_id=uid, limit=limit)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"items": items}


# ════════════════════════════════════════════════════════════════════════
# R7 · 影子层（内网口令通道；调用方 = `fin.shadow` 工作流的活动）
# ════════════════════════════════════════════════════════════════════════
#
# 三个端点对应工作流的三步：**准备 → 记账 → 判定**。全部只调
# `services/fin/evolution.py`（唯一服务模块）—— 路由层不写任何表名 / SQL。


class ProposalsIn(BaseModel):
    project_id: str
    limit: int = 200


class ShadowPrepareIn(BaseModel):
    proposal_id: str
    market: Optional[str] = None


class ShadowRecordIn(BaseModel):
    records: list[dict]


class ShadowEvaluateIn(BaseModel):
    proposal_id: str
    market: str
    trade_date: str


class ValidateIn(BaseModel):
    proposal_id: str
    market: str
    trade_date: str


@router.post("/internal/fin/evolution/proposals")
async def list_proposals_internal(body: ProposalsIn, request: Request):
    """内网通道列出某项目下的提案（含冻结计划）。`fin.shadow` 工作流据此挑待验证的。"""
    _auth_internal(request)
    try:
        return {"items": evolution_svc.list_proposals(
            project_id=body.project_id, user_id=None, limit=body.limit)}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/internal/fin/evolution/validate")
async def start_validation(body: ValidateIn, request: Request):
    """把提案推进到 `validating` 并记下**验证窗口起点**（幂等）。"""
    _auth_internal(request)
    try:
        return evolution_svc.start_validation(
            proposal_id=body.proposal_id, market=body.market, trade_date=body.trade_date)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/internal/fin/evolution/shadow/prepare")
async def shadow_prepare(body: ShadowPrepareIn, request: Request):
    """影子一步的准备数据：两臂配置 / 两臂当前状态 / 初始资金 / 计划（只读）。"""
    _auth_internal(request)
    try:
        return evolution_svc.shadow_prepare(proposal_id=body.proposal_id, market=body.market)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/internal/fin/evolution/shadow/record")
async def shadow_record(body: ShadowRecordIn, request: Request):
    """把两臂的影子里程碑**追加**进影子事件表（唯一键幂等）。"""
    _auth_internal(request)
    try:
        out = evolution_svc.record_shadow_events(records=body.records)
    except evolution_svc.EvolutionValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    logger.info("[fin.evolution] shadow record · written={} skipped={}",
                out.get("written"), out.get("skipped"))
    return out


@router.post("/internal/fin/evolution/shadow/evaluate")
async def shadow_evaluate(body: ShadowEvaluateIn, request: Request):
    """算两臂指标并按冻结计划判定；**终局才**追加 passed / failed / inconclusive 事件。"""
    _auth_internal(request)
    try:
        return evolution_svc.evaluate_validation(
            proposal_id=body.proposal_id, market=body.market, trade_date=body.trade_date)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


# ════════════════════════════════════════════════════════════════════════
# R7 · 影子事件读（JWT 通道；R9 界面用）
# ════════════════════════════════════════════════════════════════════════

@router.get("/v1/fin/evolution/shadow-events")
async def list_shadow_events(
    request: Request,
    proposal_id: str = Query(...),
    arm: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=5000),
):
    """列出某提案的影子事件（可选按臂过滤）。跨用户 / 不存在 → **404**。"""
    uid = _uid(request)
    # 归属校验：借用 list_proposals 的同一道 `_owned_project`（跨用户 → LookupError）。
    try:
        items = evolution_svc.list_proposals(project_id=_project_of(proposal_id), user_id=uid, limit=1)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not items:
        raise HTTPException(404, "提案不存在")
    return {"items": evolution_svc.shadow_events(validation_id=proposal_id, arm=arm, limit=limit)}


def _project_of(proposal_id: str) -> str:
    try:
        return evolution_svc.get_proposal(proposal_id=proposal_id)["project_id"]
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


# ════════════════════════════════════════════════════════════════════════
# R8 · 生效 / 观察 / 回滚 / 回灌（内网口令通道；调用方 = fin-worker / 守控台）
# ════════════════════════════════════════════════════════════════════════
#
# 与 R6/R7 一样：路由**不做任何闸门判断**（五条放行闸门、CAS、版本链核验全在
# `services/fin/evolution.py` + `services/fin/control.py`）。路由只负责判通道 + 翻 HTTP 码。


class ApplyIn(BaseModel):
    """生效入参。`confirm` **必须显式为真** —— 本方案不做自动生效（`FIN_AUTO_APPLY` 恒 0）。"""

    proposal_id: str
    expected_base_config_hash: str
    actor: str
    confirm: bool = False
    market: Optional[str] = None


class ObserveIn(BaseModel):
    proposal_id: str
    as_of: Optional[str] = None
    market: Optional[str] = None


class RollbackIn(BaseModel):
    proposal_id: str
    reason: str
    actor: str
    expected_current_config_hash: Optional[str] = None


class ReinjectIn(BaseModel):
    proposal_id: Optional[str] = None


def _apply_errors(fn):
    """把服务层异常翻成 HTTP 码（口径与 R6 各端点一致，多两条 R8 的分支）。"""
    from app.services.fin import switches as switches_svc
    try:
        return fn()
    except switches_svc.SwitchConfigError as exc:                    # 硬开关违规
        raise HTTPException(503, str(exc)) from exc
    except evolution_svc.EvolutionDisabledError as exc:
        raise HTTPException(503, str(exc)) from exc
    except evolution_svc.EvolutionRollbackRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    except evolution_svc.EvolutionConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except evolution_svc.EvolutionGateError as exc:
        raise HTTPException(400, str(exc)) from exc
    except evolution_svc.EvolutionValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/internal/fin/evolution/apply")
async def apply_proposal(body: ApplyIn, request: Request):
    """**人工确认后把提案应用到模拟盘**（CAS 切换 + 注册候选版本 + 写日志 + 追加 applied 事件）。"""
    _auth_internal(request)
    out = _apply_errors(lambda: evolution_svc.apply_proposal(
        proposal_id=body.proposal_id, expected_base_config_hash=body.expected_base_config_hash,
        actor=body.actor, confirm=body.confirm, market=body.market))
    logger.info("[fin.evolution] apply id={} {} → {}",
                out.get("proposal_id"), out.get("from_key"), out.get("to_key"))
    return out


@router.post("/internal/fin/evolution/observe")
async def observe_applied(body: ObserveIn, request: Request):
    """观察一次已生效的提案：**只有冻结的 rollback_line 会自动回滚**，其余只告警。"""
    _auth_internal(request)
    return _apply_errors(lambda: evolution_svc.observe_applied(
        proposal_id=body.proposal_id, as_of=body.as_of, market=body.market))


@router.post("/internal/fin/evolution/rollback")
async def rollback_proposal(body: RollbackIn, request: Request):
    """**回到上一个已验证版本**（版本链核验 + 停模拟下单 + 写失败经验）。"""
    _auth_internal(request)
    out = _apply_errors(lambda: evolution_svc.rollback_proposal(
        proposal_id=body.proposal_id, reason=body.reason, actor=body.actor,
        expected_current_config_hash=body.expected_current_config_hash))
    logger.warning("[fin.evolution] rollback id={} {} → {}",
                   out.get("proposal_id"), out.get("from_key"), out.get("to_key"))
    return out


@router.post("/internal/fin/evolution/reinject/retry")
async def retry_reinject(body: ReinjectIn, request: Request):
    """重试回灌队列里的 `pending` 任务（「回滚了但没学到」的补救入口）。"""
    _auth_internal(request)
    return _apply_errors(lambda: evolution_svc.retry_reinject(proposal_id=body.proposal_id))


@router.get("/v1/fin/evolution/reinject")
async def list_reinject(request: Request, proposal_id: str = Query(...)):
    """列出某提案**还没回灌成功**的失败经验任务（R9 界面据此显示「回灌待完成」）。"""
    uid = _uid(request)
    try:
        evolution_svc.list_proposals(project_id=_project_of(proposal_id), user_id=uid, limit=1)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"items": evolution_svc.reinject_pending(proposal_id=proposal_id)}
