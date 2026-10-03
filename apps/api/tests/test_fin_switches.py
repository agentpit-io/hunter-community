# -*- coding: utf-8 -*-
"""R4 · 运行开关与安全边界（`services/fin/switches.py`）。

两类用例：

  · **纯逻辑**（不连库、不连网）：四个开关的默认值 / 解析 / 硬开关拒绝 / 降级判定，
    以及 `memory.query` / `memory.append_evidence` 在 `FIN_MEMORY_ENABLED=0` 下的行为；
  · **真库**（`TEST_DATABASE_URL` 未设时 skip）：五个 `paper` 依赖探针的形状与真实结果。

    cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_switches.py -q

⚠️ `conftest.py` 把 `FIN_MEMORY_ENABLED` 默认成 1（测试套件测的是开着的经验库）。
本文件要验「关着」的行为，所以**每个用例显式**设/清这个变量，不依赖外部环境。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))

from app.services.fin import memory as memory_svc  # noqa: E402
from app.services.fin import switches  # noqa: E402

_ALL_SWITCH_ENVS = (
    switches.MEMORY_ENABLED_ENV,
    switches.EVOLUTION_MODE_ENV,
    switches.AUTO_APPLY_ENV,
    switches.LIVE_ORDER_ENV,
)


@pytest.fixture(autouse=True)
def _clean_switch_env(monkeypatch):
    """每个用例从「四个开关都没设」出发 —— 谁要什么自己 monkeypatch。"""
    for name in _ALL_SWITCH_ENVS:
        monkeypatch.delenv(name, raising=False)


# ════════════════════════════════════════════════════════════════════════
# 一 · 默认值：全部取安全的一侧
# ════════════════════════════════════════════════════════════════════════

def test_defaults_are_fail_safe():
    assert switches.memory_enabled() is False, "代码默认必须是 0（未显式开 = 关）"
    assert switches.evolution_mode_requested() == "off"
    assert switches.auto_apply() is False
    assert switches.live_order_enabled() is False
    assert switches.hard_config_errors() == []


@pytest.mark.parametrize("raw,expected", [
    ("1", True),
    ("0", False),
    ("", False),          # 空串（compose 的 ${X:-}）→ 回落默认 0
    ("true", False),      # 非法值 → fail-safe 关
    ("2", False),
    ("yes", False),
])
def test_memory_enabled_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv(switches.MEMORY_ENABLED_ENV, raw)
    assert switches.memory_enabled() is expected


@pytest.mark.parametrize("raw,expected", [
    ("off", "off"),
    ("observe", "observe"),
    ("paper", "paper"),
    ("PAPER", "paper"),   # 大小写不敏感
    ("", "off"),          # 空 → 默认 off
    ("live", "off"),      # 非法 → fail-safe off
    ("auto", "off"),
])
def test_evolution_mode_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv(switches.EVOLUTION_MODE_ENV, raw)
    assert switches.evolution_mode_requested() == expected


# ════════════════════════════════════════════════════════════════════════
# 二 · 硬开关：只允许 0，非 0 一律拒绝
# ════════════════════════════════════════════════════════════════════════

def test_auto_apply_zero_is_ok():
    assert switches.hard_config_errors() == []
    switches.assert_hard_ok()          # 不抛


@pytest.mark.parametrize("raw", ["1", "true", "yes", "2"])
def test_auto_apply_nonzero_rejected(monkeypatch, raw):
    monkeypatch.setenv(switches.AUTO_APPLY_ENV, raw)
    errors = switches.hard_config_errors()
    assert errors and "本方案恒为 0，自动生效未交付" in errors[0]
    with pytest.raises(switches.SwitchConfigError) as exc:
        switches.assert_hard_ok()
    assert switches.AUTO_APPLY_ENV in str(exc.value)


@pytest.mark.parametrize("raw", ["1", "true", "yes"])
def test_live_order_nonzero_rejected(monkeypatch, raw):
    monkeypatch.setenv(switches.LIVE_ORDER_ENV, raw)
    errors = switches.hard_config_errors()
    assert errors and "没有实盘订单出口" in errors[0]
    with pytest.raises(switches.SwitchConfigError):
        switches.assert_hard_ok()


def test_both_hard_switches_listed(monkeypatch):
    monkeypatch.setenv(switches.AUTO_APPLY_ENV, "1")
    monkeypatch.setenv(switches.LIVE_ORDER_ENV, "1")
    errors = switches.hard_config_errors()
    assert len(errors) == 2
    joined = "；".join(errors)
    assert "本方案恒为 0" in joined and "没有实盘订单出口" in joined


# ════════════════════════════════════════════════════════════════════════
# 三 · FIN_MEMORY_ENABLED=0 时，两个工具在**服务层**的行为
# ════════════════════════════════════════════════════════════════════════

def test_query_returns_empty_when_disabled(monkeypatch):
    monkeypatch.setenv(switches.MEMORY_ENABLED_ENV, "0")
    # 不连库：关掉时连库那一步根本走不到（这正是我们要的 —— 关就是关）
    out = memory_svc.query(project_id="prj_anything", caller="internal")
    assert out["items"] == []
    assert out["memory_snapshot_id"] is None
    assert out["memory_disabled"] is True


def test_append_rejected_when_disabled(monkeypatch):
    monkeypatch.setenv(switches.MEMORY_ENABLED_ENV, "0")
    with pytest.raises(memory_svc.MemoryDisabledError) as exc:
        memory_svc.append_evidence(
            caller="internal", project_id="prj_anything", kind="fact",
            statement="关着的时候写不进去", evidence=[{"evidence_kind": "external",
                                                        "ref_id": "x"}])
    assert "FIN_MEMORY_ENABLED=0" in str(exc.value)


def test_gate_is_not_taken_when_enabled(monkeypatch):
    """开着时**不**走关掉的短路 —— 用一个必然非关的理由（未知通道）证明它进了正常逻辑。"""
    monkeypatch.setenv(switches.MEMORY_ENABLED_ENV, "1")
    with pytest.raises(memory_svc.MemoryValidationError):
        memory_svc.append_evidence(
            caller="bogus", project_id="prj_anything", kind="fact",
            statement="x", evidence=[])


def test_disabled_error_is_not_a_validation_error():
    """503（能力没开）与 400（请求不合法）必须是两个类型，路由才分得开。"""
    assert not issubclass(memory_svc.MemoryDisabledError, memory_svc.MemoryValidationError)
    assert not issubclass(memory_svc.MemoryDisabledError, ValueError)


# ════════════════════════════════════════════════════════════════════════
# 四 · 降级判定（探针打桩，不连库不连网）
# ════════════════════════════════════════════════════════════════════════

def test_runtime_state_off_skips_probes(monkeypatch):
    monkeypatch.setenv(switches.EVOLUTION_MODE_ENV, "off")
    called = []
    monkeypatch.setattr(switches, "paper_dependency_failures",
                        lambda **kw: called.append(1) or [])
    state = switches.runtime_state()
    assert state["evolution_mode"] == "off"
    assert state["evolution_mode_requested"] == "off"
    assert state["degraded_reason"] is None
    assert called == [], "请求的不是 paper 就不该跑探针"


def test_runtime_state_paper_all_ok(monkeypatch):
    monkeypatch.setenv(switches.EVOLUTION_MODE_ENV, "paper")
    monkeypatch.setattr(switches, "paper_dependency_failures", lambda **kw: [])
    state = switches.runtime_state()
    assert state["evolution_mode"] == "paper"
    assert state["evolution_mode_requested"] == "paper"
    assert state["degraded_reason"] is None


def test_runtime_state_paper_degrades_to_observe(monkeypatch):
    monkeypatch.setenv(switches.EVOLUTION_MODE_ENV, "paper")
    monkeypatch.setattr(switches, "paper_dependency_failures", lambda **kw: [
        {"key": "paper_ledger", "name": "模拟账本", "ok": False, "detail": "不可达"},
        {"key": "market_calendar", "name": "市场日历", "ok": False, "detail": "没有交易日"},
    ])
    state = switches.runtime_state()
    assert state["evolution_mode"] == "observe"
    assert state["evolution_mode_requested"] == "paper"
    assert state["degraded_reason"]
    assert "模拟账本未就绪" in state["degraded_reason"]
    assert "市场日历未就绪" in state["degraded_reason"]


def test_runtime_state_shape():
    state = switches.runtime_state()
    assert set(state) == {
        "memory_enabled", "evolution_mode", "evolution_mode_requested",
        "degraded_reason", "auto_apply", "live_order_enabled",
    }


# ════════════════════════════════════════════════════════════════════════
# 五 · 真库：五个依赖探针
# ════════════════════════════════════════════════════════════════════════

_NEED_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="需要 TEST_DATABASE_URL 指向一个跑过 0041 迁移的 postgres",
)


@_NEED_DB
def test_paper_dependency_checks_shape():
    checks = switches.paper_dependency_checks()
    assert [c["key"] for c in checks] == [k for k, _ in switches.PAPER_DEPENDENCIES]
    for c in checks:
        assert set(c) == {"key", "name", "ok", "detail"}
        assert isinstance(c["ok"], bool)
        if c["ok"]:
            assert c["detail"] == ""
        else:
            assert c["detail"]


@_NEED_DB
def test_paper_dependency_failures_is_subset():
    checks = switches.paper_dependency_checks()
    failures = switches.paper_dependency_failures()
    assert {c["key"] for c in failures} <= {c["key"] for c in checks}
    assert all(not c["ok"] for c in failures)
