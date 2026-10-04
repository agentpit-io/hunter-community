"""智能炒股 · 发布（L07）· `publish.submit/get` 的 HTTP 入口。

口径与红线见 `app/services/fin/publish/__init__.py` 模块文档。这里只做三件事：
**判归属**、**翻错误码**、**把同步 IO 挪到线程**（`CLAUDE.md`：async 端点里的
httpx / psycopg2 必须 `asyncio.to_thread`）。

| 端点 | 谁调 | 作用 |
|---|---|---|
| `POST /v1/fin/reports/{id}/publish` | 前端 | 发布到某渠道（`in_app` / `webhook` / `file`） |
| `GET  /v1/fin/publish-receipts/{receipt_id}` | 前端 / 运维 | 读回执；`?resolve=true` 查外部状态落定 |
| `POST /v1/fin/publish-receipts/{receipt_id}/resolve` | 前端 / 运维 | **手动核实**（查不出外部状态时人工落定） |
| `GET  /v1/fin/publish-receipts` | 前端 | **待核实队列**（`status='UNKNOWN'`） |

「不盲目重发」在服务层强制（`UNKNOWN` 下未带重发标记 → 403/409）；路由**不重复实现**判据。
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from app.services.fin import publish as publish_svc
from app.services.fin import report as report_svc

router = APIRouter(tags=["fin-publish"])


def _uid(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(status_code=401, detail={"message": "请先登录", "need_login": True})
    return str(uid)


def _owned(uid: str, report_id: str) -> dict:
    """报告必须属于当前用户（**不泄露存在性**：不是自己的按 404）。"""
    conn = report_svc.get_conn()
    try:
        loaded = report_svc.load_report(conn, report_id)
        if not loaded:
            raise HTTPException(404, "报告不存在")
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM fin_project WHERE project_id = %s",
                        (loaded["report"]["project_id"],))
            row = cur.fetchone()
        if not row or row[0] != uid:
            raise HTTPException(404, "报告不存在")
        return loaded
    finally:
        conn.close()


def _owned_receipt(uid: str, receipt_id: str) -> None:
    conn = report_svc.get_conn()
    try:
        r = publish_svc.get(conn, receipt_id)      # 不存在 → PublishBlocked
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.user_id FROM fin_publish_receipt r "
                "JOIN fin_report rp ON rp.report_id = r.report_id "
                "JOIN fin_project p ON p.project_id = rp.project_id "
                "WHERE r.receipt_id = %s", (receipt_id,))
            row = cur.fetchone()
        if not row or row[0] != uid:
            raise HTTPException(404, "回执不存在")   # 不泄露存在性
    except publish_svc.PublishBlocked as exc:
        raise HTTPException(404, "回执不存在") from exc
    finally:
        conn.close()


class PublishIn(BaseModel):
    channel: str
    target: Optional[object] = None       # 字符串 URL/路径，或 {"url":…,"status_url":…}
    resend: bool = False
    reason: Optional[str] = None


@router.post("/v1/fin/reports/{report_id}/publish")
async def publish_report(report_id: str, body: PublishIn, request: Request):
    """把一份**已通过校验**的报告发布到指定渠道。

    `channel` ∈ {`in_app`, `webhook`, `file`}。`UNKNOWN` 下再发同一份报告 → 409，
    除非 `resend=true`（**并写明 `reason`**，重发会留痕）。
    """
    uid = _uid(request)
    loaded = await asyncio.to_thread(_owned, uid, report_id)

    def _run() -> dict:
        conn = report_svc.get_conn()
        try:
            return publish_svc.submit(conn, report_id, body.channel, target=body.target,
                                      resend=body.resend, actor=uid, reason=body.reason,
                                      loaded=loaded)
        finally:
            conn.close()

    try:
        out = await asyncio.to_thread(_run)
    except publish_svc.PublishBlocked as exc:
        code = 409 if exc.code in ("unknown_needs_resend",) else 400
        if exc.code == "not_found":
            code = 404
        raise HTTPException(code, detail={"message": exc.reason, "code": exc.code}) from exc
    logger.info("[fin.publish] report={} channel={} → {}", report_id, body.channel, out.get("status"))
    return out


@router.get("/v1/fin/publish-receipts/{receipt_id}")
async def get_publish_receipt(receipt_id: str, request: Request, resolve: bool = False):
    """读一条回执。`resolve=true` 时对 `UNKNOWN` **查外部状态**尝试落定。"""
    uid = _uid(request)
    await asyncio.to_thread(_owned_receipt, uid, receipt_id)

    def _run() -> dict:
        conn = report_svc.get_conn()
        try:
            return publish_svc.get(conn, receipt_id, resolve=resolve)
        finally:
            conn.close()

    try:
        return await asyncio.to_thread(_run)
    except publish_svc.PublishBlocked as exc:
        raise HTTPException(404, detail={"message": exc.reason, "code": exc.code}) from exc


class ResolveIn(BaseModel):
    status: str                # SUCCESS / FAILED
    note: Optional[str] = None


@router.post("/v1/fin/publish-receipts/{receipt_id}/resolve")
async def resolve_publish_receipt(receipt_id: str, body: ResolveIn, request: Request):
    """**手动核实**：查外部状态后人工落定 `SUCCESS` / `FAILED`（`§11.2`）。"""
    uid = _uid(request)
    await asyncio.to_thread(_owned_receipt, uid, receipt_id)

    def _run() -> dict:
        conn = report_svc.get_conn()
        try:
            return publish_svc.resolve(conn, receipt_id, body.status, actor=uid, note=body.note)
        finally:
            conn.close()

    try:
        return await asyncio.to_thread(_run)
    except publish_svc.PublishBlocked as exc:
        code = 400 if exc.code == "bad_status" else 404
        raise HTTPException(code, detail={"message": exc.reason, "code": exc.code}) from exc


@router.get("/v1/fin/publish-receipts")
async def list_publish_receipts(request: Request, limit: int = 100):
    """**待核实队列**：当前用户项目里 `status='UNKNOWN'` 的回执。"""
    uid = _uid(request)

    def _run() -> dict:
        conn = report_svc.get_conn()
        try:
            return {"pending": publish_svc.list_pending(conn, owner_user_id=uid, limit=limit)}
        finally:
            conn.close()

    return await asyncio.to_thread(_run)
