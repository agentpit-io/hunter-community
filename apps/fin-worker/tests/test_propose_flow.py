"""L01 · 自动提案链路（**不连网、不连库、不连 Temporal**）。

覆盖四件事：

1. `fin-propose-<market>` 的时点 = **时段末点 + 复核延迟 + 提案延迟**，排在 review 之后；
2. `FIN_PROPOSE_DELAY_MINUTES` 可配 / 非法值回落 2 并留痕；
3. 两个活动打对端点：`propose_candidates` 只读、`propose_submit` 走**唯一写入口**；
4. **编排**：`_propose_for_project` 在 `action=skip` 时如实记「不提」与原因（不硬造提案）；
   `action=propose` 时逐条提交，**一条候选失败（撞唯一索引 / 闸门拒绝）不挂别的候选**。
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
        return HunterApiClient(base_url="http://api.test", internal_key="k",
                               client=httpx.Client(transport=transport))

    monkeypatch.setattr(activities, "HunterApiClient", factory)
    return seen


# ── ① Schedule：时段末点 + 复核 + 提案延迟 ────────────────────────────────

def test_propose_schedule_is_session_close_plus_delays(monkeypatch):
    monkeypatch.setenv("FIN_REVIEW_DELAY_MINUTES", "30")
    monkeypatch.setenv("FIN_PROPOSE_DELAY_MINUTES", "2")
    specs = {s.schedule_id: s for s in schedules.propose_specs()}
    assert set(specs) == {"fin-propose-CN_A", "fin-propose-HK", "fin-propose-US"}
    # 收盘：A 股 15:00、港股 16:10（收市竞价）、美股 16:00 → +30（复核）+2（提案）
    assert specs["fin-propose-CN_A"].cron == "32 15 * * 1-5"
    assert specs["fin-propose-HK"].cron == "42 16 * * 1-5"
    assert specs["fin-propose-US"].cron == "32 16 * * 1-5"
    for s in specs.values():
        assert s.workflow == "fin.propose"


def test_propose_schedule_runs_after_review(monkeypatch):
    """提案排在复核**之后**（复盘产经验 → 提案），不抢它的时点。"""
    monkeypatch.setenv("FIN_REVIEW_DELAY_MINUTES", "30")
    monkeypatch.setenv("FIN_PROPOSE_DELAY_MINUTES", "2")
    review = {s.schedule_id: s for s in schedules.review_specs()}
    propose = {s.schedule_id: s for s in schedules.propose_specs()}
    # 同市场：提案分钟数 > 复核分钟数（晚出发）
    assert propose["fin-propose-HK"].cron != review["fin-review-HK"].cron
    assert propose["fin-propose-HK"].args["market"] == "HK"
    assert propose["fin-propose-HK"].timezone == "Asia/Hong_Kong"


def test_propose_delay_is_configurable(monkeypatch):
    monkeypatch.setenv("FIN_PROPOSE_DELAY_MINUTES", "7")
    specs = {s.schedule_id: s for s in schedules.propose_specs()}
    assert specs["fin-propose-CN_A"].cron == "37 15 * * 1-5"      # 15:00 + 30 + 7
    assert "7 分钟" in specs["fin-propose-CN_A"].title


@pytest.mark.parametrize("bad", ["", "abc", "-5", "3.5", "三十"])
def test_illegal_propose_delay_falls_back_to_default(monkeypatch, bad):
    monkeypatch.setenv("FIN_PROPOSE_DELAY_MINUTES", bad)
    assert config.propose_delay_minutes() == config.DEFAULT_PROPOSE_DELAY_MINUTES == 2
    specs = {s.schedule_id: s for s in schedules.propose_specs()}
    assert specs["fin-propose-CN_A"].cron == "32 15 * * 1-5"


# ── ② 活动：打对端点 ──────────────────────────────────────────────────────

def test_propose_candidates_posts_correct_endpoint(monkeypatch):
    seen = _install_api(monkeypatch, lambda r: httpx.Response(200, json={
        "action": "skip", "reason": "证据不足", "drafts": []}))
    out = activities.propose_candidates({"project_id": "prj_1", "market": "HK"})
    req = seen[-1]
    body = json.loads(req.read().decode())
    assert "/api/internal/fin/evolution/propose-candidates" in str(req.url)
    assert body == {"project_id": "prj_1", "market": "HK"}
    assert out["action"] == "skip"


def test_propose_submit_posts_draft_to_unique_write_entry(monkeypatch):
    seen = _install_api(monkeypatch, lambda r: httpx.Response(200, json={
        "ok": True, "proposal": {"proposal_id": "evp_1", "target": "strategy",
                                 "direction": "tighten"}}))
    draft = {"proposal_id": "evp_1", "evidence_refs": ["exp_1"],
             "candidate_config": {"stop_loss_pct": -0.035}}
    out = activities.propose_submit({"draft": draft})
    req = seen[-1]
    body = json.loads(req.read().decode())
    assert "/api/internal/fin/evolution/proposal" in str(req.url)
    assert body == draft                       # draft 原样提交，工作流不加工
    assert out["proposal"]["proposal_id"] == "evp_1"
    assert out["proposal"]["direction"] == "tighten"


def test_propose_submit_raises_on_http_error(monkeypatch):
    """撞唯一索引（409）/ 闸门拒绝（400）→ 抛 `ApiError`，不吞。"""
    _install_api(monkeypatch, lambda r: httpx.Response(409, text="同一 base 已有待验证提案"))
    with pytest.raises(ApiError) as ei:
        activities.propose_submit({"draft": {"proposal_id": "evp_1"}})
    assert ei.value.status == 409


def test_activity_list_registers_propose_activities():
    from app.worker import activity_list
    names = {fn.__name__ for fn in activity_list()}
    assert {"propose_candidates", "propose_submit"} <= names


# ── ③ 编排：skip 如实记 / 逐条隔离 ────────────────────────────────────────

class _FakeLogger:
    def __init__(self):
        self.warnings: list[str] = []

    def warning(self, *args, **kwargs):
        self.warnings.append(" ".join(str(a) for a in args))

    def info(self, *args, **kwargs):
        pass


async def _run_propose_project(monkeypatch, *, candidates, submit_results):
    fake_logger = _FakeLogger()
    monkeypatch.setattr(workflows.workflow, "logger", fake_logger)

    async def fake_exec(fn, arg, **kw):
        name = fn.__name__
        if name == "propose_candidates":
            return candidates
        if name == "propose_submit":
            pid = arg["draft"]["proposal_id"]
            value = submit_results[pid]
            if isinstance(value, Exception):
                raise value
            return value
        raise AssertionError(f"未预期的活动 {name}")

    monkeypatch.setattr(workflows, "_exec", fake_exec)
    out = await workflows._propose_for_project(
        {"project_id": "prj_1"}, "HK", "2026-10-09", "2026-10-09T16:42:00+08:00")
    return out, fake_logger


def test_skip_records_reason_without_submitting(monkeypatch):
    """关着 / 超预算 / 证据不足 → 如实记「不提」与原因，**不硬造提案**。"""
    out, _ = asyncio.run(_run_propose_project(
        monkeypatch,
        candidates={"action": "skip", "reason": "本窗口已提 1 条，达到预算上限 1",
                    "budget": {"limit": 1, "used": 1, "remaining": 0}, "drafts": []},
        submit_results={}))
    assert out["action"] == "skip"
    assert "预算" in out["reason"]
    assert out["budget"]["remaining"] == 0
    assert out["proposals"] == []                # 一条都没提交


def test_propose_submits_each_draft(monkeypatch):
    out, _ = asyncio.run(_run_propose_project(
        monkeypatch,
        candidates={"action": "propose", "reason": "ok", "budget": {"limit": 2},
                    "drafts": [{"proposal_id": "evp_1", "evidence_refs": ["e1", "e2"]},
                               {"proposal_id": "evp_2", "evidence_refs": ["e3"]}]},
        submit_results={
            "evp_1": {"proposal": {"proposal_id": "evp_1", "target": "strategy",
                                   "direction": "tighten"}},
            "evp_2": {"proposal": {"proposal_id": "evp_2", "target": "strategy",
                                   "direction": "tighten"}},
        }))
    assert out["action"] == "propose"
    recs = {r["proposal_id"]: r for r in out["proposals"]}
    assert recs["evp_1"]["ok"] is True and recs["evp_1"]["direction"] == "tighten"
    assert recs["evp_1"]["evidence_count"] == 2
    assert recs["evp_2"]["ok"] is True


def test_one_failed_draft_does_not_abort_the_rest(monkeypatch):
    """一条候选撞唯一索引（409）→ 记进汇总继续，后面的候选照常提交。"""
    out, logger = asyncio.run(_run_propose_project(
        monkeypatch,
        candidates={"action": "propose", "reason": "ok", "budget": {"limit": 2},
                    "drafts": [{"proposal_id": "evp_1", "evidence_refs": ["e1"]},
                               {"proposal_id": "evp_2", "evidence_refs": ["e2"]}]},
        submit_results={
            "evp_1": ApiError(409, "同一 base 已有待验证提案"),
            "evp_2": {"proposal": {"proposal_id": "evp_2", "target": "strategy",
                                   "direction": "tighten"}},
        }))
    recs = {r["proposal_id"]: r for r in out["proposals"]}
    assert recs["evp_1"]["ok"] is False and "409" in recs["evp_1"]["error"]
    assert recs["evp_2"]["ok"] is True           # 第二条照常
    assert len(logger.warnings) == 1
