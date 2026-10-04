"""Schedule 定义（**每市场一组时点** + 三个 ETL 市场 + 三个市场的标的元数据同步）。"""

from __future__ import annotations

import pytest

from app import config, schedules


def test_three_markets_eighteen_point_schedules_plus_etl_plus_instrument():
    specs = schedules.all_specs()
    # 3 市场 × 6 时点 + 3 ETL + 3 标的元数据同步 + 3 收盘后复核（R3）+ 3 影子验证（R7）
    # + 3 自动盯盘观察（R13）+ 3 自动提案（L01）
    assert len(specs) == 18 + 3 + 3 + 3 + 3 + 3 + 3


def test_r13_observe_schedule_one_per_market_after_shadow():
    """R13 · 自动盯盘 Schedule：每市场一条，排在影子之后，且**不动**任何既有 Schedule。"""
    specs = {s.schedule_id: s for s in schedules.observe_specs()}
    assert set(specs) == {"fin-observe-CN_A", "fin-observe-HK", "fin-observe-US"}
    for sid, spec in specs.items():
        assert spec.workflow == "fin.observe"
        assert spec.args["market"] == sid.rsplit("-", 1)[1]
        assert spec.args["point"] == f"{spec.args['market']}-observe"
    # CN_A 收盘 15:00 + 复核 30 + 影子 15 + 观察 30 → 16:15
    assert specs["fin-observe-CN_A"].cron == "15 16 * * 1-5"
    # HK 收市竞价末点 16:10 + 75 → 17:25；US 16:00 + 75 → 17:15
    assert specs["fin-observe-HK"].cron == "25 17 * * 1-5"
    assert specs["fin-observe-US"].cron == "15 17 * * 1-5"
    # 既有 Schedule 一条没变：point 时点、复核、影子都在
    assert len(schedules.point_specs()) == 18
    assert {s.schedule_id for s in schedules.review_specs()} == {
        "fin-review-CN_A", "fin-review-HK", "fin-review-US"}
    assert {s.schedule_id for s in schedules.shadow_specs()} == {
        "fin-shadow-CN_A", "fin-shadow-HK", "fin-shadow-US"}
    # 与观察那条不撞 id
    assert {s.schedule_id for s in schedules.shadow_specs()} & set(specs) == set()


def test_r13_observe_delay_is_configurable(monkeypatch):
    """`FIN_OBSERVE_DELAY_MINUTES` 改值 → 时点跟着变（可配，不是写死 30）。"""
    monkeypatch.setenv("FIN_OBSERVE_DELAY_MINUTES", "5")
    specs = {s.schedule_id: s for s in schedules.observe_specs()}
    assert specs["fin-observe-CN_A"].cron == "50 15 * * 1-5"     # 15:00+30+15+5
    assert "5 分钟" in specs["fin-observe-CN_A"].title


@pytest.mark.parametrize("bad", ["", "abc", "-5", "3.5", "三十"])
def test_r13_illegal_observe_delay_falls_back_to_default(monkeypatch, bad):
    """非法值 → 默认 30（**不按 0**：配置写错不该让保护悄悄变）。"""
    monkeypatch.setenv("FIN_OBSERVE_DELAY_MINUTES", bad)
    assert config.observe_delay_minutes() == config.DEFAULT_OBSERVE_DELAY_MINUTES == 30
    specs = {s.schedule_id: s for s in schedules.observe_specs()}
    assert specs["fin-observe-CN_A"].cron == "15 16 * * 1-5"


def test_r13_observe_schedules_use_market_timezones():
    specs = {s.schedule_id: s for s in schedules.observe_specs()}
    assert specs["fin-observe-CN_A"].timezone == "Asia/Shanghai"
    assert specs["fin-observe-HK"].timezone == "Asia/Hong_Kong"
    assert specs["fin-observe-US"].timezone == "America/New_York"


def test_r7_shadow_schedule_one_per_market_after_review():
    """R7 · 影子验证 Schedule：每市场一条，排在复核之后，且**不动** 6 个 point 时点。"""
    specs = {s.schedule_id: s for s in schedules.shadow_specs()}
    assert set(specs) == {"fin-shadow-CN_A", "fin-shadow-HK", "fin-shadow-US"}
    for sid, spec in specs.items():
        assert spec.workflow == "fin.shadow"
        assert spec.args["market"] == sid.rsplit("-", 1)[1]
    # CN_A 收盘 15:00 + 复核 30 + 影子 15 → 15:45
    assert specs["fin-shadow-CN_A"].cron == "45 15 * * 1-5"
    # 6 个 point 时点一字不变（这是本轮的硬约束）
    assert len(schedules.point_specs()) == 18
    assert {s.schedule_id for s in schedules.point_specs()} & set(specs) == set()


def test_point_schedules_have_per_market_timezone():
    """每个市场的时点 Schedule 用它自己的 IANA 时区名（夏令时自动跟随）。"""
    by_tz = {s.schedule_id: s.timezone for s in schedules.point_specs()}
    assert by_tz["fin-point-CN_A-0915"] == "Asia/Shanghai"
    assert by_tz["fin-point-HK-0930"] == "Asia/Hong_Kong"
    assert by_tz["fin-point-US-0930"] == "America/New_York"


def test_point_schedule_args_carry_market_and_point():
    specs = {s.schedule_id: s for s in schedules.point_specs()}
    hk = specs["fin-point-HK-1200"]
    assert hk.workflow == "fin.point_match_a"
    assert hk.args["market"] == "HK"
    assert hk.args["at"] == "12:00"
    assert hk.args["point"] == "HK-1200"
    us = specs["fin-point-US-1615"]
    assert us.workflow == "fin.point_close"
    assert us.args["market"] == "US"


def test_point_schedule_ids_and_workflows_cover_three_markets():
    specs = schedules.point_specs()
    ids = [s.schedule_id for s in specs]
    assert len(ids) == 18 and len(set(ids)) == 18
    markets = {i.split("-")[2] for i in ids}
    assert markets == {"CN_A", "HK", "US"}


def test_instrument_sync_covers_three_markets():
    by_market = {s.args["market"]: s for s in schedules.INSTRUMENT_SCHEDULES}
    assert set(by_market) == {"cn", "hk", "us"}
    for spec in by_market.values():
        assert spec.workflow == "fin.instrument_sync"
    assert len({s.schedule_id for s in schedules.INSTRUMENT_SCHEDULES}) == 3


def test_etl_schedules_cover_three_markets():
    markets = {s.args["market"] for s in schedules.ETL_SCHEDULES}
    assert markets == {"cn", "hk", "us"}


def test_all_schedule_ids_unique():
    ids = [s.schedule_id for s in schedules.all_specs()]
    assert len(ids) == len(set(ids))


def test_every_cron_is_five_fields():
    for s in schedules.all_specs():
        assert len(s.cron.split()) == 5, s.cron


def test_market_rules_drive_the_points():
    """给一组市场规则（含不同时点），Schedule 就按它建 —— 这是「从表读」的证据。"""
    rules = [
        {"market": "CN_A", "timezone": "Asia/Shanghai",
         "points": ["09:15", "09:30", "11:30", "13:00", "14:55", "15:30"]},
        {"market": "HK", "timezone": "Asia/Hong_Kong",
         "points": ["09:20", "09:35", "12:05", "13:05", "15:55", "16:15"]},
        {"market": "US", "timezone": "America/New_York",
         "points": ["09:05", "09:30", "12:00", "14:55", "15:55", "16:15"]},
    ]
    specs = {s.schedule_id: s for s in schedules.point_specs(rules)}
    assert "fin-point-HK-0920" in specs
    assert specs["fin-point-HK-0920"].cron == "20 9 * * 1-5"
    assert specs["fin-point-HK-0920"].timezone == "Asia/Hong_Kong"
    assert "fin-point-US-0905" in specs


def test_fallback_market_rules_match_seed():
    rules = {r["market"]: r for r in schedules.fallback_market_rules()}
    assert rules["CN_A"]["timezone"] == "Asia/Shanghai"
    assert rules["HK"]["points"][2] == "12:00"
    assert rules["US"]["timezone"] == "America/New_York"


def test_every_spec_builds_a_real_temporal_schedule():
    """真构造一次 SDK 对象。

    M4 首次起容器时 `ScheduleSpec(timezone=...)` 报
    `TypeError: unexpected keyword argument`（这个版本叫 `time_zone_name`），
    Schedule 一个都没建上。构造一次就能挡住这类「字段名与 SDK 版本不符」。
    """
    from temporalio.client import Schedule

    for spec in schedules.all_specs():
        built = schedules.build_schedule(spec)
        assert isinstance(built, Schedule)
        assert built.action.workflow == spec.workflow
        assert built.spec.cron_expressions == [spec.cron]
        # 市场 Schedule 用各自时区；ETL / 标的同步没给时区 → 回落默认（上海）
        assert built.spec.time_zone_name == (spec.timezone or "Asia/Shanghai")
