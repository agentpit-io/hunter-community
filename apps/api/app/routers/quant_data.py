"""数据中心接口 · /api/quant/data/*

方案见 doc/开源hunter-community/01详细工作目录/11量化策略/
      22_20260822_数据中心_技术方案.md §4.2

单独一个 router 而不是塞进 quant.py:数据下载和策略/回测是两件事,
quant.py 已经 600 多行了。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.services.quant import data_center

log = logging.getLogger(__name__)

router = APIRouter(prefix="/quant/data", tags=["quant-data"])


def _uid(request: Request) -> str | None:
    u = getattr(request.state, "user_id", None)
    return str(u) if u else None


@router.get("/overview")
async def get_overview():
    """当前数据概览 · 数据页顶部那一排。

    `empty=true` 时前端提示"到「数据」页选一批股票下载" ——
    以前是开机自动下载,现在改成让用户自己决定(老板:
    「用户都不知道你就占用他的资源很不好」)。
    """
    return data_center.overview()


@router.get("/scopes")
async def get_scopes(request: Request):
    """可选范围 + 每个的股票数。前端拿它渲染①那一排和行业两级。"""
    return data_center.scopes(_uid(request))


class EstimateIn(BaseModel):
    scope: dict = {}                 # {kind, industries[], codes[]}
    span_months: int = 36            # 0 = 只补最新
    with_financial: bool = False
    keep_raw: bool = False


@router.post("/estimate")
async def post_estimate(body: EstimateIn, request: Request):
    """预估:只数 / 可跳过 / 耗时 / 磁盘。

    前端拖选项时是**本地算**的(速率写死在前端),点「开始下载」前
    调这个拿准确的可跳过数 —— 因为"哪些已经下过"只有后端知道。
    """
    return data_center.estimate(
        body.scope, body.span_months, body.with_financial,
        body.keep_raw, _uid(request),
    )


# ═══════════════════════════════════════════════════════════
# 下载任务
# ═══════════════════════════════════════════════════════════

class JobIn(BaseModel):
    scope: dict = {}
    span_months: int = 36
    with_financial: bool = False
    keep_raw: bool = False


@router.post("/jobs")
async def create_job(body: JobIn, request: Request):
    """建一个下载任务并开始跑。

    **同时只允许一个** —— 多个任务并发打同一个上游会互相拖慢并触发限流
    (实测:800 只不限速连着打,腾讯清一色 ReadTimeout)。
    """
    import asyncio as _aio
    from app.services.quant import data_job

    running = data_job.active_job()
    if running:
        return {"error": "job_running",
                "message": f"已经有一个任务在跑(#{running['id']})· 同时只允许一个",
                "job": running}

    est = data_center.estimate(body.scope, body.span_months, body.with_financial,
                              body.keep_raw, _uid(request))
    if not est["stocks"]:
        return {"error": "empty_scope", "message": est.get("note") or "这个范围没有股票"}

    jid = data_job.create(body.scope, body.span_months, body.with_financial,
                          body.keep_raw, est["stocks"], _uid(request))
    # 放线程里跑:一趟可能几小时,卡在事件循环里整个 API 就没响应了
    _aio.create_task(_aio.to_thread(data_job.run, jid))
    return {"ok": True, "job_id": jid, "estimate": est}


@router.get("/jobs")
async def list_jobs(limit: int = 20):
    from app.services.quant import data_job
    return {"jobs": data_job.recent(limit), "active": data_job.active_job()}


@router.get("/jobs/{job_id}")
async def get_job(job_id: int):
    from app.services.quant import data_job
    j = data_job.get(job_id)
    return j or {"error": "not_found"}


@router.post("/jobs/{job_id}/pause")
async def pause_job(job_id: int):
    """暂停。worker 在另一个线程,没法直接打断 —— 它每 5 只回头读一次
    自己的 status,读到 paused 就自己退出。所以点了之后最多几秒生效。"""
    from app.services.quant import data_job
    ok = data_job.set_status(job_id, "paused", "用户暂停")
    return {"ok": ok}


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: int):
    """续跑 —— 已下载的不会重来(worker 开头会查 data_coverage 跳过)。"""
    import asyncio as _aio
    from app.services.quant import data_job

    running = data_job.active_job()
    if running:
        return {"error": "job_running",
                "message": f"已经有一个任务在跑(#{running['id']})"}
    if not data_job.set_status(job_id, "queued", "续跑"):
        return {"error": "not_found"}
    _aio.create_task(_aio.to_thread(data_job.run, job_id))
    return {"ok": True, "job_id": job_id}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: int):
    from app.services.quant import data_job
    return {"ok": data_job.set_status(job_id, "canceled", "用户取消")}
