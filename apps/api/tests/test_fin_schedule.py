# -*- coding: utf-8 -*-
"""六时点展示副本与 fin-worker 权威清单的一致性用例（M6）· 不连库、不联网。

    cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_schedule.py -q

**为什么要有这个测试**：时刻表的权威在 `apps/fin-worker/app/points.py`（真正跑它的
是那边的 Temporal Schedule），api 侧那份只是给前端渲染用的副本。两处漂了，页面上
写着 14:55 而实际 14:50 跑 —— 这种错误**不会报错**，只会让用户对着错误的时刻表排查。
所以这里直接读那个文件的源码做**文本级比对**（不 import，两个应用的依赖不同）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from app.services.fin import schedule  # noqa: E402

# apps/api/tests/ → apps/ → 仓库根 → apps/fin-worker/app/points.py
_POINTS_PY = Path(__file__).resolve().parents[2] / "fin-worker" / "app" / "points.py"


def _load_points_module():
    """把 fin-worker 的 `points.py` 当**独立模块**加载（不 import 它所在的包）。

    N4 起 points.py **零外部依赖**（只 `from dataclasses import dataclass`），所以
    可以安全加载 —— 比按正则抠源码稳得多（N4 改结构时正则当场失效过一次）。
    """
    assert _POINTS_PY.exists(), f"读不到权威清单：{_POINTS_PY}"
    spec = importlib.util.spec_from_file_location("fin_worker_points_under_test", _POINTS_PY)
    mod = importlib.util.module_from_spec(spec)
    # 必须先注册进 sys.modules：模块里有 `@dataclass`，dataclasses 会按
    # `cls.__module__` 反查 `sys.modules` —— 不注册就 NoneType 报错。
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _authoritative() -> list[dict]:
    """权威清单里的 **CN_A 六个时点**（本副本只讲 A 股；多市场切换器是 N5 的事）。"""
    mod = _load_points_module()
    rows = [
        {"key": p.key, "at": p.at, "kind": p.kind, "cron": p.cron, "title": p.title}
        for p in mod.ALL_POINTS if p.market == "CN_A"
    ]
    assert rows, "没有从 points.py 里取到 CN_A 时点 —— 文件结构变了，先修测试"
    return rows


def test_same_length():
    assert len(schedule.POINTS) == len(_authoritative())


def test_each_row_matches():
    mine = list(schedule.POINTS)
    theirs = _authoritative()
    for a, b in zip(mine, theirs):
        assert a["key"] == b["key"], f"时点顺序漂了：{a['key']} vs {b['key']}"
        assert a["at"] == b["at"], f"{a['key']} 时刻漂了：{a['at']} vs {b['at']}"
        assert a["kind"] == b["kind"], f"{a['key']} 类型漂了"
        assert a["cron"] == b["cron"], f"{a['key']} cron 漂了：{a['cron']} vs {b['cron']}"
        assert a["title"] == b["title"], f"{a['key']} 标题漂了"


def test_as_list_shape_for_frontend():
    rows = schedule.as_list()
    assert len(rows) == 6
    for r in rows:
        assert set(r) == {"key", "at", "kind", "title", "cron", "plain"}
        assert r["plain"], "每个时点都要有一句人话说明，否则前端会显示空行"


def test_cron_is_weekdays_only():
    """节假日不在 cron 里表达（由工作流读交易日历挡掉）—— 这句话在 cron 上就是 `1-5`。"""
    for p in schedule.POINTS:
        assert p["cron"].endswith("1-5"), f"{p['key']} 的 cron 不是工作日：{p['cron']}"
