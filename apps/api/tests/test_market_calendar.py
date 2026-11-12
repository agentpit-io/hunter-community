# -*- coding: utf-8 -*-
"""港美股交易日历（`services/fin/market_calendar`）· 解析 / 交易日 / 对账 / 端点。

    cd apps/api && HUNTER_INTERNAL_KEY=testkey123 \
      PYTHONPATH=. python -m pytest tests/test_market_calendar.py -q

⚠️ 不要跑整个 `tests/` 目录（`test_screen_fix.py` 在 import 阶段 sys.exit）。
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("HUNTER_INTERNAL_KEY", "testkey123")

from app.routers import internal_etl  # noqa: E402
from app.services.fin import market_calendar as mcal  # noqa: E402

KEY = "testkey123"
H = {"X-Hunter-Internal-Key": KEY}


# ── 解析：HKEX 页内嵌 JSON ─────────────────────────────────────────────────
_HKEX_PAGE = """<!doctype html><html><script>
  var DataSource = '{"monthly":[
    {"name":"The first day of January","startdate":"2026-01-01","enddate":"2026-01-01","holidayIcon":"HongKongPublicHolidays"},
    {"name":"Good Friday","startdate":"2026-04-03","enddate":"2026-04-03","holidayIcon":"HongKongPublicHolidays"},
    {"name":"Half-Day Trading Day - Afternoon Session is Closed on New Year’s Eve","startdate":"2026-12-31","enddate":"2026-12-31","holidayIcon":"HKEX"},
    {"name":"Some Marketing Event","startdate":"2026-03-03","enddate":"2026-03-03","holidayIcon":"Activity"}
  ]}';
  var calendarDataSourceRanage = [new Date(2025, 9, 1, 0, 0, 0), new Date(2027, 9, 31, 23, 59, 59)];
</script></html>"""


def test_parse_hkex_extracts_holidays_and_range():
    holidays, half, rng = mcal.parse_hkex_calendar(_HKEX_PAGE)
    assert set(holidays) == {"2026-01-01", "2026-04-03"}          # 非 holidayIcon 的不进休市集合
    assert half == ["2026-12-31"]                                  # 半日市单独列（不是休市）
    assert rng == (date(2025, 10, 1), date(2027, 10, 31))


def test_parse_hkex_missing_data_raises():
    with pytest.raises(ValueError):
        mcal.parse_hkex_calendar("<html>no data here</html>")


# ── 解析：NYSE 表格 ────────────────────────────────────────────────────────
_NYSE_PAGE = """<div class="overflow-x-auto"><table class="table-data">
<thead><tr><th>Holiday</th><th>2026</th><th>2027</th></tr></thead>
<tbody>
<tr><th>New Year&rsquo;s Day</th><td>Thursday, January 1</td><td>Friday, January 1</td></tr>
<tr><th>Independence Day</th><td>Friday, July 3 (Independence Day observed)</td><td>Monday, July 5</td></tr>
<tr><th>Thanksgiving Day</th><td>Thursday, November 26***</td><td>Thursday, November 25</td></tr>
<tr><th>Christmas Day</th><td>Friday, December 25****</td><td>&mdash;*</td></tr>
</tbody></table></div>"""


def test_parse_nyse_handles_markers_and_observed():
    h = mcal.parse_nyse_calendar(_NYSE_PAGE, 2026)
    assert h["2026-01-01"] == "New Year’s Day"
    assert h["2026-07-03"] == "Independence Day"        # 去掉 (observed)
    assert h["2026-11-26"] == "Thanksgiving Day"        # 去掉脚注星号
    assert h["2026-12-25"] == "Christmas Day"
    assert list(h) == sorted(h)


def test_parse_nyse_missing_year_column_raises():
    with pytest.raises(ValueError):
        mcal.parse_nyse_calendar(_NYSE_PAGE, 2030)


def test_parse_nyse_em_dash_means_no_holiday():
    h = mcal.parse_nyse_calendar(_NYSE_PAGE, 2027)
    assert "2027-12-25" not in h                        # `—*` = 那年无此假日


# ── 交易日 = 工作日 − 休市日 ───────────────────────────────────────────────
def test_trading_days_excludes_weekend_and_holiday():
    days = mcal.trading_days(date(2026, 1, 1), date(2026, 1, 6), {"2026-01-01"})
    # 1/1 周四休市、1/2 周五交易、1/3-4 周末、1/5 周一交易
    assert days == ["2026-01-02", "2026-01-05", "2026-01-06"]


# ── 对账 ───────────────────────────────────────────────────────────────────
def test_reconcile_agrees():
    cal = ["2026-09-01", "2026-09-02", "2026-09-03"]
    bars = ["2026-09-01", "2026-09-02", "2026-09-03"]
    r = mcal.reconcile(date(2026, 9, 1), date(2026, 9, 3), cal, bars)
    assert r["agree"] == 3 and r["mismatches"] == []


def test_reconcile_reports_both_directions():
    cal = ["2026-09-01", "2026-09-02"]          # 日历说 09-02 开市
    bars = ["2026-09-01", "2026-09-03"]          # 行情 09-03 有数据
    r = mcal.reconcile(date(2026, 9, 1), date(2026, 9, 3), cal, bars)
    assert r["cal_open_no_bar"] == ["2026-09-02"]
    assert r["bar_open_cal_closed"] == ["2026-09-03"]
    assert r["mismatches"] == ["2026-09-02", "2026-09-03"]


# ── 兜底：官方接口失败 → 人工种子 ──────────────────────────────────────────
def test_market_holidays_falls_back_to_manual_seed(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(mcal, "load_official", boom)
    info = mcal.market_holidays("US")
    assert info["ok"] is False and info["source_label"] == "manual_seed"
    assert info["holidays"] == mcal.MANUAL_SEED["US"]["holidays"]
    assert "network down" in info["error"]


def test_manual_seed_has_source_and_date():
    for market in ("HK", "US"):
        seed = mcal.MANUAL_SEED[market]
        assert seed["source"].startswith("https://")
        assert seed["seeded_at"] and seed["holidays"]


# ── 端点：hk/us 不再 501 ───────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(internal_etl.router)
    return TestClient(app)


def test_endpoint_rejects_without_key(client):
    r = client.get("/internal/calendar/trading-days",
                   params={"market": "hk", "start": "2026-01-01", "end": "2026-01-10"})
    assert r.status_code == 401


def test_endpoint_hk_uses_market_calendar(client, monkeypatch):
    monkeypatch.setattr(mcal, "market_holidays", lambda market, **k: {
        "market": market, "holidays": {"2026-01-01": "New Year"},
        "half_days": [], "source": "https://www.hkex.com.hk/x",
        "source_label": "hkex_official", "fetched_at": "2026-10-03T00:00:00Z",
        "ok": True, "error": None})
    r = client.get("/internal/calendar/trading-days", headers=H,
                   params={"market": "hk", "start": "2026-01-01", "end": "2026-01-06"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["market"] == "hk" and body["calendar_source"] == "hkex_official"
    assert body["trading_days"][0] == "2026-01-02"       # 1/1 休市
    assert body["fallback_reason"] is None


def test_endpoint_reports_fallback_reason(client, monkeypatch):
    monkeypatch.setattr(mcal, "market_holidays", lambda market, **k: {
        "market": market, "holidays": {"2026-01-01": "New Year"}, "half_days": [],
        "source": "https://www.nyse.com/x", "source_label": "manual_seed",
        "fetched_at": "2026-10-03", "ok": False, "error": "timeout"})
    r = client.get("/internal/calendar/trading-days", headers=H,
                   params={"market": "us", "start": "2026-01-01", "end": "2026-01-06"})
    assert r.status_code == 200
    assert r.json()["calendar_source"] == "manual_seed"
    assert r.json()["fallback_reason"] == "timeout"
