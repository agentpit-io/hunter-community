"""七类实体的读，加上「入金 / 估值 / 对账」三个动作。

**读接口不写库**（`db.cursor()` 默认不提交）；三个动作里只有入金与估值 / 对账会追加行。
没有任何端点能改已写入的行。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app import db, ledger, recon, valuation
from app.schemas import ValuationIn

router = APIRouter(tags=["ledger"])


def _require_project(cur, project_id: str, market: str | None = None) -> dict:
    """项目必须在；**`MULTI` 项目还必须有显式 `market`**（否则 400，不许落到 A 股）。

    这条校验放在这里（每个端点都先调它），所以「账本接口不传 market」的行为
    在**所有**读 / 写端点上一致 —— 不是只在某一个接口上挡一下。
    """
    project = ledger.get_project(cur, project_id)
    if not project:
        raise HTTPException(404, f"项目不存在：{project_id}")
    try:
        ledger.require_market(project, market)
    except ledger.MarketRequired as exc:
        raise HTTPException(400, str(exc)) from exc
    return project


@router.get("/api/v1/projects")
def list_projects(status: str = "active", limit: int = 500) -> dict:
    """按状态列项目。**M4 的时点工作流靠它找「今天要为哪些项目干活」**。

    `status` 只认 `active` / `closed`（其它值一律当 `active`，不做花式过滤）。
    """
    want = status if status in ("active", "closed") else "active"
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT project_id, user_id, tier, status, initial_capital, currency,
                   market_scope, version, run_mode, opened_at, closed_at, close_reason
              FROM fin_project
             WHERE status = %s
             ORDER BY opened_at DESC
             LIMIT %s
            """,
            (want, limit),
        )
        return {"items": cur.fetchall(), "status": want}


@router.post("/api/v1/projects/{project_id}/confirm-t1")
def confirm_t1(project_id: str) -> dict:
    """T+1 日切：把买入的持仓转为可卖。**每个交易日开盘前**由时点工作流调用。

    排在 `preopen`（09:15）：那时没有挂单（昨天的已在昨天收盘撤掉），
    日切不会影响任何在途占用（`M3 报告 · 遗留 5`）。
    """
    with db.cursor(commit=True) as cur:
        _require_project(cur, project_id)
        changed = ledger.confirm_t1(cur, project_id)
    return {"project_id": project_id, "positions_made_sellable": changed}


@router.get("/api/v1/projects/{project_id}")
def get_project(project_id: str, market: str | None = None) -> dict:
    with db.cursor() as cur:
        project = _require_project(cur, project_id, market)
        mk = ledger.resolve_market(project, market)
        available, frozen = ledger.cash_balance(cur, project_id, market)
        positions = ledger.list_positions(cur, project_id, market)
        param = ledger.get_param(cur, project_id)
    return {
        "project": project,
        "param": param,
        "market": mk,
        "currency": ledger.MARKET_CURRENCY.get(mk),
        "cash": {"available": available, "frozen": frozen},
        "positions": positions,
    }


@router.post("/api/v1/projects/{project_id}/funding")
def fund(project_id: str, market: str | None = None) -> dict:
    """把**该市场子账户**的本金记成一条 `deposit` 流水（幂等）。

    账本的起点是显式的，不做隐式补记。`market` 缺省取项目 `market_scope`
    （A 股项目 = `CN_A`，行为与一期逐字一致）。
    """
    with db.cursor(commit=True) as cur:
        _require_project(cur, project_id, market)
        return ledger.seed_funding(cur, project_id, market)


@router.get("/api/v1/projects/{project_id}/orders")
def orders(project_id: str, limit: int = 200, market: str | None = None) -> dict:
    with db.cursor() as cur:
        _require_project(cur, project_id, market)
        return {"items": ledger.list_orders(cur, project_id, limit, market)}


@router.get("/api/v1/projects/{project_id}/trades")
def trades(project_id: str, limit: int = 200, market: str | None = None) -> dict:
    with db.cursor() as cur:
        _require_project(cur, project_id, market)
        return {"items": ledger.list_trades(cur, project_id, limit, market)}


@router.get("/api/v1/projects/{project_id}/cash")
def cash(project_id: str, limit: int = 200, market: str | None = None) -> dict:
    with db.cursor() as cur:
        _require_project(cur, project_id, market)
        available, frozen = ledger.cash_balance(cur, project_id, market)
        return {
            "available": available,
            "frozen": frozen,
            "total": available + frozen,
            "entries": ledger.list_cash(cur, project_id, limit, market),
        }


@router.get("/api/v1/projects/{project_id}/positions")
def positions(project_id: str, market: str | None = None) -> dict:
    with db.cursor() as cur:
        _require_project(cur, project_id, market)
        return {"items": ledger.list_positions(cur, project_id, market)}


@router.post("/api/v1/projects/{project_id}/valuation")
def make_valuation(project_id: str, body: ValuationIn) -> dict:
    try:
        with db.cursor(commit=True) as cur:
            _require_project(cur, project_id, body.market)
            return valuation.store(cur, project_id, body.as_of, body.market)
    except valuation.MissingPrice as exc:
        # 缺价就不出估值，点名是哪几只（不许拿成本价顶替市值）。
        raise HTTPException(409, str(exc)) from exc


@router.get("/api/v1/projects/{project_id}/valuation/latest")
def latest_valuation(project_id: str, market: str | None = None) -> dict:
    with db.cursor() as cur:
        _require_project(cur, project_id, market)
        row = valuation.latest(cur, project_id, market)
        available, frozen = ledger.cash_balance(cur, project_id, market)
    if not row:
        return {"valuation": None, "cash": {"available": available, "frozen": frozen}}
    return {"valuation": row, "cash": {"available": available, "frozen": frozen}}


@router.post("/api/v1/projects/{project_id}/recon")
def run_recon(project_id: str, body: ValuationIn) -> dict:
    with db.cursor(commit=True) as cur:
        _require_project(cur, project_id, body.market)
        return recon.run(cur, project_id, body.as_of, body.market)


@router.get("/api/v1/projects/{project_id}/recon")
def list_recon(project_id: str, limit: int = 50) -> dict:
    with db.cursor() as cur:
        _require_project(cur, project_id)
        cur.execute(
            "SELECT * FROM fin_recon_log WHERE project_id = %s ORDER BY id DESC LIMIT %s",
            (project_id, limit),
        )
        return {"items": cur.fetchall()}
