"""每个市场的一组时点（N4）。

一期是 A 股六个固定时点；N4 起**三个市场各一组**，时区各按市场。
"""

from __future__ import annotations

from app.points import (
    ALL_POINTS,
    DEFAULT_POINTS,
    MARKETS,
    POINT_SHAPE,
    POINTS,
    points_for,
    resolve_point_key,
)

# A 股（一期口径，逐字不变）
EXPECTED_CN = {
    "CN_A-0915": ("09:15", "preopen", "fin.point_preopen"),
    "CN_A-0930": ("09:30", "decide", "fin.point_decide"),
    "CN_A-1130": ("11:30", "match", "fin.point_match_a"),
    "CN_A-1300": ("13:00", "match", "fin.point_match_b"),
    "CN_A-1455": ("14:55", "match", "fin.point_match_c"),
    "CN_A-1530": ("15:30", "close", "fin.point_close"),
}


def test_cn_six_points_exactly():
    assert len(POINTS) == 6
    assert {p.key for p in POINTS} == set(EXPECTED_CN)


def test_cn_each_point_matches_expected_time_kind_workflow():
    by_key = {p.key: p for p in POINTS}
    for key, (at, kind, wf) in EXPECTED_CN.items():
        p = by_key[key]
        assert (p.at, p.kind, p.workflow, p.market) == (at, kind, wf, "CN_A")


def test_three_markets_six_points_each():
    assert MARKETS == ("CN_A", "HK", "US")
    assert len(ALL_POINTS) == 18
    by_market: dict[str, list] = {}
    for p in ALL_POINTS:
        by_market.setdefault(p.market, []).append(p)
    assert set(by_market) == {"CN_A", "HK", "US"}
    for market, pts in by_market.items():
        assert len(pts) == 6, market
        assert [p.kind for p in pts] == ["preopen", "decide", "match", "match", "match", "close"]


def test_default_points_times_match_the_db_seed():
    """兜底时点必须与 `0030` 的 `fin_market_rule.points` 种子逐项一致。"""
    assert DEFAULT_POINTS["CN_A"] == ["09:15", "09:30", "11:30", "13:00", "14:55", "15:30"]
    assert DEFAULT_POINTS["HK"] == ["09:15", "09:30", "12:00", "13:00", "15:55", "16:15"]
    assert DEFAULT_POINTS["US"] == ["09:15", "09:30", "12:00", "14:55", "15:55", "16:15"]


def test_point_keys_are_market_unique():
    keys = [p.key for p in ALL_POINTS]
    assert len(keys) == len(set(keys))


def test_crons_are_five_field_and_weekday_only():
    for p in ALL_POINTS:
        parts = p.cron.split()
        assert len(parts) == 5, p.cron
        assert parts[4] == "1-5", "时点只在工作日触发，节假日交给交易日历"


def test_cron_derived_from_local_time():
    hk_1000 = points_for("HK", ["10:00", "10:05", "10:10", "10:15", "10:20", "10:25"])
    assert hk_1000[0].cron == "0 10 * * 1-5"
    assert hk_1000[3].cron == "15 10 * * 1-5"


def test_role_workflow_names_are_stable_and_shared_across_markets():
    """同一角色在三个市场共用同一个工作流类型（市场经 Schedule 的 args 传入）。"""
    roles = {p.role: p.workflow for p in ALL_POINTS}
    assert roles == {
        "preopen": "fin.point_preopen", "decide": "fin.point_decide",
        "match_a": "fin.point_match_a", "match_b": "fin.point_match_b",
        "match_c": "fin.point_match_c", "close": "fin.point_close",
    }
    assert [r for r, _ in POINT_SHAPE] == [
        "preopen", "decide", "match_a", "match_b", "match_c", "close"]


def test_points_for_rejects_wrong_length():
    import pytest
    with pytest.raises(ValueError):
        points_for("US", ["09:15", "09:30"])


def test_resolve_point_key_accepts_full_and_bare():
    assert resolve_point_key("CN_A-0915").market == "CN_A"
    assert resolve_point_key("HK-0930").market == "HK"
    assert resolve_point_key("US-1615").workflow == "fin.point_close"
    # 裸 HHMM 按 A 股解析（一期运维脚本的兼容）
    assert resolve_point_key("0915").key == "CN_A-0915"
    assert resolve_point_key("9999") is None
    assert resolve_point_key("nope") is None
