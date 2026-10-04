"""内网 · 自有策略服务的三个正式入口（五期 `L04` · §10.1 的工具名）。

| 工具 | HTTP | 语义 |
|---|---|---|
| `strategy.submit` | `POST /internal/fin/strategy/submit` | 提交一个候选版本（**未生效**） |
| `strategy.get`    | `GET  /internal/fin/strategy/get`    | 查状态（候选 / 生效中 / 已取消 …） |
| `strategy.cancel` | `POST /internal/fin/strategy/cancel` | 取消**未生效**的候选（生效了的拒绝） |
| （只读辅助）       | `GET  /internal/fin/strategy/active` | 项目当前生效版本（`fin-worker` 的 `strategy_version` 来源） |

鉴权与 `/api/internal/*` 其余端点同一把口令（`X-Hunter-Internal-Key`）。业务全在
`app/services/fin/strategy.py`（唯一服务模块）；这里只判通道与把异常翻成 HTTP 码。

⛔ **本路由不写 `fin_param`** —— 让候选生效仍走 `control.py` 那个唯一写入口（红线 7）。
⛔ `submit` 传 `target != 'strategy'`（放风控）→ **400 + 拒绝事件**（红线 8）。
"""

from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.services.fin import strategy as strategy_svc

router = APIRouter(prefix="/internal/fin/strategy", tags=["internal-fin-strategy"])

_INTERNAL_KEY = os.getenv("HUNTER_INTERNAL_KEY", "")


def _auth(request: Request) -> None:
    key = request.headers.get("X-Hunter-Internal-Key", "")
    if not _INTERNAL_KEY or key != _INTERNAL_KEY:
        raise HTTPException(401, "internal auth failed")


class SubmitIn(BaseModel):
    """`strategy.submit` 请求体。`params` 是该版本的定义内容（参与内容哈希）。"""

    project_id: str = ""
    strategy_key: str = ""
    name: str = ""
    source_ref: str = ""
    params: dict[str, Any] = {}
    target: str = "strategy"        # 只认 'strategy'；'risk' → 拒绝（红线 8）
    origin: str = "user"
    note: Optional[str] = None


class CancelIn(BaseModel):
    candidate_id: str = ""
    project_id: Optional[str] = None


def _conn():
    return strategy_svc.get_conn()


@router.post("/submit")
def submit(request: Request, body: SubmitIn) -> dict:
    """`strategy.submit`：登记候选版本 + 追加 `submitted` 事件。**不生效。**"""
    _auth(request)
    conn = _conn()
    try:
        return strategy_svc.submit(
            conn, project_id=body.project_id, strategy_key=body.strategy_key,
            name=body.name or body.strategy_key, source_ref=body.source_ref,
            params=body.params, actor="internal", target=body.target,
            origin=body.origin, note=body.note)
    except strategy_svc.RiskLooseningRefused as exc:
        raise HTTPException(400, str(exc))
    except strategy_svc.StrategyError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@router.get("/get")
def get_state(request: Request, candidate_id: Optional[str] = Query(None),
              project_id: Optional[str] = Query(None)) -> dict:
    """`strategy.get`：查候选状态；给 `project_id` 则另附当前生效版本。"""
    _auth(request)
    conn = _conn()
    try:
        result = strategy_svc.get(conn, candidate_id=candidate_id, project_id=project_id)
        # `get` 里的自愈式内置登记（注册表为空时）要落库 —— 读路径提交是幂等无害的。
        conn.commit()
        return result
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except strategy_svc.StrategyError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@router.post("/cancel")
def cancel(request: Request, body: CancelIn) -> dict:
    """`strategy.cancel`：取消未生效的候选；**已生效的拒绝**（走回滚路）。"""
    _auth(request)
    conn = _conn()
    try:
        return strategy_svc.cancel(conn, candidate_id=body.candidate_id,
                                   project_id=body.project_id, actor="internal")
    except strategy_svc.CandidateIsActive as exc:
        raise HTTPException(409, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except strategy_svc.StrategyError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@router.get("/active")
def active(request: Request, project_id: str = Query("")) -> dict:
    """项目当前生效的策略版本（`fin-worker` 调它拿稳定的 `strategy_version`）。**只读。**"""
    _auth(request)
    if not project_id.strip():
        raise HTTPException(400, "project_id 必填")
    conn = _conn()
    try:
        with conn.cursor() as cur:
            result = strategy_svc.active_version(cur, project_id)
        # 自愈式内置登记（注册表为空时）要落库。
        conn.commit()
        return result
    finally:
        conn.close()
