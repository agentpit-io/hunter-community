# -*- coding: utf-8 -*-
"""R5 · regime 判定器（`app/services/fin/regime.py`）—— **不连库、不连网**。

盯住四条不许退让的规则（`plan/R5.md` §一.2）：
① 阈值随版本固定（未登记的版本**抛**，不静默降级）；② 行情缺失 ⇒ `unknown`；
③ `unknown` 是独立取值（不与明确 regime 同组）；④ 「不可用」有唯一判据 `is_available`。
外加**固定四元组**的形状与**确定性**（同一输入两次判定一致）。

    cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_regime.py -q
"""
from __future__ import annotations

import sys
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

from app.services.fin import regime as R  # noqa: E402

AS_OF = "2026-10-03T16:30:00+08:00"
# 一个正好达到 min_bars（121）的窗口。
LEN = R.RULES[R.RULE_VERSION]["min_bars"]


def _obs(closes, code="000300", dates=None):
    return {"code": code, "closes": list(closes),
            "dates": dates or [f"2026-{(i % 12) + 1:02d}-01" for i in range(len(closes))]}


def _flat_then(last):
    return _obs([100.0] * (LEN - 1) + [last])


# ── ① 固定四元组的形状 ────────────────────────────────────────────────────

def test_output_is_exactly_four_keys():
    out = R.detect(market="CN_A", observations=_flat_then(120.0), as_of=AS_OF)
    assert set(out.keys()) == {"label", "rule_version", "source_snapshot_id", "as_of"}
    assert out["rule_version"] == R.RULE_VERSION
    assert out["as_of"] == AS_OF


# ── ② 闭集分类 ────────────────────────────────────────────────────────────

def test_bull_bear_range():
    assert R.classify(_flat_then(120.0)) == "bull"      # 站上均线 + 半年 +20%
    assert R.classify(_flat_then(80.0)) == "bear"       # 跌破均线 + 半年 -20%
    assert R.classify(_flat_then(100.0)) == "range"     # 平：没有方向
    assert R.classify(_flat_then(103.0)) == "range"     # 站上均线但涨幅不足阈值


def test_labels_are_closed_set():
    assert R.UNKNOWN not in R.LABELS                   # unknown 与明确 regime 分开
    assert set(R.LABELS) == {"bull", "bear", "range"}
    assert set(R.ALL_LABELS) == {"bull", "bear", "range", "unknown"}


# ── ③ 行情缺失 ⇒ unknown（规则 2，不许兜底）─────────────────────────────

@pytest.mark.parametrize("observations", [
    None,
    {},
    {"closes": []},
    {"closes": [100.0] * (LEN - 1)},                        # 差一根
    {"closes": [100.0, None] + [100.0] * (LEN - 2)},        # 有坏格子
    {"closes": [100.0, 0.0] + [100.0] * (LEN - 2)},         # 价格为 0
    {"closes": [100.0, -1.0] + [100.0] * (LEN - 2)},        # 负价
    {"closes": "not-a-list"},
])
def test_missing_or_bad_market_data_is_unknown(observations):
    out = R.detect(market="CN_A", observations=observations, as_of=AS_OF)
    assert out["label"] == "unknown"
    assert R.is_available(out) is False


def test_no_market_is_unknown():
    out = R.detect(market=None, observations=_flat_then(120.0), as_of=AS_OF)
    assert out["label"] == "unknown" and R.is_available(out) is False


# ── ④ 版本 / 市场写错 ⇒ 抛（不许伪装成 unknown）─────────────────────────

def test_unknown_rule_version_raises():
    with pytest.raises(ValueError):
        R.detect(market="CN_A", observations=_flat_then(120.0), rule_version="regime-vX")
    with pytest.raises(ValueError):
        R.classify(_flat_then(120.0), rule_version="regime-vX")


def test_unknown_market_raises():
    with pytest.raises(ValueError):
        R.detect(market="NASDAQ", observations=_flat_then(120.0))


# ── ⑤ 确定性 / 来源身份 ──────────────────────────────────────────────────

def test_same_input_is_deterministic():
    obs = _obs([100.0] * (LEN - 1) + [120.0], code="000300",
               dates=["x"] * (LEN - 1) + ["2026-09-30"])
    a = R.detect(market="CN_A", observations=obs, as_of=AS_OF)
    b = R.detect(market="CN_A", observations=obs, as_of=AS_OF)
    assert a["label"] == b["label"] and a["rule_version"] == b["rule_version"]
    # 同一份窗口 → 同一个来源身份（不依赖调用时刻）
    assert a["source_snapshot_id"] == b["source_snapshot_id"] == "SNAP-BENCH-CN_A-000300-2026-09-30"


def test_explicit_snapshot_id_wins():
    out = R.detect(market="CN_A", observations=_flat_then(120.0),
                   source_snapshot_id="SNAP-20261003-093000-000300", as_of=AS_OF)
    assert out["source_snapshot_id"] == "SNAP-20261003-093000-000300"


def test_source_snapshot_id_is_null_without_window():
    assert R.detect(market="CN_A", observations=None, as_of=AS_OF)["source_snapshot_id"] is None


# ── ⑥ 标签 / 可用性 ──────────────────────────────────────────────────────

def test_tags_for_and_is_available():
    assert R.tags_for({"label": "bull"}) == ["bull"]
    assert R.tags_for({"label": "unknown"}) == ["unknown"]
    assert R.tags_for(None) == ["unknown"]
    assert R.is_available({"label": "bull"}) is True
    assert R.is_available({"label": "unknown"}) is False


# ── ⑦ 取数：本部署没有基准快照 ⇒ unknown（集成入口）─────────────────────

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        pass

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self, **k):
        return _FakeCursor(self._rows)


def test_detect_for_market_without_benchmark_is_unknown():
    """库里没有基准快照 → `observe` 返回 None → `unknown`（不是编一个 regime）。"""
    out = R.detect_for_market(market="CN_A", conn=_FakeConn([]), as_of=AS_OF)
    assert out["label"] == "unknown" and out["source_snapshot_id"] is None


def test_detect_for_market_reads_benchmark_series():
    """有基准快照序列时，走真实取数路径判定。"""
    rows = [{"snapshot_id": f"SNAP-{i}", "last_price": 100.0,
             "day": f"2026-01-{i:02d}"} for i in range(1, LEN)]
    rows.append({"snapshot_id": "SNAP-last", "last_price": 120.0, "day": "2026-09-30"})
    out = R.detect_for_market(market="CN_A", conn=_FakeConn(rows), as_of=AS_OF)
    assert out["label"] == "bull"
    assert out["source_snapshot_id"] == "SNAP-last"
