"""长任务协议（`01方案 §10.4`）：submit → 持久化 job_id → get → 结果引用 → 可取消。

最要紧的一条：**任务只有在已持久化后才能返回 `ACCEPTED`** ——
实现上就是「先 INSERT 再返回」。测试用「新建一个连接去查」来钉它：
如果实现是先返回再落库，另一个连接就查不到这个 `job_id`。
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("HUNTER_INTERNAL_KEY", "test-internal-key")
os.environ.setdefault("PAPER_MODE", "PAPER")

pytestmark = pytest.mark.db

pytest.importorskip("psycopg2")
if not os.getenv("PAPER_TEST_DSN"):
    pytest.skip("未设置 PAPER_TEST_DSN，跳过长任务用例", allow_module_level=True)

from app import jobs  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
H = {"X-Hunter-Internal-Key": "test-internal-key"}


def _get(path):
    r = client.get(path, headers=H)
    return r.status_code, (r.json() if r.content else None)


def _post(path, body=None):
    r = client.post(path, json=body if body is not None else {}, headers=H)
    return r.status_code, (r.json() if r.content else None)


def test_submit_is_persisted_before_it_returns(project):
    """新建一个**独立连接**去查 job_id —— 先返回后落库的实现在这里查不到。"""
    status, job = _post("/api/v1/jobs", {"type": "snapshot", "project_id": project,
                                         "params": {"code": "600519"}})
    assert status == 200 and job["status"] == jobs.ACCEPTED

    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(os.environ["PAPER_DATABASE_URL"])
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT job_id, status, type FROM fin_job WHERE job_id = %s",
                        (job["job_id"],))
            row = cur.fetchone()
    finally:
        conn.rollback()
        conn.close()
    assert row is not None
    assert row["status"] == jobs.ACCEPTED and row["type"] == "snapshot"


def test_get_returns_the_job(project):
    _, job = _post("/api/v1/jobs", {"type": "report", "project_id": project})
    status, fetched = _get(f"/api/v1/jobs/{job['job_id']}")
    assert status == 200 and fetched["job_id"] == job["job_id"]
    assert fetched["status"] == jobs.ACCEPTED


def test_get_unknown_job_is_404():
    status, body = _get(f"/api/v1/jobs/job_{uuid.uuid4().hex}")
    assert status == 404 and "任务不存在" in body["detail"]


def test_submit_is_idempotent_per_key(project):
    """同一个键再提交 → 返回**原来那条** job（不新建）——「回执丢失后按请求标识找回」。"""
    key = f"job-{uuid.uuid4().hex}"
    _, first = _post("/api/v1/jobs", {"type": "snapshot", "project_id": project,
                                      "idempotency_key": key})
    _, second = _post("/api/v1/jobs", {"type": "snapshot", "project_id": project,
                                       "idempotency_key": key})
    assert second["job_id"] == first["job_id"]
    assert second["created"] is False

    items = _get(f"/api/v1/projects/{project}/jobs")[1]["items"]
    assert len([j for j in items if j["job_id"] == first["job_id"]]) == 1


def test_state_machine_happy_path(project):
    _, job = _post("/api/v1/jobs", {"type": "snapshot", "project_id": project})
    jid = job["job_id"]

    assert _post(f"/api/v1/jobs/{jid}/running")[1]["status"] == jobs.RUNNING
    status, done = _post(f"/api/v1/jobs/{jid}/succeed",
                         {"result_ref": "artifact://report/1"})
    assert status == 200 and done["status"] == jobs.SUCCEEDED
    assert done["result_ref"] == "artifact://report/1"


def test_cancel_from_accepted_goes_straight_to_cancelled(project):
    _, job = _post("/api/v1/jobs", {"type": "snapshot", "project_id": project})
    status, out = _post(f"/api/v1/jobs/{job['job_id']}/cancel")
    assert status == 200 and out["status"] == jobs.CANCELLED and out["changed"] is True


def test_cancel_running_goes_through_cancel_requested(project):
    _, job = _post("/api/v1/jobs", {"type": "snapshot", "project_id": project})
    jid = job["job_id"]
    _post(f"/api/v1/jobs/{jid}/running")
    status, out = _post(f"/api/v1/jobs/{jid}/cancel")
    assert status == 200 and out["status"] == jobs.CANCEL_REQUESTED
    # 任务自己收尾 → CANCELLED
    assert _post(f"/api/v1/jobs/{jid}/cancel")[1]["status"] == jobs.CANCELLED


def test_cancel_on_terminal_job_is_a_noop(project):
    """取消一个已完成的任务不是错误 —— 返回原状态、不改动。"""
    _, job = _post("/api/v1/jobs", {"type": "snapshot", "project_id": project})
    jid = job["job_id"]
    _post(f"/api/v1/jobs/{jid}/running")
    _post(f"/api/v1/jobs/{jid}/succeed", {"result_ref": "x"})
    status, out = _post(f"/api/v1/jobs/{jid}/cancel")
    assert status == 200 and out["changed"] is False and out["status"] == jobs.SUCCEEDED


def test_illegal_transition_is_refused(project):
    """已成功的任务不能回到 RUNNING —— 状态机是恢复语义的地基，非法迁移要拒绝。"""
    _, job = _post("/api/v1/jobs", {"type": "snapshot", "project_id": project})
    jid = job["job_id"]
    _post(f"/api/v1/jobs/{jid}/running")
    _post(f"/api/v1/jobs/{jid}/succeed", {"result_ref": "x"})
    status, body = _post(f"/api/v1/jobs/{jid}/running")
    assert status == 409 and "不能从" in body["detail"]


def test_fail_is_terminal(project):
    _, job = _post("/api/v1/jobs", {"type": "snapshot", "project_id": project})
    jid = job["job_id"]
    _post(f"/api/v1/jobs/{jid}/running")
    assert _post(f"/api/v1/jobs/{jid}/fail")[1]["status"] == jobs.FAILED
    assert _post(f"/api/v1/jobs/{jid}/cancel")[1]["status"] == jobs.FAILED


def test_six_statuses_match_the_spec():
    """`09 §4.7` 的 CHECK 里就是这六个值 —— 少一个都会让迁移与代码对不上。"""
    assert jobs.TERMINAL == {"SUCCEEDED", "FAILED", "CANCELLED"}
    assert {jobs.ACCEPTED, jobs.RUNNING, jobs.SUCCEEDED,
            jobs.FAILED, jobs.CANCEL_REQUESTED, jobs.CANCELLED} == {
        "ACCEPTED", "RUNNING", "SUCCEEDED", "FAILED", "CANCEL_REQUESTED", "CANCELLED"}
