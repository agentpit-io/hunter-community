"""工作流注册（六个时点 + ETL），以及「六时点只在 Temporal」的守卫。"""

from __future__ import annotations

import tokenize
from pathlib import Path

from app import points, workflows

ROOT = Path(__file__).resolve().parents[1]


def _code_only(path: Path) -> str:
    """源码里**只有代码**的部分（注释与字符串字面量都去掉）。

    这样守卫盯的是「真的写了兜底定时器」，而不是文档里提到这个词。
    """
    skip = {tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE,
            tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING}
    parts: list[str] = []
    with path.open("rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type not in skip:
                parts.append(tok.string)
    return " ".join(parts)


def _wf_name(cls) -> str:
    definition = getattr(cls, "__temporal_workflow_definition", None)
    assert definition is not None, f"{cls.__name__} 不是 Temporal 工作流"
    return definition.name


def test_all_workflows_registered():
    names = {_wf_name(c) for c in workflows.ALL_WORKFLOWS}
    assert names == {
        "fin.point_0915", "fin.point_0930", "fin.point_1130",
        "fin.point_1300", "fin.point_1455", "fin.point_1530",
        "fin.market_etl",
    }


def test_every_point_has_a_registered_workflow():
    registered = {_wf_name(c) for c in workflows.ALL_WORKFLOWS}
    for p in points.POINTS:
        assert p.workflow in registered


def test_activity_list_covers_workflow_calls():
    from app.worker import activity_list

    names = {fn.__name__ for fn in activity_list()}
    assert {
        "sync_calendar", "read_calendar", "list_active_projects",
        "begin_point_job", "write_checkpoint", "finish_point_job", "fail_point_job",
        "confirm_t1", "build_decision", "submit_decision",
        "match_open_orders", "close_day", "trigger_market_etl",
        "generate_daily_report",
    } == names


def test_no_fallback_cron_or_in_process_timer():
    """六时点只归 Temporal：fin-worker 里不许出现 APScheduler / threading.Timer / crontab。

    `01方案 §5.3`「上层调度只有一个权威」。留一条兜底定时器 = 两套调度都以为自己权威。
    """
    bad = ("apscheduler", "AsyncIOScheduler", "BackgroundScheduler", "Timer", "crontab")
    hits = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        code = _code_only(path)
        for token in bad:
            if token in code:
                hits.append(f"{path.name}: {token}")
    assert not hits, "fin-worker 不许自带兜底调度：\n" + "\n".join(hits)
