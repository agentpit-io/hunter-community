"""M5 · 每日报告活动（`activities.generate_daily_report`）。

不连网 / 不连库 / 不连 Temporal：全部走 `httpx.MockTransport`。
盯三件事：
① 生成成功 → job SUCCEEDED 且带上产物引用；
② 回读校验不通过 → job **FAILED**（不发布），且**不重试**；
③ 无报告日 → job SUCCEEDED 并写明原因（不是失败）。
④ 幂等键确定性：不含随机数，同项目同日重放拿到同一个键。
"""

from __future__ import annotations

import httpx
import pytest

from app import activities
from app.bridge import idem
from app.bridge.hunter_api import HunterApiClient
from app.bridge.paper import PaperClient


def _install(monkeypatch, handler):
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(wrapped)

    def paper_factory(*a, **kw):
        return PaperClient(base_url="http://paper.test", key="k",
                           client=httpx.Client(transport=transport))

    def api_factory(*a, **kw):
        return HunterApiClient(base_url="http://api.test", key="k",
                               client=httpx.Client(transport=transport))

    monkeypatch.setattr(activities, "PaperClient", paper_factory)
    monkeypatch.setattr(activities, "HunterApiClient", api_factory)
    return seen


def _default_handler(gen_response: dict, fail_after_submit: bool = False):
    """假 paper（submit/running/succeed/fail）+ 假 api（generate）。"""
    def h(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/api/internal/fin/reports/generate":
            return httpx.Response(200, json=gen_response)
        if path == "/api/v1/jobs" and req.method == "POST":
            return httpx.Response(200, json={"job_id": "job_report_1", "status": "ACCEPTED",
                                             "created": True})
        if path == "/api/v1/jobs/job_report_1/running":
            return httpx.Response(200, json={"job_id": "job_report_1", "status": "RUNNING"})
        if path == "/api/v1/jobs/job_report_1/succeed":
            return httpx.Response(200, json={"job_id": "job_report_1", "status": "SUCCEEDED"})
        if path == "/api/v1/jobs/job_report_1/fail":
            return httpx.Response(200, json={"job_id": "job_report_1", "status": "FAILED"})
        raise AssertionError(f"意外请求：{req.method} {path}")

    return h


# ── ① 成功 ────────────────────────────────────────────────────────────────

def test_generate_report_success_marks_job_succeeded(monkeypatch):
    seen = _install(monkeypatch, _default_handler({
        "report_id": "rpt_abc", "status": "validated", "artifact_ref": "artifact:fin-abc",
        "check": {"ok": True, "checked": 6, "matched": 6, "violations": []},
        "facts": 19, "used_fallback": False,
    }))
    out = activities.generate_daily_report(
        {"project_id": "prj_1", "trade_date": "2026-09-30"})

    assert out["status"] == "validated"
    assert out["job_id"] == "job_report_1"
    assert out["artifact_ref"] == "artifact:fin-abc"
    paths = [r.url.path for r in seen]
    assert "/api/v1/jobs" in paths
    assert "/api/v1/jobs/job_report_1/succeed" in paths
    assert "/api/v1/jobs/job_report_1/fail" not in paths
    # 幂等键随请求体进了 paper
    submit = next(r for r in seen if r.url.path == "/api/v1/jobs")
    assert "fin-report:prj_1:2026-09-30" in submit.content.decode()


# ── ② 校验不通过 → FAILED、不发布 ──────────────────────────────────────────

def test_generate_report_validation_failure_marks_job_failed(monkeypatch):
    seen = _install(monkeypatch, _default_handler({
        "report_id": "rpt_bad", "status": "failed", "artifact_ref": None,
        "check": {"ok": False, "checked": 7, "matched": 6,
                  "violations": [{"field": "analysis_text", "token": "+8.74%"}]},
        "facts": 19, "used_fallback": False,
    }))
    out = activities.generate_daily_report(
        {"project_id": "prj_1", "trade_date": "2026-09-30"})

    assert out["status"] == "failed"
    paths = [r.url.path for r in seen]
    assert "/api/v1/jobs/job_report_1/fail" in paths
    assert "/api/v1/jobs/job_report_1/succeed" not in paths


# ── ③ 无报告日 → SUCCEEDED + 原因 ─────────────────────────────────────────

def test_generate_report_no_report_day_is_not_failure(monkeypatch):
    seen = _install(monkeypatch, _default_handler({
        "created": False, "status": "no_report", "report_id": None,
        "reason": "2026-10-01 没有收盘估值：非交易日",
    }))
    out = activities.generate_daily_report(
        {"project_id": "prj_1", "trade_date": "2026-10-01"})

    assert out["status"] == "no_report"
    paths = [r.url.path for r in seen]
    assert "/api/v1/jobs/job_report_1/succeed" in paths
    assert "/api/v1/jobs/job_report_1/fail" not in paths


# ── ④ API 报错 → job FAILED 且抛出（交给 Temporal 重试）────────────────────

def test_generate_report_api_error_fails_job_and_raises(monkeypatch):
    def h(req):
        if req.url.path == "/api/internal/fin/reports/generate":
            return httpx.Response(503, text="api 不可用")
        return _default_handler({})(req)

    seen = _install(monkeypatch, h)
    with pytest.raises(Exception):
        activities.generate_daily_report({"project_id": "prj_1", "trade_date": "2026-09-30"})
    assert "/api/v1/jobs/job_report_1/fail" in [r.url.path for r in seen]


# ── ⑤ 幂等键确定性（不含随机数）──────────────────────────────────────────

def test_report_job_key_is_deterministic():
    a = idem.report_job_key("prj_1", "2026-09-30")
    b = idem.report_job_key("prj_1", "2026-09-30")
    assert a == b == "fin-report:prj_1:2026-09-30"
    assert idem.report_job_key("prj_1", "2026-10-09") != a


# ── ⑥ 收盘工作流确实带上了报告这一步 ──────────────────────────────────────

def test_close_branch_calls_report_after_close_day():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app" / "workflows.py").read_text(encoding="utf-8")
    close_idx = src.index('kind == "close"')
    close_block = src[close_idx:close_idx + 1200]
    assert "activities.close_day" in close_block
    assert "activities.generate_daily_report" in close_block
    assert close_block.index("activities.close_day") < close_block.index("activities.generate_daily_report")
