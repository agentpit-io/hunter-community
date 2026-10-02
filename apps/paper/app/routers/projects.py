"""七类实体的读，加上「入金 / 估值 / 对账」三个动作。

**读接口不写库**（`db.cursor()` 默认不提交）；三个动作里只有入金与估值 / 对账会追加行。
没有任何端点能改已写入的行。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app import db, ledger, recon, valuation
from app.schemas import ValuationIn

router = APIRouter(tags=["ledger"])


def _require_project(cur, project_id: str) -> dict:
    project = ledger.get_project(cur, project_id)
    if not project:
        raise HTTPException(404, f"项目不存在：{project_id}")
    return project


@router.get("/api/v1/projects/{project_id}")
def get_project(project_id: str) -> dict:
    with db.cursor() as cur:
        project = _require_project(cur, project_id)
        available, frozen = ledger.cash_balance(cur, project_id)
        positions = ledger.list_positions(cur, project_id)
        param = ledger.get_param(cur, project_id)
    return {
        "project": project,
        "param": param,
        "cash": {"available": available, "frozen": frozen},
        "positions": positions,
    }


@router.post("/api/v1/projects/{project_id}/funding")
def fund(project_id: str) -> dict:
    """把本金记成一条 `deposit` 流水（幂等）。账本的起点是显式的，不做隐式补记。"""
    with db.cursor(commit=True) as cur:
        _require_project(cur, project_id)
        return ledger.seed_funding(cur, project_id)


@router.get("/api/v1/projects/{project_id}/orders")
def orders(project_id: str, limit: int = 200) -> dict:
    with db.cursor() as cur:
        _require_project(cur, project_id)
        return {"items": ledger.list_orders(cur, project_id, limit)}


@router.get("/api/v1/projects/{project_id}/trades")
def trades(project_id: str, limit: int = 200) -> dict:
    with db.cursor() as cur:
        _require_project(cur, project_id)
        return {"items": ledger.list_trades(cur, project_id, limit)}


@router.get("/api/v1/projects/{project_id}/cash")
def cash(project_id: str, limit: int = 200) -> dict:
    with db.cursor() as cur:
        _require_project(cur, project_id)
        available, frozen = ledger.cash_balance(cur, project_id)
        return {
            "available": available,
            "frozen": frozen,
            "total": available + frozen,
            "entries": ledger.list_cash(cur, project_id, limit),
        }


@router.get("/api/v1/projects/{project_id}/positions")
def positions(project_id: str) -> dict:
    with db.cursor() as cur:
        _require_project(cur, project_id)
        return {"items": ledger.list_positions(cur, project_id)}


@router.post("/api/v1/projects/{project_id}/valuation")
def make_valuation(project_id: str, body: ValuationIn) -> dict:
    try:
        with db.cursor(commit=True) as cur:
            _require_project(cur, project_id)
            return valuation.store(cur, project_id, body.as_of)
    except valuation.MissingPrice as exc:
        # 缺价就不出估值，点名是哪几只（不许拿成本价顶替市值）。
        raise HTTPException(409, str(exc)) from exc


@router.get("/api/v1/projects/{project_id}/valuation/latest")
def latest_valuation(project_id: str) -> dict:
    with db.cursor() as cur:
        _require_project(cur, project_id)
        row = valuation.latest(cur, project_id)
        available, frozen = ledger.cash_balance(cur, project_id)
    if not row:
        return {"valuation": None, "cash": {"available": available, "frozen": frozen}}
    return {"valuation": row, "cash": {"available": available, "frozen": frozen}}


@router.post("/api/v1/projects/{project_id}/recon")
def run_recon(project_id: str, body: ValuationIn) -> dict:
    with db.cursor(commit=True) as cur:
        _require_project(cur, project_id)
        return recon.run(cur, project_id, body.as_of)


@router.get("/api/v1/projects/{project_id}/recon")
def list_recon(project_id: str, limit: int = 50) -> dict:
    with db.cursor() as cur:
        _require_project(cur, project_id)
        cur.execute(
            "SELECT * FROM fin_recon_log WHERE project_id = %s ORDER BY id DESC LIMIT %s",
            (project_id, limit),
        )
        return {"items": cur.fetchall()}
