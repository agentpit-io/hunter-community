"""智能炒股 · 每日报告 API（M5，对应 `05 §3.2` M-17 / `05 §3.4` M-32）。

两类端点，**入口不同、鉴权不同**：

| 端点 | 谁调 | 鉴权 |
|---|---|---|
| `POST /api/internal/fin/reports/generate` | `fin-worker` 的 `15:30` 时点（Temporal 工作流） | `X-Hunter-Internal-Key` |
| `GET  /api/v1/fin/projects/{id}/reports/{date}` | 前端（M6 每日报告页） | JWT，且校验项目归属 |
| `GET  /api/v1/fin/reports/{id}` · `/reports/{id}/validate` | 前端 / M-32 校验脚本 | 同上 |

**无报告日返回空态**：非交易日或没有那天的收盘估值时，生成接口返回
`{created: false, status: "no_report", reason}` 并且**一行都不写** —— 前端据此显示空态，
而不是空白，更不是「编一份出来」（`05 §3.2` M-17 验收项）。

设计与红线见 `app/services/fin/report.py` 模块文档。**这里不重写任何指标逻辑。**
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from app.services.fin import report as report_svc

router = APIRouter(tags=["fin-report"])

_INTERNAL_KEY = os.getenv("HUNTER_INTERNAL_KEY", "")


def _auth_internal(request: Request) -> None:
    key = request.headers.get("X-Hunter-Internal-Key", "")
    if not _INTERNAL_KEY or key != _INTERNAL_KEY:
        raise HTTPException(401, "internal auth failed")


def _uid(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(status_code=401, detail={"message": "请先登录", "need_login": True})
    return str(uid)


class GenerateIn(BaseModel):
    project_id: str
    trade_date: str


@router.post("/internal/fin/reports/generate")
async def generate_report(body: GenerateIn, request: Request):
    """由 Temporal 工作流触发：为某项目生成某交易日的报告。

    幂等：报告 id 由 `(project_id, trade_date)` 推导；同日重跑覆盖同一行，
    事实行按 `(report_id, metric_key)` upsert，不产生第二份。
    """
    _auth_internal(request)
    try:
        out = await report_svc.generate(body.project_id, body.trade_date)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    logger.info("[fin.report] generate project={} date={} → {}",
                body.project_id, body.trade_date, out.get("status"))
    return out


def _owned_report(uid: str, report_id: str) -> dict:
    conn = report_svc.get_conn()
    try:
        loaded = report_svc.load_report(conn, report_id)
        if not loaded:
            raise HTTPException(404, "报告不存在")
        pid = loaded["report"]["project_id"]
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM fin_project WHERE project_id = %s", (pid,))
            row = cur.fetchone()
        if not row or row[0] != uid:
            raise HTTPException(404, "报告不存在")   # 不泄露存在性
        return loaded
    finally:
        conn.close()


@router.get("/v1/fin/projects/{project_id}/reports/{trade_date}")
async def get_report_by_date(project_id: str, trade_date: str, request: Request):
    """某项目某交易日的报告。**没有报告时返回空态**（不是 404、更不是空白）。"""
    uid = _uid(request)
    conn = report_svc.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM fin_project WHERE project_id = %s", (project_id,))
            row = cur.fetchone()
        if not row or row[0] != uid:
            raise HTTPException(404, "项目不存在")

        report_id = report_svc.report_id_for(project_id, trade_date)
        loaded = report_svc.load_report(conn, report_id)
        if not loaded:
            return {
                "report": None,
                "facts": [],
                "receipts": [],
                "empty_state": {
                    "trade_date": trade_date,
                    "reason": "该交易日没有报告（非交易日、尚未生成，或当天数据缺失）",
                },
            }
        return loaded
    finally:
        conn.close()


@router.get("/v1/fin/reports/{report_id}")
async def get_report(report_id: str, request: Request):
    uid = _uid(request)
    return _owned_report(uid, report_id)


@router.get("/v1/fin/reports/{report_id}/validate")
async def validate_report(report_id: str, request: Request):
    """**M-32 · 数字口径校验（可重复跑）**：逐数字回追 `fin_report_fact`。

    对每个数字给出它落到哪个 `metric_key`；找不到对应行就是违规。前端与运维都用它
    复核「报告与页面上的每个数字可追溯到账本或指标代码」。
    """
    uid = _uid(request)
    _owned_report(uid, report_id)   # 归属校验
    conn = report_svc.get_conn()
    try:
        return report_svc.validate_stored(conn, report_id)
    finally:
        conn.close()


@router.get("/v1/fin/reports/{report_id}/artifact")
async def get_report_artifact(report_id: str, request: Request):
    """报告 HTML 产物（复用 `hunter_artifacts`）。"""
    uid = _uid(request)
    loaded = _owned_report(uid, report_id)
    ref = loaded["report"].get("artifact_ref")
    if not ref:
        raise HTTPException(404, "该报告没有产物（可能未通过校验，未发布）")
    short_id = str(ref).removeprefix("artifact:")
    conn = report_svc.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content_html, title, published_at, is_published "
                "FROM hunter_artifacts.published_artifact WHERE short_id = %s", (short_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "产物不存在")
    return {"short_id": short_id, "title": row[1], "published_at": row[2],
            "is_published": row[3], "content_html": row[0]}
