# -*- coding: utf-8 -*-
"""R6 · 提案层（`app/services/fin/evolution.py`）—— **纯函数**（不连库、不连网）。

盯住的是「服务端说了算」这件事里**能脱离数据库**的那一半：

  · 白名单（参数含 `strategies.<key>.params.<name>` 形态）与类型 / 范围 / 单次最大变化；
  · 配置规范哈希（`0.10 == 0.1`、整数与浮点同形）与 `param_diff` 的机器计算；
  · 方向判定（risk 走 `risk.compare()` 的「负数是更紧」；strategy 走白名单方向语义）；
  · `target` 服务端推导（混类拒绝）；
  · regime 混组规则 + `is_available` 闸门（unknown 不得与明确 regime 同组）；
  · 计划冻结哈希（改一个字段哈希就变；未知覆盖键拒绝）。

「证据来自冻结快照 / 跨项目 / holdout / refute 可引用」「红线 8 不入库」「唯一索引」
「哈希链 / 触发器」要在真库上测 —— 见 `tests/test_fin_evolution_router.py`。

    cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_evolution.py -q
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_API_ROOT = Path(__file__).resolve().parents[1]
for _p in ("", "/", str(_API_ROOT)):
    while _p in sys.path:
        sys.path.remove(_p)
sys.path.insert(0, str(_API_ROOT))
_bad_app = sys.modules.get("app")
if _bad_app is not None and Path(getattr(_bad_app, "__file__", "") or "").parent == _API_ROOT:
    del sys.modules["app"]

from app.services.fin import evolution as E  # noqa: E402


# ── 白名单 ──────────────────────────────────────────────────────────────

def test_whitelist_lookup_shapes():
    assert E.whitelist_for("max_position_pct")[0] == "risk"
    assert E.whitelist_for("stop_loss_pct")[0] == "strategy"
    # 嵌套形态取最后一段当参数名
    t, name, entry = E.whitelist_for("strategies.volume_breakout.params.vol_mult")
    assert (t, name) == ("strategy", "vol_mult")
    assert E.whitelist_for("fast") is None          # 未收录的参数
    assert E.whitelist_for("") is None
    assert E.whitelist_for("strategies.x.params.fast") is None


def test_whitelist_entries_have_all_required_fields():
    for name, entry in {**E._RISK_FIELDS, **E._STRATEGY_FIELDS}.items():
        for key in ("type", "min", "max", "max_step", "tighter_when", "baseline_version"):
            assert key in entry, f"{name} 缺 {key}"
        assert entry["tighter_when"] in ("smaller", "larger")
        assert entry["type"] in ("ratio", "int")
        assert entry["min"] <= entry["max"]


def test_public_whitelist_is_serializable_and_versioned():
    wl = E.public_whitelist()
    assert wl["algo_version"] == E.ALGO_VERSION
    assert "max_position_pct" in wl["risk"] and "vol_mult" in wl["strategy"]


# ── 规范哈希 ────────────────────────────────────────────────────────────

def test_canon_num_forms_are_equal():
    assert E._canon_num(0.10) == E._canon_num(0.1) == E._canon_num("0.100")
    assert E._canon_num(5) == E._canon_num(5.0) == E._canon_num("5.000")
    assert E._canon_num(-0.02) == "-0.02"


def test_hash_config_is_deterministic_and_key_order_free():
    a = E.hash_config({"max_position_pct": 0.2, "stop_loss_pct": -0.08})
    b = E.hash_config({"stop_loss_pct": -0.08, "max_position_pct": 0.20})
    assert a == b
    assert E.hash_config({"max_position_pct": 0.2}) != a


def test_config_diff_only_lists_changed_fields_sorted():
    base = {"max_position_pct": 0.20, "stop_loss_pct": -0.08, "hold_days_max": 5}
    cand = {"max_position_pct": 0.20, "stop_loss_pct": -0.05, "hold_days_max": 5}
    assert E.config_diff(base, cand) == [
        {"field": "stop_loss_pct", "old": -0.08, "new": -0.05}]
    assert E.config_diff(base, base) == []


# ── 候选配置校验 ────────────────────────────────────────────────────────

def test_normalize_candidate_rejects_non_whitelisted_param():
    with pytest.raises(E.EvolutionGateError) as ei:
        E.normalize_candidate({"max_position_pct": 0.2, "fast": 5})
    assert "white" not in str(ei.value).lower() and "白名单" in str(ei.value)
    assert "fast" in str(ei.value)


def test_normalize_candidate_type_and_range():
    with pytest.raises(E.EvolutionGateError):
        E.normalize_candidate({"max_position_pct": "0.2"})       # 字符串
    with pytest.raises(E.EvolutionGateError):
        E.normalize_candidate({"max_position_pct": True})        # 布尔
    with pytest.raises(E.EvolutionGateError):
        E.normalize_candidate({"max_position_pct": 1.5})         # 超范围
    with pytest.raises(E.EvolutionGateError):
        E.normalize_candidate({"hold_days_max": 5.5})            # int 字段给小数
    assert E.normalize_candidate({"max_position_pct": 0.15}) == {"max_position_pct": 0.15}


def test_check_step_enforces_max_change():
    entry = E._RISK_FIELDS["max_position_pct"]                      # max_step 0.10
    E._check_step("max_position_pct", entry, 0.20, 0.10)            # 恰好 0.10 → 放行
    with pytest.raises(E.EvolutionGateError) as ei:
        E._check_step("max_position_pct", entry, 0.20, 0.35)        # 0.15 > 0.10
    assert "单次变化" in str(ei.value)


# ── param_diff 形状 ─────────────────────────────────────────────────────

def test_norm_diff_map_rejects_bad_shapes():
    with pytest.raises(E.EvolutionGateError):
        E._norm_diff_map([{"field": "x", "old": 1}])                # 缺 new
    with pytest.raises(E.EvolutionGateError):
        E._norm_diff_map([{"field": "x", "old": 1, "new": 2},
                          {"field": "x", "old": 1, "new": 3}])      # 重复字段
    with pytest.raises(E.EvolutionGateError):
        E._norm_diff_map([{"field": "x", "old": "a", "new": 2}])    # 非数字
    assert E._norm_diff_map([{"field": "x", "old": 0.10, "new": 0.1}]) == {"x": ("0.1", "0.1")}


# ── 方向判定 ────────────────────────────────────────────────────────────

def test_direction_strategy_uses_whitelist_semantics():
    # `stop_loss_pct` 是**负数**口径（与 fin_param 存量数据一致）：
    # -0.08 → -0.05 = 止损线向 0 靠近 = 更早离场 = **更紧**。
    base = {"stop_loss_pct": -0.08, "vol_mult": 2.0}
    assert E.compute_direction("strategy", base, {"stop_loss_pct": -0.05, "vol_mult": 2.0},
                               ["stop_loss_pct"]) == "tighten"
    # 量能门槛变大 = 更紧
    assert E.compute_direction("strategy", base, {"stop_loss_pct": -0.08, "vol_mult": 3.0},
                               ["vol_mult"]) == "tighten"
    # -0.08 → -0.10 = 止损线离 0 更远 = 更晚离场 = 放松
    assert E.compute_direction("strategy", base, {"stop_loss_pct": -0.10, "vol_mult": 2.0},
                               ["stop_loss_pct"]) == "loosen"


def test_stop_loss_pct_uses_fin_param_sign_convention():
    """跨阶段缺陷回归（R10）：档位模板（`tiers.py`）写进 `fin_param.stop_loss_pct` 的是**负数**
    （`-0.04` / `-0.03`），白名单必须接受它 —— 否则**任何向导开出来的真实项目都提不出提案**。"""
    assert E.normalize_candidate({"stop_loss_pct": -0.04}) == {"stop_loss_pct": -0.04}
    assert E.normalize_candidate({"stop_loss_pct": -0.03}) == {"stop_loss_pct": -0.03}
    # 正号（R6 初版口径）与越界值仍被拒
    with pytest.raises(E.EvolutionGateError):
        E.normalize_candidate({"stop_loss_pct": 0.04})
    with pytest.raises(E.EvolutionGateError):
        E.normalize_candidate({"stop_loss_pct": -0.001})            # 太接近 0
    with pytest.raises(E.EvolutionGateError):
        E.normalize_candidate({"stop_loss_pct": -0.8})              # 超出下界


def test_direction_risk_uses_risk_compare_negative_is_tighter():
    # 单票占比变小 = 更紧
    assert E.compute_direction("risk", {"max_position_pct": 0.20},
                               {"max_position_pct": 0.15}, ["max_position_pct"]) == "tighten"
    # 单票占比变大 = 放宽
    assert E.compute_direction("risk", {"max_position_pct": 0.20},
                               {"max_position_pct": 0.30}, ["max_position_pct"]) == "loosen"
    # 熔断线 -3% → -2%：**越接近 0 越紧**（负数是更紧，别写反）
    assert E.compute_direction("risk", {"daily_loss_halt_pct": -0.03},
                               {"daily_loss_halt_pct": -0.02}, ["daily_loss_halt_pct"]) == "tighten"
    # 熔断线 -2% → -3%：放宽
    assert E.compute_direction("risk", {"daily_loss_halt_pct": -0.02},
                               {"daily_loss_halt_pct": -0.03}, ["daily_loss_halt_pct"]) == "loosen"


def test_derive_target_rejects_mixed():
    assert E.derive_target(["max_position_pct"]) == "risk"
    assert E.derive_target(["stop_loss_pct", "vol_mult"]) == "strategy"
    with pytest.raises(E.EvolutionGateError):
        E.derive_target(["max_position_pct", "stop_loss_pct"])       # 一次只改一类


# ── regime 混组 + 可用性 ────────────────────────────────────────────────

def test_normalize_regime_tags():
    assert E.normalize_regime_tags([" bull ", "bull", "range"]) == ["bull", "range"]
    with pytest.raises(E.EvolutionGateError):
        E.normalize_regime_tags(None)
    with pytest.raises(E.EvolutionGateError):
        E.normalize_regime_tags([])


def test_check_regime_blocks_unknown_label():
    with pytest.raises(E.EvolutionGateError) as ei:
        E.check_regime(["unknown"], set())
    assert "停止" in str(ei.value) or "不可用" in str(ei.value)
    # 明确 regime 通过
    E.check_regime(["bull"], {"bull"})
    E.check_regime(["bull"], set())                                  # 证据无标签也允许


def test_check_regime_blocks_mixing_and_scope():
    with pytest.raises(E.EvolutionGateError):                        # 提案侧不许混 unknown
        E.check_regime(["unknown", "bull"], set())
    with pytest.raises(E.EvolutionGateError):                        # 证据侧不许混
        E.check_regime(["bull"], {"unknown", "bear"})
    with pytest.raises(E.EvolutionGateError):                        # 证据超出提案范围
        E.check_regime(["bull"], {"bear"})


# ── 计划冻结 ────────────────────────────────────────────────────────────

def test_build_plan_rejects_unknown_override_key():
    assert E.build_plan({"window_days": 30})["window_days"] == 30
    with pytest.raises(E.EvolutionValidationError):
        E.build_plan({"pass_line_should_not_exist": 1})


def test_plan_hash_changes_when_any_field_changes():
    p = E.build_plan()
    h = E.plan_hash(p)
    assert E.plan_hash(E.build_plan()) == h                          # 默认值稳定
    p2 = dict(p)
    p2["window_days"] = p["window_days"] + 1
    assert E.plan_hash(p2) != h


# ── 事件哈希 ────────────────────────────────────────────────────────────

def test_event_hash_is_deterministic_and_payload_sensitive():
    t = datetime(2026, 10, 3, 9, 0, 0, tzinfo=timezone.utc)
    h1 = E.event_hash("", "evp_1", "created", {"a": 1}, t)
    assert h1 == E.event_hash("", "evp_1", "created", {"a": 1}, t)
    assert h1 != E.event_hash("", "evp_1", "created", {"a": 2}, t)     # payload 变 → 哈希变
    assert h1 != E.event_hash("x", "evp_1", "created", {"a": 1}, t)   # prev 变 → 哈希变
    # 同一个时刻用不同时区表达（同一瞬间）→ 哈希相同
    t_utc8 = datetime(2026, 10, 3, 17, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    h_utc = E.event_hash("", "evp_1", "created", {"a": 1},
                         datetime(2026, 10, 3, 9, 0, 0, tzinfo=timezone.utc))
    assert E.event_hash("", "evp_1", "created", {"a": 1}, t_utc8) == h_utc


def test_status_of_kind_covers_all_event_kinds():
    for kind in E.EVENT_KINDS:
        assert kind in E.STATUS_OF_KIND
