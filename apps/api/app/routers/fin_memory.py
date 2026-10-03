"""智能炒股 · 统一经验系统 API（第四段 R2）。

两条通道，**入口不同、鉴权不同** —— 口径照抄 `routers/fin_report.py`：

| 端点 | 谁调 | 鉴权 | `source`（服务端强制） |
|---|---|---|---|
| `POST /api/internal/fin/memory/evidence` | `fin-worker` 的复核工作流（R3） | `X-Hunter-Internal-Key` | **`ai`** |
| `POST /api/internal/fin/memory/query` | 工作流读经验（R3） | 同上 | — |
| `GET  /api/v1/fin/memory/experiences` | 前端（R4） | JWT，校验项目归属 | — |
| `GET  /api/v1/fin/memory/snapshots/{id}` | 前端 / 审计（R4） | 同上 | — |
| `POST /api/v1/fin/memory/evidence` | 真人写（R4 的表单） | 同上 | **`human_mixed`** |

**这一层不做任何过滤**：所有硬校验 / 硬过滤都在 `services/fin/memory.py`（唯一入口）。
路由只负责（a）判通道、（b）把通道翻成 `source`、（c）把服务层异常翻成 HTTP 码：

- `MemoryValidationError`（`ValueError` 子类）→ **400**
- `LookupError` → **404**（项目 / 快照不存在，或跨用户 —— 不区分两者，不泄露存在性）

⚠️ 请求体里带 `source` / `exposure_scope` **一律不读**（规则 7 / 规则 4）——
内网口令写的永远是 `ai`，JWT 写的永远是 `human_mixed` + `created_by='user:<uuid>'`，
保底污染永远传染成 `holdout_only`。**不实现放开参数，就不可能被误开**（零开关，红线）。
"""

from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel

from app.services.fin import memory as memory_svc

router = APIRouter(tags=["fin-memory"])

_INTERNAL_KEY = os.getenv("HUNTER_INTERNAL_KEY", "")


def _auth_internal(request: Request) -> None:
    """内网口令（与 `fin_report.py:52` 同一条），**不新增密钥**。"""
    key = request.headers.get("X-Hunter-Internal-Key", "")
    if not _INTERNAL_KEY or key != _INTERNAL_KEY:
        raise HTTPException(401, "internal auth failed")


def _uid(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(status_code=401, detail={"message": "请先登录", "need_login": True})
    return str(uid)


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


# ════════════════════════════════════════════════════════════════════════
# 入参模型 —— `source` / `exposure_scope` 收下但不读（规则 7 / 规则 4）
# ════════════════════════════════════════════════════════════════════════

class EvidenceIn(BaseModel):
    """`memory.append_evidence` 的入参。

    ⚠️ `source` 字段**故意存在但从不读取** —— 它是「入参里传什么都不认」的现场证据：
    内网通道恒 `ai`、JWT 通道恒 `human_mixed`。同理由 `exposure_scope` 也是派生值，
    不从这里读（由证据的 `holdout_tainted` 传染决定）。
    """

    project_id: str
    kind: str
    statement: str
    evidence: list[dict[str, Any]]
    market: Optional[str] = None
    status: Optional[str] = None
    applicability: Optional[str] = None
    invalidation_condition: Optional[str] = None
    method: Optional[str] = None
    sample_size: Optional[int] = None
    uncertainty: Optional[float] = None
    confidence: Optional[float] = None
    as_of: Optional[str] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    memory_snapshot_id: Optional[str] = None
    supersedes: Optional[str] = None
    # 以下两个被服务端忽略（保留在模型里，专为把「传了也没用」测出来）
    source: Optional[str] = None
    exposure_scope: Optional[str] = None


class QueryIn(BaseModel):
    """`memory.query` 的入参（内网通道）。

    ⚠️ **没有** `include_holdout` / `debug` / `admin` —— 这类键即使写进请求体，
    pydantic 默认忽略，服务层签名里也没有它们的落点。
    """

    project_id: str
    market: Optional[str] = None
    kind: Optional[str] = None
    status: Optional[str] = None
    for_decision: bool = False
    as_of: Optional[str] = None
    freeze: bool = False
    purpose: str = "decision"
    trade_date: Optional[str] = None
    point: Optional[str] = None


def _append_kwargs(body: EvidenceIn, caller: str, user_id: Optional[str]) -> dict:
    """把请求体翻成服务层参数 —— **只搬字段，不做判断**（判断全在服务层）。"""
    return dict(
        caller=caller,
        project_id=body.project_id,
        kind=body.kind,
        statement=body.statement,
        evidence=body.evidence,
        user_id=user_id,
        market=body.market,
        status=body.status,
        applicability=body.applicability,
        invalidation_condition=body.invalidation_condition,
        method=body.method,
        sample_size=body.sample_size,
        uncertainty=body.uncertainty,
        confidence=body.confidence,
        as_of=body.as_of,
        valid_from=body.valid_from,
        valid_until=body.valid_until,
        memory_snapshot_id=body.memory_snapshot_id,
        supersedes=body.supersedes,
        # body.source / body.exposure_scope 到此为止 —— 服务层根本不接这两个参数
    )


# ════════════════════════════════════════════════════════════════════════
# 内网口令通道（fin-worker / R3）
# ════════════════════════════════════════════════════════════════════════

@router.post("/internal/fin/memory/evidence")
async def append_evidence_internal(body: EvidenceIn, request: Request):
    """**唯一写入口**（内网通道）—— AI 自主经验，`source` 强制 `ai`。"""
    _auth_internal(request)
    try:
        row = memory_svc.append_evidence(**_append_kwargs(body, "internal", None))
    except memory_svc.MemoryValidationError as exc:
        raise _bad(exc) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    logger.info("[fin.memory] append(internal) kind={} id={} scope={}",
                row.get("kind"), row.get("experience_id"), row.get("exposure_scope"))
    return {"ok": True, "experience": row}


@router.post("/internal/fin/memory/query")
async def query_internal(body: QueryIn, request: Request):
    """**唯一读入口**（内网通道）—— 工作流读经验；`freeze=true` 顺带冻结。"""
    _auth_internal(request)
    try:
        out = memory_svc.query(
            project_id=body.project_id, caller="internal", user_id=None,
            market=body.market, kind=body.kind, status=body.status,
            for_decision=body.for_decision, as_of=body.as_of, freeze=body.freeze,
            purpose=body.purpose, trade_date=body.trade_date, point=body.point,
        )
    except memory_svc.MemoryValidationError as exc:
        raise _bad(exc) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return out


# ════════════════════════════════════════════════════════════════════════
# JWT 通道（前端 / R4）
# ════════════════════════════════════════════════════════════════════════

@router.get("/v1/fin/memory/experiences")
async def list_experiences(
    request: Request,
    project_id: str = Query(...),
    market: Optional[str] = Query(None),
    kind: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    for_decision: bool = Query(False),
    as_of: Optional[str] = Query(None),
    freeze: bool = Query(False),
    purpose: str = Query("decision"),
):
    """经验列表（前端）—— **同一套服务端硬过滤**，与内网通道逐字一致。

    ⚠️ 多余的查询参数（`include_holdout` / `debug` / `admin` / 任意未知键）被 FastAPI
    忽略，服务层也没有它们的落点 —— 「不实现就不可能被误开」。
    """
    uid = _uid(request)
    try:
        return memory_svc.query(
            project_id=project_id, caller="jwt", user_id=uid,
            market=market, kind=kind, status=status,
            for_decision=for_decision, as_of=as_of, freeze=freeze, purpose=purpose,
        )
    except memory_svc.MemoryValidationError as exc:
        raise _bad(exc) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/v1/fin/memory/snapshots/{memory_snapshot_id}")
async def get_snapshot(memory_snapshot_id: str, request: Request):
    """回放与审计：**按 id 取那份冻结的集合，不重跑查询**（防未来函数）。"""
    uid = _uid(request)
    try:
        return memory_svc.get_snapshot(memory_snapshot_id=memory_snapshot_id, user_id=uid)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/v1/fin/memory/evidence")
async def append_evidence_user(body: EvidenceIn, request: Request):
    """真人在 growth 页写经验（R4）—— `source` 强制 `human_mixed` + `created_by='user:<uuid>'`。"""
    uid = _uid(request)
    try:
        row = memory_svc.append_evidence(**_append_kwargs(body, "jwt", uid))
    except memory_svc.MemoryValidationError as exc:
        raise _bad(exc) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    logger.info("[fin.memory] append(jwt) user={} kind={} id={}",
                uid, row.get("kind"), row.get("experience_id"))
    return {"ok": True, "experience": row}
