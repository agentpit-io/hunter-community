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


# ── ⑦ 取数：`observe()` 从 `klines` 取基准日线（`R11` 换的数据源）──────────
#
# 这些用例只验**取数那一段**（SQL 打的代码对不对、结果怎么组装、失败怎么 fail-closed），
# 判定与四元组形状仍由上面 ①–⑥ 盯着。

class _FakeCursor:
    def __init__(self, rows, raises=None):
        self._rows = rows
        self._raises = raises
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, args=None):
        self.executed.append((sql, args))
        if self._raises is not None:
            raise self._raises

    def fetchall(self):
        return self._rows


class _FakeConn:
    """最小假连接：记录每次 execute 的 SQL / 参数，可模拟查库异常。"""

    def __init__(self, rows, raises=None):
        self._cur = _FakeCursor(rows, raises)

    def cursor(self, **k):
        return self._cur


def _klines_rows(closes, code_dates_from="2026-01-01"):
    from datetime import date, timedelta
    y, m, d = (int(x) for x in code_dates_from.split("-"))
    start = date(y, m, d)
    return [{"ts": (start + timedelta(days=i)).isoformat(), "close": c}
            for i, c in enumerate(closes)]


def test_detect_for_market_without_benchmark_is_unknown():
    """`klines` 里没有该基准 → `observe` 返回 None → `unknown`（不是编一个 regime）。"""
    out = R.detect_for_market(market="CN_A", conn=_FakeConn([]), as_of=AS_OF)
    assert out["label"] == "unknown" and out["source_snapshot_id"] is None


def test_observe_uses_configured_benchmark_code():
    """obs 查的是 `RULES[…]` 里的基准代码，不是别的。"""
    for market, code in (("CN_A", "000300"), ("HK", "HSI"), ("US", ".INX")):
        conn = _FakeConn(_klines_rows([100.0] * LEN))
        obs = R.observe(market=market, conn=conn)
        assert obs is not None and obs["code"] == code
        sql, args = conn._cur.executed[-1]
        assert "klines" in sql and "period = 'daily'" in sql
        assert args == (code,)
        assert len(obs["closes"]) == LEN and len(obs["dates"]) == LEN


def test_detect_for_market_reads_klines_benchmark():
    """有基准日线窗口时走真实取数路径判定；来源身份用确定性回退 `SNAP-BENCH-…`。"""
    closes = [100.0] * (LEN - 1) + [120.0]     # 站上均线 + 半年 +20% → bull
    out = R.detect_for_market(market="CN_A", conn=_FakeConn(_klines_rows(closes)), as_of=AS_OF)
    assert out["label"] == "bull"
    last_day = _klines_rows(closes)[-1]["ts"]
    assert out["source_snapshot_id"] == f"SNAP-BENCH-CN_A-000300-{last_day}"


def test_observe_too_few_rows_is_unknown():
    """行数不足 `min_bars` → 判定 `unknown`（窗口不够长，不拿短窗口冒充）。"""
    out = R.detect_for_market(market="CN_A", conn=_FakeConn(_klines_rows([100.0] * 3)), as_of=AS_OF)
    assert out["label"] == "unknown" and R.is_available(out) is False


def test_observe_db_error_is_unknown():
    """查库抛异常 = 没有行情 → None → `unknown`，不许抛、不许兜底一个值。"""
    conn = _FakeConn([], raises=RuntimeError("boom"))
    out = R.detect_for_market(market="CN_A", conn=conn, as_of=AS_OF)
    assert out["label"] == "unknown" and out["source_snapshot_id"] is None


def test_observe_conn_none_is_unknown():
    """没有连接就拿不到行情 → `unknown`（fail-closed）。"""
    assert R.observe(market="CN_A", conn=None) is None
    out = R.detect_for_market(market="CN_A", conn=None, as_of=AS_OF)
    assert out["label"] == "unknown"
