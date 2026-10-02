"""长任务：`submit → 持久化 job_id → get / 完成事件 → 结果引用 → 可请求取消`。

（`01方案 §10.4`）**先落库再返回** —— 返回体里的 `job_id` 一定查得到。
M4 的时点工作流会用它登记「今天 09:30 那轮跑起来了没有」这类长动作。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app import db, jobs
from app.schemas import JobIn, JobSucceedIn

router = APIRouter(tags=["jobs"])


@router.post("/api/v1/jobs")
def submit_job(body: JobIn) -> dict:
    with db.cursor(commit=True) as cur:
        return jobs.submit(
            cur,
            job_type=body.type,
            params=body.params,
            project_id=body.project_id,
            idempotency_key=body.idempotency_key,
        )


@router.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with db.cursor() as cur:
        row = jobs.get(cur, job_id)
    if not row:
        raise HTTPException(404, f"任务不存在：{job_id}")
    return row


@router.get("/api/v1/projects/{project_id}/jobs")
def list_jobs(project_id: str, limit: int = 100) -> dict:
    with db.cursor() as cur:
        return {"items": jobs.list_for_project(cur, project_id, limit)}


@router.post("/api/v1/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    try:
        with db.cursor(commit=True) as cur:
            return jobs.request_cancel(cur, job_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except jobs.JobTransitionError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/v1/jobs/{job_id}/running")
def mark_running(job_id: str) -> dict:
    try:
        with db.cursor(commit=True) as cur:
            return jobs.mark_running(cur, job_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except jobs.JobTransitionError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/v1/jobs/{job_id}/succeed")
def mark_succeeded(job_id: str, body: JobSucceedIn) -> dict:
    try:
        with db.cursor(commit=True) as cur:
            return jobs.succeed(cur, job_id, body.result_ref, body.checkpoint)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except jobs.JobTransitionError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/v1/jobs/{job_id}/fail")
def mark_failed(job_id: str) -> dict:
    try:
        with db.cursor(commit=True) as cur:
            return jobs.fail(cur, job_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except jobs.JobTransitionError as exc:
        raise HTTPException(409, str(exc)) from exc
