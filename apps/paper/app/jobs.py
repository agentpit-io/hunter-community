"""长任务协议（`01方案 §10.4` · `09 §4.7`）。

```text
submit → 持久化 job_id → get / 完成事件 → 结果引用 → 可请求取消
```

状态机（`fin_job.status` 的 CHECK 就是这六个）：

```text
ACCEPTED ──► RUNNING ──► SUCCEEDED
    │           │
    │           └─────► FAILED
    ├──► CANCEL_REQUESTED ──► CANCELLED
    └──────────────────────► CANCELLED
```

**「任务只有在已持久化后才能返回 `ACCEPTED`」** —— 实现上就是
「先 `INSERT` 再返回」。这不是措辞问题：先返回再落库，进程在中间挂掉，
调用方手里就有一个查不到的 `job_id`，而「根据稳定请求标识找回原 `job_id`」
（`01方案 §11.2` 的「策略提交后回执丢失」一行）就没有依据了。

幂等：`fin_job.idempotency_key` 上有查询。同一个键再提交 → **返回原来那条 job**
（不新建），这样重试拿到的是同一个 `job_id`。
"""

from __future__ import annotations

from typing import Any, Optional

import psycopg2.extras

from app.ledger import new_id

# 状态集合与允许的迁移。改这里等于改协议 —— 先看 `09 §4.7` 的 CHECK。
ACCEPTED = "ACCEPTED"
RUNNING = "RUNNING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
CANCEL_REQUESTED = "CANCEL_REQUESTED"
CANCELLED = "CANCELLED"

TERMINAL = frozenset({SUCCEEDED, FAILED, CANCELLED})

_ALLOWED = {
    ACCEPTED: {RUNNING, CANCEL_REQUESTED, CANCELLED, FAILED},
    RUNNING: {SUCCEEDED, FAILED, CANCEL_REQUESTED},
    CANCEL_REQUESTED: {CANCELLED, SUCCEEDED, FAILED},
    SUCCEEDED: set(),
    FAILED: set(),
    CANCELLED: set(),
}


class JobTransitionError(Exception):
    """非法状态迁移。**拒绝**而不是就地改 —— 状态机是恢复语义的地基。"""


def _view(row: dict) -> dict:
    return row


def get(cur, job_id: str) -> Optional[dict]:
    cur.execute("SELECT * FROM fin_job WHERE job_id = %s", (job_id,))
    return cur.fetchone()


def get_by_idempotency_key(cur, key: str) -> Optional[dict]:
    cur.execute(
        "SELECT * FROM fin_job WHERE idempotency_key = %s ORDER BY created_at DESC LIMIT 1",
        (key,),
    )
    return cur.fetchone()


def list_for_project(cur, project_id: str, limit: int = 100) -> list[dict]:
    cur.execute(
        "SELECT * FROM fin_job WHERE project_id = %s ORDER BY created_at DESC LIMIT %s",
        (project_id, limit),
    )
    return cur.fetchall()


def submit(
    cur,
    *,
    job_type: str,
    params: Optional[dict] = None,
    project_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    """登记一个长任务，**先落库再返回**（返回体里的 `job_id` 一定查得到）。

    同一个 `idempotency_key` 重复提交 → 返回原来那条（不新建）。
    """
    if idempotency_key:
        existing = get_by_idempotency_key(cur, idempotency_key)
        if existing:
            return {**_view(existing), "created": False}

    job_id = new_id("job")
    cur.execute(
        """
        INSERT INTO fin_job (job_id, project_id, type, status, params, idempotency_key)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (job_id, project_id, job_type, ACCEPTED,
         psycopg2.extras.Json(params or {}), idempotency_key),
    )
    return {**_view(cur.fetchone()), "created": True}


def _transition(cur, job_id: str, target: str, **setters: Any) -> dict:
    row = get(cur, job_id)
    if not row:
        raise LookupError(f"任务不存在：{job_id}")
    current = row["status"]
    if target not in _ALLOWED.get(current, set()):
        raise JobTransitionError(f"任务 {job_id} 不能从 {current} 变成 {target}")
    assignments = ["status = %s", "updated_at = now()"]
    values: list[Any] = [target]
    for column, value in setters.items():
        assignments.append(f"{column} = %s")
        values.append(psycopg2.extras.Json(value) if column == "checkpoint" else value)
    values.append(job_id)
    cur.execute(
        f"UPDATE fin_job SET {', '.join(assignments)} WHERE job_id = %s RETURNING *",
        tuple(values),
    )
    return cur.fetchone()


def mark_running(cur, job_id: str, checkpoint: Optional[dict] = None) -> dict:
    return _transition(cur, job_id, RUNNING, checkpoint=checkpoint)


def succeed(cur, job_id: str, result_ref: str, checkpoint: Optional[dict] = None) -> dict:
    return _transition(cur, job_id, SUCCEEDED, result_ref=result_ref, checkpoint=checkpoint)


def fail(cur, job_id: str, checkpoint: Optional[dict] = None) -> dict:
    return _transition(cur, job_id, FAILED, checkpoint=checkpoint)


def set_checkpoint(cur, job_id: str, checkpoint: dict) -> dict:
    """只写业务检查点，**不动状态**（`01方案 §11.2`：长计算中断按检查点恢复）。

    与 `_transition` 分开：状态迁移有它的状态机，检查点写入与状态无关 ——
    RUNNING 的任务要能一边跑一边更新「跑到哪一步了」。
    """
    row = get(cur, job_id)
    if not row:
        raise LookupError(f"任务不存在：{job_id}")
    cur.execute(
        "UPDATE fin_job SET checkpoint = %s, updated_at = now() WHERE job_id = %s RETURNING *",
        (psycopg2.extras.Json(checkpoint or {}), job_id),
    )
    return cur.fetchone()


def request_cancel(cur, job_id: str) -> dict:
    """请求取消。已经终态的任务返回原样（取消一个已完成的任务不是错误）。"""
    row = get(cur, job_id)
    if not row:
        raise LookupError(f"任务不存在：{job_id}")
    if row["status"] in TERMINAL:
        return {**_view(row), "changed": False}
    if row["status"] == RUNNING:
        return {**_transition(cur, job_id, CANCEL_REQUESTED), "changed": True}
    # ACCEPTED / CANCEL_REQUESTED → 直接进 CANCELLED（还没跑起来，没有中间态要收）
    return {**_transition(cur, job_id, CANCELLED), "changed": True}
