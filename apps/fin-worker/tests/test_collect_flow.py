"""L09 · 采集补齐链路（**不连网、不连库、不连 Temporal**）。

覆盖：

1. `fin-news-<market>` / `fin-fundamental-<market>` 的时点 = **时段末点 + 采集延迟**；
   新闻三市场各一条、**基本面只排 A 股**（港美股财报未接，排了只是空跑）；
2. `FIN_NEWS_DELAY_MINUTES` / `FIN_FUNDAMENTAL_DELAY_MINUTES` 可配 / 非法值回落并留痕；
3. 两个活动打对端点、body 正确、HTTP 错误**不吞**（交给 Temporal 重试）；
4. 两个工作流把 Schedule 的 args 转成活动入参，**如实带回读数**（`ok=False` → `status=failed`）；
5. 白名单 / 活动清单登记齐全（防漂）。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app import activities, config, runtime_control, schedules, workflows
from app.bridge.hunter_api import HunterApiClient


def _install_api(monkeypatch, handler):
    """把活动里的 `HunterApiClient` 换成绑到 MockTransport 的同一套客户端（复用它的
    路径拼装 / 鉴权头 / 超时逻辑），只替换传输层。"""
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


# ── ① Schedule：时段末点 + 采集延迟 ───────────────────────────────────────

def test_news_schedule_is_session_close_plus_delay(monkeypatch):
    monkeypatch.setenv("FIN_NEWS_DELAY_MINUTES", "90")
    specs = {s.schedule_id: s for s in schedules.news_specs()}
    assert set(specs) == {"fin-news-CN_A", "fin-news-HK", "fin-news-US"}
    # 收盘：A 股 15:00 → 16:30；港股 16:10（收市竞价）→ 17:40；美股 16:00 → 17:30
    assert specs["fin-news-CN_A"].cron == "30 16 * * 1-5"
    assert specs["fin-news-HK"].cron == "40 17 * * 1-5"
    assert specs["fin-news-US"].cron == "30 17 * * 1-5"
    for s in specs.values():
        assert s.workflow == "fin.news_collect"
    # args.market 用 ETL 的小写三字母（工作流按它走 collection._MARKETS）
    assert specs["fin-news-CN_A"].args["market"] == "cn"
    assert specs["fin-news-HK"].args["market"] == "hk"
    assert specs["fin-news-US"].args["market"] == "us"
    assert specs["fin-news-CN_A"].timezone == "Asia/Shanghai"
    assert specs["fin-news-HK"].timezone == "Asia/Hong_Kong"
    assert specs["fin-news-US"].timezone == "America/New_York"


def test_fundamental_schedule_is_cn_only(monkeypatch):
    """**只有 A 股**排财报采集 —— 港美股财报未接，排了只是空跑。"""
    monkeypatch.setenv("FIN_FUNDAMENTAL_DELAY_MINUTES", "120")
    specs = {s.schedule_id: s for s in schedules.fundamental_specs()}
    assert set(specs) == {"fin-fundamental-CN_A"}
    assert specs["fin-fundamental-CN_A"].cron == "0 17 * * 1-5"     # 15:00 + 120
    assert specs["fin-fundamental-CN_A"].workflow == "fin.fundamental_collect"
    assert specs["fin-fundamental-CN_A"].args["market"] == "cn"


def test_news_delay_is_configurable(monkeypatch):
    monkeypatch.setenv("FIN_NEWS_DELAY_MINUTES", "7")
    specs = {s.schedule_id: s for s in schedules.news_specs()}
    assert specs["fin-news-CN_A"].cron == "7 15 * * 1-5"           # 15:00 + 7
    assert "7 分钟" in specs["fin-news-CN_A"].title


@pytest.mark.parametrize("bad", ["", "abc", "-5", "3.5", "三十"])
def test_illegal_news_delay_falls_back_to_default(monkeypatch, bad):
    monkeypatch.setenv("FIN_NEWS_DELAY_MINUTES", bad)
    assert config.news_delay_minutes() == config.DEFAULT_NEWS_DELAY_MINUTES == 90
    specs = {s.schedule_id: s for s in schedules.news_specs()}
    assert specs["fin-news-CN_A"].cron == "30 16 * * 1-5"


@pytest.mark.parametrize("bad", ["", "abc", "-5", "3.5"])
def test_illegal_fundamental_delay_falls_back_to_default(monkeypatch, bad):
    monkeypatch.setenv("FIN_FUNDAMENTAL_DELAY_MINUTES", bad)
    assert config.fundamental_delay_minutes() == config.DEFAULT_FUNDAMENTAL_DELAY_MINUTES == 120


def test_existing_schedules_untouched(monkeypatch):
    """新加的两组采集 Schedule **不动**任何既有 Schedule。"""
    assert len(schedules.point_specs()) == 18
    assert {s.schedule_id for s in schedules.review_specs()} == {
        "fin-review-CN_A", "fin-review-HK", "fin-review-US"}
    assert len(schedules.ETL_SCHEDULES) == 3
    assert len(schedules.INSTRUMENT_SCHEDULES) == 3


# ── ② 活动：打对端点 ──────────────────────────────────────────────────────

def test_collect_news_posts_correct_endpoint(monkeypatch):
    seen = _install_api(monkeypatch, lambda r: httpx.Response(200, json={
        "ok": True, "market": "CN_A", "fetched": 3, "inserted": 2, "gaps": 1,
        "news_rows": 10, "latest_news_fetched_at": "2026-10-04T16:30:00+00:00"}))
    out = activities.collect_news({"market": "cn", "limit": 5, "per_code": 3})
    req = seen[-1]
    body = json.loads(req.read().decode())
    assert "/api/internal/fin/collect/news" in str(req.url)
    assert body == {"market": "cn", "limit": 5, "per_code": 3}
    assert out["inserted"] == 2 and out["gaps"] == 1


def test_collect_fundamentals_posts_correct_endpoint(monkeypatch):
    seen = _install_api(monkeypatch, lambda r: httpx.Response(200, json={
        "ok": True, "market": "CN_A", "candidates": 4, "downloaded": 3, "metrics": 84,
        "failed": 1, "skipped": 0}))
    out = activities.collect_fundamentals({"market": "cn", "limit": 4, "keep_raw": False})
    req = seen[-1]
    body = json.loads(req.read().decode())
    assert "/api/internal/fin/collect/fundamental" in str(req.url)
    assert body == {"market": "cn", "limit": 4, "keep_raw": False}
    assert out["downloaded"] == 3


def test_collect_http_error_is_not_swallowed(monkeypatch):
    """api 挂了 / 5xx → 抛 `ApiError`，交给 Temporal 重试（**不吞**）。"""
    _install_api(monkeypatch, lambda r: httpx.Response(503, text="api 不可用"))
    from app.bridge.hunter_api import ApiError
    with pytest.raises(ApiError) as ei:
        activities.collect_news({"market": "cn"})
    assert ei.value.status == 503


def test_activity_list_registers_collect_activities():
    from app.worker import activity_list
    names = {fn.__name__ for fn in activity_list()}
    assert {"collect_news", "collect_fundamentals"} <= names


# ── ③ 工作流：转参 + 如实带回读数 ─────────────────────────────────────────

def _patch_exec(monkeypatch, result):
    calls: list[tuple[str, dict]] = []

    async def fake_exec(fn, arg, **kw):
        calls.append((fn.__name__, arg))
        return result

    monkeypatch.setattr(workflows, "_exec", fake_exec)
    return calls


def test_news_workflow_passes_market_and_reads_back(monkeypatch):
    calls = _patch_exec(monkeypatch, {"ok": True, "inserted": 5, "gaps": 2})
    out = asyncio.run(workflows.NewsCollectWorkflow().run({"market": "hk", "limit": 7}))
    assert calls == [("collect_news", {"market": "hk", "limit": 7,
                                       "per_code": None, "codes": None})]
    assert out["status"] == "collected" and out["inserted"] == 5


def test_news_workflow_reports_failure_honestly(monkeypatch):
    """数据源不通 → 活动带回 `ok=False` → 工作流标 `failed`（**不伪装成功**）。"""
    _patch_exec(monkeypatch, {"ok": False, "reason": "news 表不存在"})
    out = asyncio.run(workflows.NewsCollectWorkflow().run({"market": "cn"}))
    assert out["status"] == "failed" and "news 表不存在" in out["reason"]


def test_fundamental_workflow_marks_no_source_as_failed(monkeypatch):
    """港美股财报未接 → 活动如实回 `ok=False` → 工作流标 `failed`（**不是缺口**，是没源）。"""
    _patch_exec(monkeypatch, {"ok": False, "reason": "US 财报数据源未接"})
    out = asyncio.run(workflows.FundamentalCollectWorkflow().run({"market": "us"}))
    assert out["status"] == "failed" and "未接" in out["reason"]


def test_collect_workflows_registered():
    def _name(cls):
        definition = getattr(cls, "__temporal_workflow_definition", None)
        assert definition is not None, f"{cls.__name__} 不是 Temporal 工作流"
        return definition.name
    names = {_name(c) for c in workflows.ALL_WORKFLOWS}
    assert {"fin.news_collect", "fin.fundamental_collect"} <= names


def test_collect_templates_registered():
    assert set(runtime_control.TEMPLATES) >= {"fin.news_collect", "fin.fundamental_collect"}
    # 市场用小写三字母（etl_market），与工作流一致
    assert runtime_control.TEMPLATES["fin.news_collect"]["market"][0] == "etl_market"
