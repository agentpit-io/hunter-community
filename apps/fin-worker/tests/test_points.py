"""六个时点的定义（M-14）。"""

from __future__ import annotations

from app.points import BY_KEY, BY_WORKFLOW, POINTS

EXPECTED = {
    "0915": ("09:15", "preopen", "fin.point_0915"),
    "0930": ("09:30", "decide", "fin.point_0930"),
    "1130": ("11:30", "match", "fin.point_1130"),
    "1300": ("13:00", "match", "fin.point_1300"),
    "1455": ("14:55", "match", "fin.point_1455"),
    "1530": ("15:30", "close", "fin.point_1530"),
}


def test_six_points_exactly():
    assert len(POINTS) == 6
    assert {p.key for p in POINTS} == set(EXPECTED)


def test_each_point_matches_expected_time_kind_workflow():
    for key, (at, kind, wf) in EXPECTED.items():
        p = BY_KEY[key]
        assert (p.at, p.kind, p.workflow) == (at, kind, wf)


def test_crons_are_five_field_and_weekday_only():
    for p in POINTS:
        parts = p.cron.split()
        assert len(parts) == 5, p.cron
        assert parts[4] == "1-5", "时点只在工作日触发，节假日交给交易日历"


def test_workflow_names_unique():
    names = [p.workflow for p in POINTS]
    assert len(names) == len(set(names))
    assert set(BY_WORKFLOW) == set(names)


def test_kinds_cover_the_day():
    kinds = [p.kind for p in POINTS]
    assert kinds == ["preopen", "decide", "match", "match", "match", "close"]
