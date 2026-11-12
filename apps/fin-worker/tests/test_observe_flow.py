"""R13 · 自动盯盘链路（**不连网、不连库、不连 Temporal**）。

覆盖四件事：

1. `observe_proposals` 只挑**已生效**（`status == 'applied'`）的提案；
2. `observe_applied` 打的是 `/api/internal/fin/evolution/observe`、体里的 `market` / `as_of`
   与传入一致（判断全在服务端，这里只搬运）；
3. 每个活动都在 `worker.activity_list()` 里显式登记（漏登记 = 工作流拉起时报未注册）；
4. **编排**：`_observe_for_project` 逐提案 catch ——
   单条观察失败**不挂整条工作流**（其余提案照常观察）；
   **回滚失败**（api 回 409 / 503）额外标 `rollback_error` 并打 ERROR 日志（红线 9）。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app import activities, config, schedules, workflows
from app.bridge.hunter_api import ApiError, HunterApiClient


def _install_api(monkeypatch, handler):
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(wrapped)

    def factory(*a, **kw):
        return HunterApiClient(base_url="http://api.test", key="k",
                               client=httpx.Client(transport=transport))

    monkeypatch.setattr(activities, "HunterApiClient", factory)
    return seen


# ── ① 只挑已生效的提案 ────────────────────────────────────────────────────

def test_observe_proposals_only_applied(monkeypatch):
    _install_api(monkeypatch, lambda r: httpx.Response(200, json={"items": [
        {"proposal_id": "p1", "status": "applied"},
        {"proposal_id": "p2", "status": "validating"},
        {"proposal_id": "p3", "status": "applied"},
        {"proposal_id": "p4", "status": "rejected"},
        {"proposal_id": "p5", "status": "rolled_back"},
    ]}))
    out = activities.observe_proposals({"project_id": "prj_1"})
    assert [p["proposal_id"] for p in out] == ["p1", "p3"]


# ── ② 活动打正确的端点 / 体 ────────────────────────────────────────────────

def test_observe_applied_posts_correct_endpoint(monkeypatch):
    seen = _install_api(monkeypatch, lambda r: httpx.Response(200, json={
        "observed": True, "action": "alert", "proposal_id": "p1",
        "window": {"start": "2026-10-01", "window_days": 20},
        "live": {"max_drawdown": -0.09, "net_return": 0.01, "points": 3},
        "rollback_line": -0.10, "fail_line": -0.01}))
    out = activities.observe_applied(
        {"proposal_id": "p1", "market": "HK", "as_of": "2026-10-09"})
    req = seen[-1]
    body = json.loads(req.read().decode())
    assert "/api/internal/fin/evolution/observe" in str(req.url)
    assert body == {"proposal_id": "p1", "market": "HK", "as_of": "2026-10-09"}
    assert out["action"] == "alert"


def test_observe_applied_omits_optional_fields(monkeypatch):
    """没传 `market` / `as_of` 时不写进体（服务端自己从 applied 事件 / 项目取）。"""
    seen = _install_api(monkeypatch, lambda r: httpx.Response(200, json={"observed": False}))
    activities.observe_applied({"proposal_id": "p9"})
    body = json.loads(seen[-1].read().decode())
    assert body == {"proposal_id": "p9"}


def test_observe_applied_raises_on_http_error(monkeypatch):
    """失败（含回滚被拒）按 HTTP 码抛 `ApiError` —— 不吞。"""
    _install_api(monkeypatch, lambda r: httpx.Response(409, text="拒绝回滚：版本链对不上"))
    with pytest.raises(ApiError) as ei:
        activities.observe_applied({"proposal_id": "p1", "market": "HK"})
    assert ei.value.status == 409


# ── ③ 活动登记 ────────────────────────────────────────────────────────────

def test_activity_list_registers_observe_activities():
    from app.worker import activity_list
    names = {fn.__name__ for fn in activity_list()}
    assert {"observe_proposals", "observe_applied"} <= names


# ── ④ 编排：单条失败隔离 + 回滚失败可见 ────────────────────────────────────

class _FakeLogger:
    def __init__(self):
        self.errors: list[str] = []

    def error(self, *args, **kwargs):
        self.errors.append(" ".join(str(a) for a in args))


async def _run_observe_project(monkeypatch, *, results):
    """用假 `_exec` 跑 `_observe_for_project`（不碰 Temporal）。"""
    fake_logger = _FakeLogger()
    monkeypatch.setattr(workflows.workflow, "logger", fake_logger)

    proposals = [{"proposal_id": pid, "status": "applied"} for pid in results]

    async def fake_exec(fn, arg, **kw):
        name = fn.__name__
        if name == "observe_proposals":
            return proposals
        if name == "observe_applied":
            value = results[arg["proposal_id"]]
            if isinstance(value, Exception):
                raise value
            return value
        raise AssertionError(f"未预期的活动 {name}")

    monkeypatch.setattr(workflows, "_exec", fake_exec)
    out = await workflows._observe_for_project(
        {"project_id": "prj_1"}, "HK", "2026-10-09", "2026-10-09T16:00:00+08:00", {})
    return out, fake_logger


def test_one_failed_observe_does_not_abort_the_rest(monkeypatch):
    """单条观察失败 → 记进汇总继续，后面的提案照常观察。"""
    out, logger = asyncio.run(_run_observe_project(monkeypatch, results={
        "p1": ApiError(0, "无法连接 api（ConnectError）"),
        "p2": {"observed": True, "action": "none", "live": {"max_drawdown": -0.01}},
    }))
    recs = {r["proposal_id"]: r for r in out["proposals"]}
    assert recs["p1"]["observed"] is False and "error" in recs["p1"]
    assert "rollback_error" not in recs["p1"]              # 网络错不是回滚失败
    assert recs["p2"]["action"] == "none"                  # 第二条照常
    assert len(logger.errors) == 1


def test_rollback_failure_is_flagged_and_logged(monkeypatch):
    """回滚失败（409/503）→ 标 `rollback_error` + ERROR 日志（红线 9：回滚必须可见）。"""
    out, logger = asyncio.run(_run_observe_project(monkeypatch, results={
        "p1": ApiError(409, "拒绝回滚：回滚目标 'vb' 已不在 fin_param.strategies 里"),
        "p2": ApiError(503, "进化未启用（FIN_EVOLUTION_MODE=off）"),
        "p3": {"observed": True, "action": "rolled_back", "rollback": {"reinject": {"status": "done"}}},
    }))
    recs = {r["proposal_id"]: r for r in out["proposals"]}
    assert recs["p1"]["rollback_error"] is True
    assert recs["p2"]["rollback_error"] is True
    assert recs["p3"]["action"] == "rolled_back"           # 成功回滚照常记
    assert len(logger.errors) == 2                         # 两次回滚失败各一条 ERROR


def test_observe_records_full_result_for_rolled_back(monkeypatch):
    """成功回滚时把 `rollback` 子体（含回灌状态）一起记进汇总 —— 三样可追溯。"""
    out, _ = asyncio.run(_run_observe_project(monkeypatch, results={
        "p1": {"observed": True, "action": "rolled_back",
               "live": {"max_drawdown": -0.15}, "rollback_line": -0.10, "fail_line": -0.01,
               "rollback": {"from_key": "vb", "to_key": "vc",
                            "reinject": {"status": "done", "task_id": "evri_1"}}},
    }))
    rec = out["proposals"][0]
    assert rec["action"] == "rolled_back"
    assert rec["rollback"]["reinject"]["status"] == "done"
    assert rec["rollback_line"] == -0.10
