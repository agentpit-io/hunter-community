# -*- coding: utf-8 -*-
"""R11 · **基准指数日线取数**（`app/services/data/klines_etl.py`）—— 不连库、不连网。

盯住四件事：
① 基准映射是**显式**的（`cn→sh000300` / `hk→hkHSI` / `us→us.INX`），不靠前缀猜；
② 落库代码与 `regime.RULES[…]["benchmarks"]` **逐字一致**（两处漂移 = 判定取不到数）；
③ 解析沿用 `[date, open, close, high, low, volume]` 顺序（close 在 high 前，模块头坑 1）；
④ `run_market` **即使股票池为空也要先取基准**（演示站 / 新库的池子常常是空的）。

    cd apps/api && PYTHONPATH=. python -m pytest tests/test_klines_etl_benchmark.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[1]
for _p in ("", "/", str(_API_ROOT)):
    while _p in sys.path:
        sys.path.remove(_p)
sys.path.insert(0, str(_API_ROOT))
_bad_app = sys.modules.get("app")
if _bad_app is not None and Path(getattr(_bad_app, "__file__", "") or "").parent == _API_ROOT:
    del sys.modules["app"]

from app.services.data import klines_etl as K  # noqa: E402
from app.services.fin import regime as R       # noqa: E402


# ── ① 显式映射 ────────────────────────────────────────────────────────────

def test_benchmarks_are_explicit():
    assert K.BENCHMARKS == {
        "cn": ("000300", "sh000300"),
        "hk": ("HSI", "hkHSI"),
        "us": (".INX", "us.INX"),
    }
    assert K.benchmark_of("cn") == ("000300", "sh000300")
    assert K.benchmark_of("HK") == ("HSI", "hkHSI")       # 大小写不敏感
    assert K.benchmark_of("jp") is None


def test_benchmark_codes_match_regime_rules():
    """落库代码 == regime 判定器去取的代码（漂了这条当场红）。"""
    bench = R.RULES[R.RULE_VERSION]["benchmarks"]        # {CN_A: 000300, HK: HSI, US: .INX}
    got = {K.benchmark_of("cn")[0]: bench["CN_A"],
           K.benchmark_of("hk")[0]: bench["HK"],
           K.benchmark_of("us")[0]: bench["US"]}
    assert got == {"000300": "000300", "HSI": "HSI", ".INX": ".INX"}


def test_prefix_guess_would_botch_the_index():
    """反证：靠 `_tencent_symbol` 前缀猜会把基准代码拼坏 —— 所以必须显式映射。"""
    # 股票那套 `.split(".")[0]` 把 `.INX` 的 `.` 之后全丢掉，代码整个没了
    assert K._tencent_symbol(".INX", "us") == "us"
    # 港股那套按股票补零，`HSI` 被补成 `00HSI`
    assert K._tencent_symbol("HSI", "hk") == "hk00HSI"
    # 显式映射给的才对
    assert K.BENCHMARKS["us"][1] == "us.INX"
    assert K.BENCHMARKS["hk"][1] == "hkHSI"


# ── ③ 解析字段顺序（close 在 high 前）────────────────────────────────────

def test_fetch_tencent_bars_field_order(monkeypatch):
    import requests

    class _Resp:
        def json(self):
            return {"data": {"sh000300": {"qfqday": [
                # [date, open, close, high, low, volume]
                ["2026-09-30", "1.0", "2.0", "3.0", "0.5", "100"],
            ]}}}

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    rows = K._fetch_tencent_bars("sh000300")
    assert len(rows) == 1
    assert rows[0]["ts"] == "2026-09-30"
    assert rows[0]["open"] == 1.0
    assert rows[0]["close"] == 2.0                 # ⚠ close 在 high 前
    assert rows[0]["high"] == 3.0
    assert rows[0]["low"] == 0.5
    assert rows[0]["volume"] == 100


def test_fetch_tencent_delegates_to_bars(monkeypatch):
    """`fetch_tencent` 与基准取数共用同一段解析。"""
    monkeypatch.setattr(K, "_fetch_tencent_bars", lambda sym, bars=K.MAX_BARS: [{"sym": sym}])
    assert K.fetch_tencent("600519", "cn") == [{"sym": "sh600519"}]
    assert K.fetch_tencent("00700", "hk") == [{"sym": "hk00700"}]
    assert K.fetch_tencent("not-a-code", "cn") == []      # 解析不出符号 → 空


# ── ④ run_benchmark 行为 ─────────────────────────────────────────────────

def test_run_benchmark_unknown_market_is_error():
    out = K.run_benchmark("jp")
    assert "error" in out and "ok" not in out


def test_run_benchmark_saves_under_explicit_code(monkeypatch):
    rows = [{"ts": "2026-09-30", "open": 1.0, "close": 2.0, "high": 3.0,
             "low": 0.5, "volume": 100, "source": "tencent"}]
    monkeypatch.setattr(K, "_fetch_tencent_bars", lambda sym, bars=K.MAX_BARS: rows)
    saved = {}

    def _save(code, rws):
        saved["code"] = code
        return len(rws)

    monkeypatch.setattr(K, "save", _save)
    out = K.run_benchmark("cn")
    assert saved["code"] == "000300"
    assert out["code"] == "000300" and out["ok"] == 1
    assert out["first"] == out["last"] == "2026-09-30" and out["last_close"] == 2.0


def test_run_benchmark_no_data_reports_error(monkeypatch):
    monkeypatch.setattr(K, "_fetch_tencent_bars", lambda sym, bars=K.MAX_BARS: [])
    out = K.run_benchmark("us")
    assert out["ok"] == 0 and "error" in out        # 取不到就如实报，不编


def test_run_market_fetches_benchmark_even_with_empty_universe(monkeypatch):
    """股票池为空时也要先取基准 —— 否则空池的部署永远取不到（R11 的核心）。"""
    calls = []

    def _bench(market, bars=K.MAX_BARS):
        calls.append(market)
        return {"market": market, "code": "000300", "ok": 800}

    monkeypatch.setattr(K, "run_benchmark", _bench)
    monkeypatch.setattr(K, "universe", lambda *a, **k: [])       # 空池

    out = K.run_market("cn")
    assert calls == ["cn"]                                        # 基准确实取了
    assert out["benchmark"]["ok"] == 800
    assert "股票池是空的" in out["error"]


def test_run_market_benchmark_failure_does_not_block(monkeypatch):
    """基准取数抛异常也不能挡住股票池那一轮 —— 只记进返回体。"""
    def _boom(market, bars=K.MAX_BARS):
        raise RuntimeError("network down")

    monkeypatch.setattr(K, "run_benchmark", _boom)
    monkeypatch.setattr(K, "universe", lambda *a, **k: [])
    out = K.run_market("us")
    assert "error" in out["benchmark"] and "股票池是空的" in out["error"]
