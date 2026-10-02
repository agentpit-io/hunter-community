# -*- coding: utf-8 -*-
"""风险档位与「单向棘轮」的纯函数用例（M6）· 不连库。

盯住三件会静默出错的事：
  · **收紧与放宽的判据是逐字段的**，不是档位序号 —— 只看序号会让某条刹车被
    悄悄松开而返回值仍说「已收紧」；
  · 现值缺失（None）不算放宽，但也**不谎称收紧**（direction=unknown_old）；
  · `tier_of` 只在参数**恰好等于**某档预设时才认档位 —— 认错了页面就会显示一个
    并不生效的档位名。

    cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_risk.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from app.services.fin import risk  # noqa: E402


def test_preset_unknown_raises():
    try:
        risk.preset("yolo")
    except ValueError as exc:
        assert "yolo" in str(exc)
        assert "conservative" in str(exc)      # 报错里要写清可选值
    else:
        raise AssertionError("未知档位必须报错，不能静默返回空预设")


def test_conservative_is_tighter_than_steady_from_steady_values():
    steady = risk.preset("steady")
    diff = risk.compare(steady, risk.preset("conservative"))
    assert diff["tightens"] is True
    assert diff["loosened"] == []
    # 单票占比 20% → 10% 是收紧；两条熔断线也都更接近 0
    for c in diff["changes"]:
        assert c["direction"] in ("tighter", "same")


def test_aggressive_from_steady_loosens_three_fields():
    diff = risk.compare(risk.preset("steady"), risk.preset("aggressive"))
    assert diff["tightens"] is False
    assert len(diff["loosened"]) == 3
    fields = {x["field"] for x in diff["loosened"]}
    assert fields == {"max_position_pct", "daily_loss_halt_pct", "account_drawdown_halt_pct"}


def test_single_field_loosening_blocks():
    """只要一条变松，整体就判「不是收紧」—— 哪怕另外两条在收紧。"""
    current = {"max_position_pct": 0.30, "daily_loss_halt_pct": -0.02,
               "account_drawdown_halt_pct": -0.20}
    diff = risk.compare(current, risk.preset("conservative"))
    # 单票 30%→10% 收紧、回撤 -20%→-5% 收紧，但日亏损 -2%→-2% 相同；全不松 → 允许
    assert diff["tightens"] is True

    current2 = dict(current, daily_loss_halt_pct=-0.01)   # 现值 -1% 比目标 -2% 更紧
    diff2 = risk.compare(current2, risk.preset("conservative"))
    # 目标 -2% 比现值 -1% 松 → 必须拦
    assert diff2["tightens"] is False
    assert [x["field"] for x in diff2["loosened"]] == ["daily_loss_halt_pct"]


def test_missing_current_value_is_not_loosening_but_not_claimed_tighter():
    diff = risk.compare({}, risk.preset("steady"))
    assert diff["tightens"] is True                  # 不拦（数据缺失不该让用户改不动）
    assert all(c["direction"] == "unknown_old" for c in diff["changes"])
    assert all(c["old"] is None for c in diff["changes"])


def test_tier_of_requires_exact_match():
    assert risk.tier_of(risk.preset("aggressive")) == "aggressive"
    assert risk.tier_of({**risk.preset("aggressive"), "max_position_pct": 0.34}) is None


def test_compare_reports_labels_for_ui():
    diff = risk.compare({}, risk.preset("steady"))
    labels = {c["field"]: c["label"] for c in diff["changes"]}
    assert labels["max_position_pct"] == "单票最高占比"
    assert labels["daily_loss_halt_pct"] == "单日亏损熔断线"
