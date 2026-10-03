"""Schedule 定义（**每市场一组时点** + 三个 ETL 市场 + 三个市场的标的元数据同步）。"""

from __future__ import annotations

import pytest

from app import schedules


def test_three_markets_eighteen_point_schedules_plus_etl_plus_instrument():
    specs = schedules.all_specs()
    # 3 市场 × 6 时点 + 3 个市场 ETL + 3 个市场的标的元数据同步 + 3 个市场的收盘后复核（R3）
    assert len(specs) == 18 + 3 + 3 + 3


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
