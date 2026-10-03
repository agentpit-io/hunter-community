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
