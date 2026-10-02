"""Schedule 定义（六个时点 + 三个 ETL 市场）。"""

from __future__ import annotations

from app import schedules


def test_six_point_schedules_plus_three_etl_plus_one_instrument():
    specs = schedules.all_specs()
    # 6 个时点 + 3 个市场 ETL + 1 个标的元数据同步（M7）
    assert len(specs) == 10


def test_instrument_sync_schedule():
    specs = {s.schedule_id: s for s in schedules.all_specs()}
    spec = specs["fin-instrument-sync"]
    assert spec.workflow == "fin.instrument_sync"
    assert spec.args["market"] == "cn"
    # 必须排在 preopen（09:15）之前 —— 开盘时风控要读的涨跌停已经是最新的
    assert spec.cron.split()[1] == "8"


def test_point_schedule_ids_and_workflows():
    specs = schedules.point_specs()
    assert [s.schedule_id for s in specs] == [
        "fin-point-0915", "fin-point-0930", "fin-point-1130",
        "fin-point-1300", "fin-point-1455", "fin-point-1530",
    ]
    assert [s.workflow for s in specs] == [
        "fin.point_0915", "fin.point_0930", "fin.point_1130",
        "fin.point_1300", "fin.point_1455", "fin.point_1530",
    ]


def test_etl_schedules_cover_three_markets():
    markets = {s.args["market"] for s in schedules.ETL_SCHEDULES}
    assert markets == {"cn", "hk", "us"}


def test_all_schedule_ids_unique():
    ids = [s.schedule_id for s in schedules.all_specs()]
    assert len(ids) == len(set(ids))


def test_every_cron_is_five_fields():
    for s in schedules.all_specs():
        assert len(s.cron.split()) == 5, s.cron


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
        assert built.spec.time_zone_name == "Asia/Shanghai"
