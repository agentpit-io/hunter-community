"""R3 · 复核工作流 / 决策注入 / 调度时点 —— 不连网、不连库、不连 Temporal。

覆盖四件事：

1. `fin-review-<market>` Schedule 的时点 = **时段末点 + `FIN_REVIEW_DELAY_MINUTES`**，
   且**现有 6 个 point 时点一个都没动**（R3 出口标准第 3 条）；
2. `FIN_REVIEW_DELAY_MINUTES` 可配 / 非法值回落 30 并留痕（第 4 条）；
3. `build_decision` 的**经验闸门**：命中即 halted 且带回依据 id + 快照 id（第 7 条的一半）；
4. 决策上下文里的 `memory` 一路进契约（`StrategyDecision.memory` / `to_paper_command` 的 `intent_ref`）。
"""

from __future__ import annotations

import httpx
import pytest

from datetime import datetime, timedelta, timezone

from app import activities, config, schedules
from app.bridge.contracts import StrategyDecision
from app.bridge.hunter_api import HunterApiClient
from app.strategy.sample import SampleMemoryBlocked, build_decision

SH = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 9, 9, 30, tzinfo=SH)


# ── ① 复核 Schedule：时点 = 时段末点 + 延迟；point 时点未动 ────────────────

def test_review_schedule_is_session_close_plus_delay(monkeypatch):
    monkeypatch.setenv("FIN_REVIEW_DELAY_MINUTES", "30")
    specs = {s.schedule_id: s for s in schedules.review_specs()}
    assert set(specs) == {"fin-review-CN_A", "fin-review-HK", "fin-review-US"}
    # 收盘时刻：A 股 15:00、港股 16:10（收市竞价那一段是最晚的）、美股 16:00
    assert specs["fin-review-CN_A"].cron == "30 15 * * 1-5"
    assert specs["fin-review-HK"].cron == "40 16 * * 1-5"
    assert specs["fin-review-US"].cron == "30 16 * * 1-5"
    for s in specs.values():
        assert s.workflow == "fin.review"


def test_review_delay_is_configurable(monkeypatch):
    """改值 → 时点跟着变（拍板的就是「可配」，不是「写死 30」）。"""
    monkeypatch.setenv("FIN_REVIEW_DELAY_MINUTES", "7")
    specs = {s.schedule_id: s for s in schedules.review_specs()}
    assert specs["fin-review-CN_A"].cron == "7 15 * * 1-5"
    assert specs["fin-review-HK"].cron == "17 16 * * 1-5"
    assert "7 分钟" in specs["fin-review-CN_A"].title


@pytest.mark.parametrize("bad", ["", "abc", "-5", "3.5", "三十"])
def test_illegal_delay_falls_back_to_default(monkeypatch, bad):
    """非法值 → 用默认 30（**不按 0、不报错退出**：配置写错不该让调度悄悄变）。"""
    monkeypatch.setenv("FIN_REVIEW_DELAY_MINUTES", bad)
    assert config.review_delay_minutes() == config.DEFAULT_REVIEW_DELAY_MINUTES == 30
    specs = {s.schedule_id: s for s in schedules.review_specs()}
    assert specs["fin-review-CN_A"].cron == "30 15 * * 1-5"


def test_review_schedules_use_market_timezones():
    specs = {s.schedule_id: s for s in schedules.review_specs()}
    assert specs["fin-review-CN_A"].timezone == "Asia/Shanghai"
    assert specs["fin-review-HK"].timezone == "Asia/Hong_Kong"
    assert specs["fin-review-US"].timezone == "America/New_York"


def test_existing_point_schedules_are_untouched():
    """R3 只**新增**一条 Schedule —— 现有 6 个 point 时点一个都没改。"""
    ids = [s.schedule_id for s in schedules.point_specs()]
    assert len(ids) == 18 and len(set(ids)) == 18
    assert ids[:6] == ["fin-point-CN_A-0915", "fin-point-CN_A-0930",
                       "fin-point-CN_A-1130", "fin-point-CN_A-1300",
                       "fin-point-CN_A-1455", "fin-point-CN_A-1530"]
    by_id = {s.schedule_id: s for s in schedules.point_specs()}
    assert by_id["fin-point-CN_A-0915"].cron == "15 9 * * 1-5"       # 未被复核时点搅动
    assert by_id["fin-point-HK-1615"].cron == "15 16 * * 1-5"


def test_session_close_takes_the_latest_session():
    assert schedules._session_close([{"open": "09:30", "close": "12:00"},
                                     {"open": "13:00", "close": "16:00"},
                                     {"open": "16:00", "close": "16:10"}]) == "16:10"
    assert schedules._hhmm_plus("16:10", 30) == "16:40"
    assert schedules._hhmm_plus("23:50", 30) == "00:20"      # 跨零点回绕


# ── ② 经验闸门：命中即 halted（只收紧，不放松）───────────────────────────

def _build(tier="operate", *, memory=None, code="00700", market="HK", version=0):
    return build_decision(
        project={"project_id": "prj_1", "tier": tier, "version": version},
        param={"max_position_pct": "0.2", "max_positions": 3},
        trade_date="2026-10-09", point="0930", now=NOW,
        code=code, strategy_key="sample-fixed", strategy_version="1.0",
        ttl_seconds=1800, market=market, market_order_supported=False,
        reference_price="421.0", lot_size=100, available_cash="1000000",
        memory=memory,
    )


def _memory(*items, snap="msnap_test"):
    return {"memory_snapshot_id": snap, "items": list(items)}


def _negative(app="HK:00700"):
    return {"experience_id": "exp_neg1", "kind": "verified", "status": "已确认",
            "statement": "追高后回撤概率显著上升", "applicability": app}


def test_memory_hit_halts_the_symbol():
    with pytest.raises(SampleMemoryBlocked) as ei:
        _build(memory=_memory(_negative()))
    exc = ei.value
    assert exc.memory_snapshot_id == "msnap_test"
    assert exc.blocked["experience_id"] == "exp_neg1"
    assert "HK:00700" in str(exc)


def test_memory_of_another_market_does_not_halt():
    """`US:0700` 拦不掉 `HK:00700` —— 规范化标的精确匹配（不是文本包含）。"""
    d = _build(memory=_memory(_negative("US:0700")))
    assert StrategyDecision.from_dict(d).intent.code == "00700"


def test_memory_of_another_kind_does_not_halt():
    for kind in ("fact", "hypothesis"):
        d = _build(memory=_memory({**_negative(), "kind": kind}))
        assert StrategyDecision.from_dict(d).intent.code == "00700"


def test_no_memory_still_orders():
    d = _build(memory=None)
    assert StrategyDecision.from_dict(d).intent.code == "00700"


# ── ③ memory 进契约与命令留痕 ─────────────────────────────────────────────

def test_decision_carries_memory_into_contract_and_command():
    d = StrategyDecision.from_dict(_build(memory=_memory(_negative("US:0700"))))
    assert d.memory["memory_snapshot_id"] == "msnap_test"
    assert d.memory["experience_ids"] == ["exp_neg1"]
    cmd = d.to_paper_command(project_id="prj_1", idempotency_key="k")
    assert cmd["intent_ref"]["memory"]["memory_snapshot_id"] == "msnap_test"


def test_decision_without_memory_keeps_empty_dict():
    d = StrategyDecision.from_dict(_build(memory=None))
    assert d.memory["memory_snapshot_id"] is None and d.memory["experience_ids"] == []


# ── ④ 三个新活动打的是正确的端点 / 幂等去重 ───────────────────────────────

def _install(monkeypatch):
    """把 activities 里的 HunterApiClient 换成打假传输的实例，记录每个请求。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/memory/query"):
            return httpx.Response(200, json={"memory_snapshot_id": None, "items": []})
        if request.url.path.endswith("/memory/evidence"):
            return httpx.Response(200, json={"ok": True, "experience": {
                "experience_id": "exp_new", "kind": "fact", "status": "已确认",
                "source": "ai", "statement": "s"}})
        if request.url.path.endswith("/review/collect"):
            return httpx.Response(200, json={"report": None, "facts": [], "trades": []})
        if request.url.path.endswith("/review/propose"):
            return httpx.Response(200, json={"candidates": [], "used_fallback": True})
        return httpx.Response(404, json={"detail": "unexpected"})

    transport = httpx.MockTransport(handler)

    def factory(*a, **kw):
        return HunterApiClient(base_url="http://api.test", internal_key="k",
                               client=httpx.Client(transport=transport))

    monkeypatch.setattr(activities, "HunterApiClient", factory)
    return seen


def test_freeze_memory_posts_freeze_and_for_decision(monkeypatch):
    import json as _json
    seen = _install(monkeypatch)
    activities.freeze_memory({"project_id": "prj_1", "market": "HK",
                              "trade_date": "2026-10-09", "point": "HK-0930",
                              "now": "2026-10-09T09:30:00+08:00", "purpose": "decision"})
    req = seen[-1]
    body = _json.loads(req.read().decode())
    assert "/api/internal/fin/memory/query" in str(req.url)
    assert body["freeze"] is True and body["for_decision"] is True
    assert body["purpose"] == "decision" and body["market"] == "HK"
    # `as_of` 是**决策那一刻** —— 回放基准，不是「现在」
    assert body["as_of"] == "2026-10-09T09:30:00+08:00"


def test_review_append_skips_candidates_already_stored(monkeypatch):
    """幂等：Activity 是 at-least-once，重试不能写第二遍（靠内容身份去重）。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/memory/query"):
            return httpx.Response(200, json={"memory_snapshot_id": None, "items": [
                {"experience_id": "exp_old", "statement": "同一句话",
                 "evidence": [{"evidence_kind": "trade", "ref_id": "trd_1"}]}]})
        if request.url.path.endswith("/memory/evidence"):
            return httpx.Response(200, json={"ok": True, "experience": {
                "experience_id": "exp_new", "kind": "fact", "status": "已确认",
                "source": "ai", "statement": "另一句话"}})
        return httpx.Response(404, json={})

    transport = httpx.MockTransport(handler)

    def factory(*a, **kw):
        return HunterApiClient(base_url="http://api.test", internal_key="k",
                               client=httpx.Client(transport=transport))

    monkeypatch.setattr(activities, "HunterApiClient", factory)
    out = activities.review_append({
        "project_id": "prj_1", "market": "HK", "trade_date": "2026-10-09",
        "candidates": [
            {"kind": "fact", "statement": "同一句话",
             "evidence": [{"evidence_kind": "trade", "ref_id": "trd_1"}]},     # 已存在 → 跳
            {"kind": "fact", "statement": "另一句话",
             "evidence": [{"evidence_kind": "trade", "ref_id": "trd_2"}]},     # 新 → 写
        ],
    })
    assert [w["experience_id"] for w in out["written"]] == ["exp_new"]
    assert len(out["skipped"]) == 1 and out["skipped"][0]["reason"] == "already_appended"
    writes = [r for r in seen if r.url.path.endswith("/memory/evidence")]
    assert len(writes) == 1     # 只写了一条，没有重复


def test_activity_list_registers_the_four_new_activities():
    from app.worker import activity_list
    names = {fn.__name__ for fn in activity_list()}
    for n in ("freeze_memory", "review_collect", "review_propose", "review_append"):
        assert n in names
