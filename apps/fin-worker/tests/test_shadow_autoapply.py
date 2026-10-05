"""L13 · **自动生效链路**（`fin.shadow` 判定 `passed` 之后自动应用）。

**不连网 / 不连库 / 不连 Temporal**，覆盖四件事：

1. 活动 `auto_apply` 打的是 `/api/internal/fin/evolution/{id}/auto-apply`、体里的 `market`
   与传入一致（方向 / 开关判断全在 api 侧，这里只搬运）；
2. **活动永不抛异常** —— 调用失败（404 / 连不上）也返回结构化结果并记日志，好让
   `fin.shadow` 正常收尾、且**不被 Temporal 重试**（重试会把 `rejected_by_gate` 事件刷屏）；
3. 活动在 `worker.activity_list()` 里**显式登记**（漏登记 = 工作流拉起时报未注册）；
4. **编排**：`_shadow_for_project` **只在 verdict == 'passed' 时**才调 `auto_apply`；
   没通过（`inconclusive` / `failed` / 窗口未结束）一律不触发。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app import activities, workflows
from app.bridge.hunter_api import HunterApiClient


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


# ── ① 活动打正确的端点 / 体 ────────────────────────────────────────────────

def test_auto_apply_posts_correct_endpoint(monkeypatch):
    seen = _install_api(monkeypatch, lambda r: httpx.Response(200, json={
        "auto_applied": True, "skipped": False, "proposal_id": "evp_1", "project_id": "prj_1",
        "from_key": "vb", "to_key": "vb#abcd"}))
    out = activities.auto_apply({"proposal_id": "evp_1", "market": "CN_A"})
    req = seen[-1]
    body = json.loads(req.read().decode())
    assert "/api/internal/fin/evolution/evp_1/auto-apply" in str(req.url)
    assert body == {"market": "CN_A"}
    assert out["auto_applied"] is True and out["to_key"] == "vb#abcd"


def test_auto_apply_omits_market_when_absent(monkeypatch):
    seen = _install_api(monkeypatch, lambda r: httpx.Response(200, json={
        "auto_applied": False, "skipped": True, "reason": "该项目未开启自动生效"}))
    out = activities.auto_apply({"proposal_id": "evp_9"})
    assert json.loads(seen[-1].read().decode()) == {}
    assert out["skipped"] is True and "未开启" in out["reason"]


def test_auto_apply_returns_skipped_without_raising(monkeypatch):
    """api 判「不自动生效」（方向不是收紧等）回 200 + skipped —— 活动原样带回。"""
    _install_api(monkeypatch, lambda r: httpx.Response(200, json={
        "auto_applied": False, "skipped": True, "proposal_id": "evp_1",
        "reason": "自动生效只放行 direction='tighten'（收紧）方向的提案；本提案 direction='loosen'"}))
    out = activities.auto_apply({"proposal_id": "evp_1", "market": "HK"})
    assert out["auto_applied"] is False and "loosen" in out["reason"]


# ── ② 活动永不抛（否则 Temporal 会重试 6 次、刷屏 rejected_by_gate）──────────

def test_auto_apply_never_raises_on_http_error(monkeypatch):
    _install_api(monkeypatch, lambda r: httpx.Response(404, text="提案不存在"))
    out = activities.auto_apply({"proposal_id": "evp_missing"})          # 不抛
    assert out["auto_applied"] is False and out["skipped"] is True
    assert "提案不存在" in out["error"] and "ApiError" in out["error"]


def test_auto_apply_never_raises_on_connect_error(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("连不上 api")

    _install_api(monkeypatch, boom)
    out = activities.auto_apply({"proposal_id": "evp_1", "market": "CN_A"})   # 不抛
    assert out["auto_applied"] is False and out["skipped"] is True
    assert "ApiError" in out["error"]


# ── ③ 活动登记 ────────────────────────────────────────────────────────────

def test_activity_list_registers_auto_apply():
    from app.worker import activity_list
    names = {fn.__name__ for fn in activity_list()}
    assert "auto_apply" in names


# ── ④ 编排：只在 passed 后触发，且不挂工作流 ────────────────────────────────

async def _run_shadow_project(monkeypatch, *, evaluates, auto_results=None, points=1):
    """用假 `_exec` 跑 `_shadow_for_project`（不碰 Temporal）。"""
    auto_results = auto_results or {}
    calls: list[str] = []
    proposals = [{"proposal_id": pid, "status": "validating"} for pid in evaluates]

    async def fake_exec(fn, arg, **kw):
        name = fn.__name__
        calls.append(name)
        if name == "shadow_proposals":
            return proposals
        if name == "shadow_step":
            return {"step": True}
        if name == "shadow_evaluate":
            return evaluates[arg["proposal_id"]]
        if name == "auto_apply":
            v = auto_results.get(arg["proposal_id"], {"auto_applied": True, "skipped": False})
            if isinstance(v, Exception):
                raise v
            return v
        raise AssertionError(f"未预期的活动 {name}")

    monkeypatch.setattr(workflows, "_exec", fake_exec)
    pts = [{"point": f"CN_A-{930 + i}"} for i in range(points)]
    out = await workflows._shadow_for_project(
        {"project_id": "prj_1"}, "CN_A", "2026-10-08", "2026-10-08T15:00:00+08:00", pts, {})
    return out, calls


def test_passed_verdict_triggers_auto_apply(monkeypatch):
    out, calls = asyncio.run(_run_shadow_project(monkeypatch, evaluates={
        "p1": {"verdict": "passed", "reason": "达标"},
    }))
    assert calls.count("auto_apply") == 1
    assert out["proposals"][0]["auto_apply"]["auto_applied"] is True


def test_non_passed_verdict_does_not_trigger(monkeypatch):
    """`inconclusive` / `failed` / 窗口未结束（None）一律不调 auto_apply。"""
    out, calls = asyncio.run(_run_shadow_project(monkeypatch, evaluates={
        "p1": {"verdict": "inconclusive", "reason": "样本不足"},
        "p2": {"verdict": "failed", "reason": "不达标"},
        "p3": {"verdict": None, "reason": "窗口未结束"},
    }))
    assert "auto_apply" not in calls
    assert all("auto_apply" not in r for r in out["proposals"])


def test_mixed_verdicts_only_passed_triggers(monkeypatch):
    out, calls = asyncio.run(_run_shadow_project(monkeypatch, evaluates={
        "p1": {"verdict": "passed"},
        "p2": {"verdict": "inconclusive"},
    }))
    assert calls.count("auto_apply") == 1
    recs = {r["proposal_id"]: r for r in out["proposals"]}
    assert recs["p1"]["auto_apply"] is not None and "auto_apply" not in recs["p2"]


def test_auto_apply_skipped_is_recorded_and_workflow_completes(monkeypatch):
    """api 判「不自动生效」→ 如实记进汇总，工作流照常收尾（不炸）。"""
    out, _ = asyncio.run(_run_shadow_project(
        monkeypatch, evaluates={"p1": {"verdict": "passed"}},
        auto_results={"p1": {"auto_applied": False, "skipped": True,
                             "reason": "该项目未开启自动生效"}}))
    rec = out["proposals"][0]
    assert rec["auto_apply"]["skipped"] is True
    assert "未开启" in rec["auto_apply"]["reason"]
