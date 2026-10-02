"""智能炒股 · 四个正文页的 API（M6）· `/api/v1/fin/*`。

| 端点 | 谁用 | 说明 |
|---|---|---|
| `GET  /overview` | 总览页 | 净值 / 今日盈亏 / 持仓概览 / 最近动作 / 账目自洽 |
| `GET  /auto-trade` | 自动交易页 | 总开关 / 策略 / 风险档位 / 今天做了什么 / 时刻表 / A 股硬约束 |
| `POST /auto-trade/switch` | 自动交易页 | 总开关（**关掉后 fin-worker 不再产生新委托**） |
| `POST /auto-trade/strategy` | 自动交易页 | 切换生效策略（下一次决策生效） |
| `POST /auto-trade/risk` | 自动交易页 | 风险档位（单向棘轮，放宽要二次确认） |
| `GET  /account` | 我的账户页 | 资金与市场设置 / 持仓 / 成交 / 快照凭证 / A 股六条 |
| `GET  /snapshots/{id}` | 两页 | 快照凭证（成交价依据，点开能看到那一份） |
| `GET  /projects/{id}/reports` | 每日报告页 | 报告列表（含摘要；无报告时返回空列表 + 说明） |

**鉴权复用现仓那套 JWT 中间件**（`/api/v1/fin/` 不在免登录前缀里），并在每个
handler 里校验**项目归属** —— 不属于当前用户一律 404（不泄露存在性），
与 `fin_report.py` 同一口径。

**为什么读接口把 project_id 当可选**：前端每次进来先问「当前项目是哪个」，
所以不传时按当前进行中的项目解析；传了就按它（仍要过归属校验），
这样开新项目之后旧账本也能从同一组端点打开回看。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg2.extras
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services.fin import control, dashboard as dash, store, views
from app.services.fin import markets as markets_svc
from app.services.fin import report as report_svc

router = APIRouter(tags=["fin-dashboard"])

SHANGHAI = timezone(timedelta(hours=8))


def _uid(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(status_code=401, detail={"message": "请先登录", "need_login": True})
    return str(uid)


def _conn():
    return store.get_conn()


def _resolve(conn, uid: str, project_id: Optional[str]) -> str:
    """解析项目 id 并校验归属。没有进行中的项目时抛 409（前端据此显示空态）。"""
    if project_id:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, status FROM fin_project WHERE project_id = %s", (project_id,))
            row = cur.fetchone()
        if not row or row[0] != uid:
            raise HTTPException(status_code=404, detail="项目不存在")
        return project_id
    current = store.get_current(uid)
    if not current:
        raise HTTPException(status_code=409, detail={
            "message": "还没有进行中的项目。先走设置向导开一个。",
            "need_project": True,
            "setup_url": "/finance/setup",
        })
    return current["project"]["project_id"]


def _today() -> str:
    return datetime.now(SHANGHAI).date().isoformat()


def _read(project_id: Optional[str], request: Request, build):
    """读接口的公共外壳：解析项目 → 建连接 → 交给 build(cur, pid) → 关连接。"""
    uid = _uid(request)
    conn = _conn()
    try:
        pid = _resolve(conn, uid, project_id)
        control.ensure_schema(conn)     # 读路径也要补列：全新库里 fin_param 可能还没这两列
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            payload = build(cur, pid)
        conn.rollback()     # 只读，显式回滚，不留 idle in transaction
        return payload
    finally:
        conn.close()


@router.get("/v1/fin/overview")
async def overview(request: Request, project_id: Optional[str] = None, market: Optional[str] = None):
    return _read(project_id, request,
                 lambda cur, pid: views.overview_payload(cur, pid, _today(), market=market))


@router.get("/v1/fin/markets")
async def markets(request: Request, trade_date: Optional[str] = None):
    """三个市场的状态（前端市场切换器与市场状态条）。**只读、不扣任何额度、不需要项目。**

    `trade_date` 指定就看那一天（用于回看「A 股休市那天港美股在不在交易」）；
    不给就是「现在」。日历缺行 → 状态 `unknown`（未知 ≠ 交易日）。
    """
    _uid(request)
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            payload = {"markets": markets_svc.market_status(cur, trade_date=trade_date)}
        conn.rollback()
        return payload
    finally:
        conn.close()


@router.get("/v1/fin/auto-trade")
async def auto_trade(request: Request, project_id: Optional[str] = None, market: Optional[str] = None):
    return _read(project_id, request,
                 lambda cur, pid: views.auto_trade_payload(cur, pid, _today(), market=market))


@router.get("/v1/fin/account")
async def account(request: Request, project_id: Optional[str] = None, market: Optional[str] = None):
    return _read(project_id, request, lambda cur, pid: views.account_payload(cur, pid, market=market))


@router.get("/v1/fin/snapshots/{snapshot_id}")
async def snapshot(snapshot_id: str, request: Request, project_id: Optional[str] = None):
    """快照凭证。**必须挂在某个项目下**才读得到（防止拿编号扫别人的账本）。"""
    uid = _uid(request)
    conn = _conn()
    try:
        pid = _resolve(conn, uid, project_id)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            row = dash.snapshot(cur, snapshot_id)
            if not row:
                raise HTTPException(status_code=404, detail="没有这份快照")
            used = dash.used_snapshot_ids(cur, pid)
        conn.rollback()
        row["used_by_project"] = used.get(snapshot_id, 0)
        return {"snapshot": row, "project_id": pid}
    finally:
        conn.close()


# ── 写：自动交易页的三个控制 ──────────────────────────────────────────────

class SwitchIn(BaseModel):
    enabled: bool
    project_id: Optional[str] = None


class StrategyIn(BaseModel):
    key: str
    project_id: Optional[str] = None


class RiskIn(BaseModel):
    tier: str
    confirm: bool = False
    project_id: Optional[str] = None


def _write(project_id: Optional[str], request: Request, action):
    uid = _uid(request)
    conn = _conn()
    try:
        pid = _resolve(conn, uid, project_id)
        try:
            out = action(conn, pid, uid)
        except PermissionError as exc:
            # 棘轮：这次调整会放宽风控上限 → 409 + 放宽了哪几条，前端据此弹二次确认。
            raise HTTPException(status_code=409, detail=exc.args[0] if exc.args else "需要二次确认")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return out
    finally:
        conn.close()


@router.post("/v1/fin/auto-trade/switch")
async def set_switch(body: SwitchIn, request: Request):
    return _write(body.project_id, request,
                  lambda conn, pid, uid: control.set_auto_enabled(conn, pid, body.enabled, uid))


@router.post("/v1/fin/auto-trade/strategy")
async def set_strategy(body: StrategyIn, request: Request):
    return _write(body.project_id, request,
                  lambda conn, pid, uid: control.set_strategy(conn, pid, body.key, uid))


@router.post("/v1/fin/auto-trade/risk")
async def set_risk(body: RiskIn, request: Request):
    return _write(body.project_id, request,
                  lambda conn, pid, uid: control.apply_risk_tier(
                      conn, pid, body.tier, confirm=body.confirm, actor=uid))


# ── 每日报告页（一次请求拿全：列表 + 最新一份正文 + 事实行）────────────────

def _report_lines(conn, project_id: str, limit: int) -> list[dict]:
    report_svc.ensure_columns(conn)     # 老库里 fin_report 还没有 market / fact 的新列
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT r.report_id, r.trade_date, r.market, r.status, r.llm_provider, r.llm_model,
                   r.artifact_ref, r.created_at,
                   (SELECT count(*) FROM fin_report_fact f WHERE f.report_id = r.report_id) AS fact_count
              FROM fin_report r WHERE r.project_id = %s
             ORDER BY r.trade_date DESC, r.market ASC LIMIT %s
            """, (project_id, limit))
        return [dash._d(r) for r in cur.fetchall()]


@router.get("/v1/fin/reports")
async def latest_report(request: Request, project_id: Optional[str] = None, limit: int = 30):
    """每日报告页的一次性读：报告列表 + 最新一份的正文、事实行、发布回执。

    **一份都没有时返回空态而不是 404**（`05 §3.2` M-17 验收项「无报告日显示空态」）。

    ⚠️ 路径**不能**写成 `/v1/fin/reports/latest` —— `fin_report.py` 已经注册了
    `/v1/fin/reports/{report_id}`，先注册的会先把 `latest` 当成 report_id 吃掉。
    这里用不带子段的 `/v1/fin/reports`（列表），与那条按 id 取单条的路径互不冲突。
    """
    uid = _uid(request)
    conn = _conn()
    try:
        pid = _resolve(conn, uid, project_id)
        report_svc.ensure_columns(conn)
        items = [_report_item(r) for r in _report_lines(conn, pid, max(1, min(limit, 200)))]
        latest = None
        empty_state = None
        if items:
            loaded = report_svc.load_report(conn, items[0]["report_id"])
            if loaded:
                latest = loaded
                latest["validate"] = report_svc.validate_stored(conn, items[0]["report_id"])
        else:
            empty_state = {
                "reason": "这个项目还没有生成过任何报告。每个交易日收盘后会生成一份；"
                          "非交易日、尚未收盘估值、或当天数据缺失时不会有报告。",
            }
        conn.rollback()
        return {"project_id": pid, "items": items, "latest": latest, "empty_state": empty_state}
    finally:
        conn.close()


def _report_item(r: dict) -> dict:
    return {
        "report_id": r["report_id"], "trade_date": r["trade_date"], "market": r.get("market"),
        "status": r["status"],
        "llm_provider": r["llm_provider"], "llm_model": r["llm_model"],
        "has_artifact": bool(r.get("artifact_ref")), "artifact_ref": r.get("artifact_ref"),
        "fact_count": int(r.get("fact_count") or 0), "created_at": r["created_at"],
    }


# ── 报告列表（每日报告页的历史区）──────────────────────────────────────────

@router.get("/v1/fin/projects/{project_id}/reports")
async def list_reports(project_id: str, request: Request, limit: int = 30):
    """该项目的报告列表，**最新的在前**。一份都没有时返回空列表 + 说明（不是 404）。"""
    uid = _uid(request)
    conn = _conn()
    try:
        pid = _resolve(conn, uid, project_id)
        report_svc.ensure_columns(conn)     # 全新库里 fin_report 可能还没有 artifact_ref
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT report_id, trade_date, market, status, valuation_as_of, llm_provider,
                       llm_model, artifact_ref, created_at
                  FROM fin_report WHERE project_id = %s
                 ORDER BY trade_date DESC, market ASC LIMIT %s
                """, (pid, max(1, min(limit, 200))))
            rows = [dash._d(r) for r in cur.fetchall()]
        conn.rollback()
        return {
            "project_id": pid,
            "items": [
                {"report_id": r["report_id"], "trade_date": r["trade_date"],
                 "market": r.get("market"),
                 "status": r["status"], "llm_provider": r["llm_provider"],
                 "llm_model": r["llm_model"], "has_artifact": bool(r.get("artifact_ref")),
                 "artifact_ref": r.get("artifact_ref"), "created_at": r["created_at"]}
                for r in rows
            ],
            "empty_state": None if rows else {
                "reason": "这个项目还没有生成过任何报告。每个交易日收盘后会生成一份。",
            },
        }
    finally:
        conn.close()
