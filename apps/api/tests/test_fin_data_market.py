"""N3 · 行情层的市场维度与快照质量（不联网）。

三件这一轮新加的东西各有一组用例：

1. **市场边界统一三值**（设计文档附 B）：`market_label` / `quote()["market"]`；
2. **报价通道按市场排序**（N0 报告 §一）：A 股 hunter 优先（一期口径不变）；
   **港美股腾讯优先** —— hunter 网关的港美股 `updated_at` **只到日期**、只能展示，
   不得用于成交（对齐 M8 那个坑）；
3. **`quote_quality`**（附 B）：有盘口 `full` / 只有最新价 `last_only`；
   以及港美股标的元数据的来源与「每手拿不到 → available=false」。

真正的数据源可用性由 N0 / N3 成果文档里的**真跑**记录承担，这里只测纯逻辑。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services import fin_data

CST = timezone(timedelta(hours=8))
_REPO_ROOT = Path(__file__).resolve().parents[3]
_HK_CSV = _REPO_ROOT / "data" / "hk_master.csv"


def _cst(h, m=0, s=0):
    return datetime(2026, 9, 30, h, m, s, tzinfo=CST)


def _hit(**kw):
    base = {"name": "测试", "last_price": "10.00", "prev_close": "9.80",
            "open": "9.90", "high": "10.10", "low": "9.70",
            "bid1_price": None, "bid1_volume": None, "ask1_price": None, "ask1_volume": None,
            "event_time": _cst(10), "source": "test"}
    base.update(kw)
    return base


# ── ① 市场三值 / 快照质量 ───────────────────────────────────────────────────

def test_market_label_is_canonical():
    assert fin_data.market_label("a") == "CN_A"
    assert fin_data.market_label("hk") == "HK"
    assert fin_data.market_label("us") == "US"


def test_quote_quality_mapping():
    """附 B：有盘口 = `full`，只有最新价 = `last_only`。"""
    assert fin_data.quote_quality_of(True) == "full"
    assert fin_data.quote_quality_of(False) == "last_only"
    assert fin_data.QUOTE_QUALITIES == ("full", "last_only", "no_book")


# ── ② 通道顺序按市场 ────────────────────────────────────────────────────────

def test_a_share_chain_is_official_first(monkeypatch):
    """**A 股不得回归**：官方链路仍然优先（一期口径）。"""
    calls: list[str] = []
    monkeypatch.setattr(fin_data, "_official_quote",
                        lambda c: calls.append("official") or _hit(name="贵州茅台"))
    monkeypatch.setattr(fin_data, "_tencent_quote",
                        lambda c: calls.append("tencent") or None)

    q = fin_data.quote("600519")
    assert calls == ["official"]
    assert q["market"] == "CN_A" and q["source"] == "test"


def test_intl_chain_is_tencent_first(monkeypatch):
    """港美股：腾讯优先 —— hunter 网关只到日期，不得用于成交（M8 的坑）。"""
    calls: list[str] = []
    monkeypatch.setattr(fin_data, "_tencent_quote",
                        lambda c: calls.append("tencent") or _hit(name="腾讯控股", source="tencent-qt"))
    monkeypatch.setattr(fin_data, "_official_quote",
                        lambda c: calls.append("official") or _hit(source="official"))

    q = fin_data.quote("00700.HK")
    assert calls == ["tencent"]                      # 腾讯在前，拿到就不再打网关
    assert q["market"] == "HK" and q["source"] == "tencent-qt"
    assert q["quote_quality"] == "last_only"
    assert q["event_time"].startswith("2026-09-30T10:00:00")


def test_intl_falls_back_to_official_with_gap(monkeypatch):
    """腾讯拿不到才回落网关，并把「腾讯无数据」如实记进 gaps。"""
    monkeypatch.setattr(fin_data, "_tencent_quote", lambda c: None)
    monkeypatch.setattr(fin_data, "_official_quote", lambda c: _hit(source="official"))

    q = fin_data.quote("AAPL.US")
    assert q["market"] == "US" and q["source"] == "official"
    assert any(g.startswith("tencent-qt:") for g in q["gaps"])


def test_intl_date_only_quote_is_flagged(monkeypatch):
    """港美股报价只到日期 → **如实记缺口**，不编一个盘中时刻（M8 那个坑）。"""
    monkeypatch.setattr(fin_data, "_tencent_quote",
                        lambda c: _hit(name="腾讯控股", event_time=_cst(0), source="tencent-qt"))

    q = fin_data.quote("00700.HK")
    assert q["event_time"].startswith("2026-09-30T00:00:00")   # 不改数据源的时刻
    assert any("腾讯通道也取不到盘中时刻" in g for g in q["gaps"])


def test_quote_quality_full_when_book_present(monkeypatch):
    monkeypatch.setattr(fin_data, "_official_quote",
                        lambda c: _hit(bid1_price="9.99", bid1_volume=100,
                                       ask1_price="10.01", ask1_volume=200))
    q = fin_data.quote("600519")
    assert q["orderbook"] is True and q["quote_quality"] == "full"


def test_intl_quote_records_orderbook_gap(monkeypatch):
    """港美股结构性没有盘口 → 记缺口（不静默）。"""
    monkeypatch.setattr(fin_data, "_tencent_quote", lambda c: _hit(source="tencent-qt"))
    q = fin_data.quote("00700.HK")
    assert any("港美股通道没有买一/卖一盘口" in g for g in q["gaps"])


def test_quote_still_returns_none_when_all_sources_fail(monkeypatch):
    """**不许返回 0 价**：全拿不到 → None（这条一期就有，N3 保持）。"""
    monkeypatch.setattr(fin_data, "_tencent_quote", lambda c: None)
    monkeypatch.setattr(fin_data, "_official_quote", lambda c: None)
    assert fin_data.quote("00700.HK") is None
    assert fin_data.quote("600519") is None


def test_zero_price_is_not_a_quote(monkeypatch):
    """港股报文里价格为 0 = 这个代码拿不到 → 通道返回 None → `quote()` 返回 None。

    **不许返回 0 价**（`fin_data.py` 一期就有的做法，N3 保持）：0 与真价长得一样，
    送进撮合就是拿一个不存在的一档价成交。
    """
    body = ('v_hk00700="1~x~00700~0~0~0~0";').encode("gbk")

    class _Resp:
        status_code = 200
        content = body

    monkeypatch.setattr("requests.get", lambda *a, **kw: _Resp())
    monkeypatch.setattr(fin_data, "_official_quote", lambda c: None)
    assert fin_data._tencent_quote("00700.HK") is None
    assert fin_data.quote("00700.HK") is None


# ── ③ 港美股标的元数据 ──────────────────────────────────────────────────────

def test_hk_master_all_reads_the_repo_csv(monkeypatch):
    """港股每手来自港交所官方清单（随仓库分发的 `data/hk_master.csv`）。"""
    if not _HK_CSV.exists():
        pytest.skip("仓库里没有 data/hk_master.csv")
    from app.services.gm import findata_db

    monkeypatch.setattr(findata_db, "_HK_CSV", str(_HK_CSV))
    monkeypatch.setattr(findata_db, "_hk_csv_cache", None)
    rows = findata_db.hk_master_all()
    assert len(rows) > 3000
    by = {r["code"]: r for r in rows}
    assert by["00700"]["lot_size"] == 100
    assert by["00005"]["lot_size"] == 400


def test_instrument_intl_hk_uses_official_csv(monkeypatch):
    """港股：每手取官方清单、交易所 HKEX、**无涨跌幅**（NULL，不编幅度）。"""
    if not _HK_CSV.exists():
        pytest.skip("仓库里没有 data/hk_master.csv")
    from app.services.gm import findata_db

    monkeypatch.setattr(findata_db, "_HK_CSV", str(_HK_CSV))
    monkeypatch.setattr(findata_db, "_hk_csv_cache", None)
    item = fin_data.instrument_intl("00700", "hk")
    assert item["available"] is True
    assert item["exchange"] == "HKEX" and item["board"] == "hk_main"
    assert item["lot_size"] == 100
    assert item["market"] == "HK" and item["currency"] == "HKD"
    assert item["limit_up_pct"] is None and item["limit_down_pct"] is None
    assert item["source"] == "hkex_listofsecurities(data/hk_master.csv)"


def test_instrument_intl_hk_missing_lot_is_unavailable(monkeypatch):
    """清单里没有这只 → `available=false`，**不按 100 猜每手**。"""
    monkeypatch.setattr(fin_data, "_hk_master_row", lambda c: None)
    item = fin_data.instrument_intl("99999", "hk")
    assert item["available"] is False and "每手" in item["reason"]


def test_instrument_intl_us_uses_gateway_exchange(monkeypatch):
    monkeypatch.setattr(fin_data, "_us_exchange_and_name", lambda c: ("NASDAQ", "Apple Inc."))
    item = fin_data.instrument_intl("AAPL", "us")
    assert item["available"] is True
    assert item["exchange"] == "NASDAQ" and item["lot_size"] == 1     # 美股每手 = 1（事实）
    assert item["market"] == "US" and item["currency"] == "USD"
    assert item["limit_up_pct"] is None
    assert item["source"] == "hunter_gateway(us_quote.exchange)"


def test_instrument_intl_us_unknown_exchange_is_unavailable(monkeypatch):
    """交易所判不出 → `available=false`（不猜一个交易所写进账本）。"""
    monkeypatch.setattr(fin_data, "_us_exchange_and_name", lambda c: (None, None))
    item = fin_data.instrument_intl("ZZZZ", "us")
    assert item["available"] is False and "交易所判不出" in item["reason"]

    monkeypatch.setattr(fin_data, "_us_exchange_and_name", lambda c: ("LSE", "X"))
    assert fin_data.instrument_intl("X", "us")["available"] is False


def test_instrument_intl_rejects_unknown_market():
    item = fin_data.instrument_intl("00700", "jp")
    assert item["available"] is False and "不支持的市场" in item["reason"]
