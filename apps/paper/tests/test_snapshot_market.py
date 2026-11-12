"""N3 · 快照带上市场与盘口质量。

出口标准落在这里：

1. **market 逐段透传**：api 的统一行情结构 → `HttpQuoteSource.fetch` → `Quote.market`
   → `fin_snapshot.market`（以前 market 到快照边界就丢了）；
2. **`quote_quality`**（设计文档附 B）：有盘口 `full` / 只有最新价 `last_only`；
3. **「报价时间戳只到日期 → 不得用于成交」专项**（对齐一期 M8 的坑）：
   只有日期的时刻**不落快照**，与「数据源没给时刻」同一处理，并记一条缺口。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app import snapshot
from app.snapshot.source import HttpQuoteSource
from helpers import quote, uniq_when


class _Static:
    """只回答一个固定报价的来源（本文件专用）。"""

    def __init__(self, fixed):
        self.default = fixed

    def fetch(self, code):  # noqa: ARG002
        return self.default


# ── ① 行情来源：透传 market / quote_quality ────────────────────────────────

def _src(monkeypatch, payload: dict) -> HttpQuoteSource:
    src = HttpQuoteSource("http://quote.test")
    monkeypatch.setattr(src, "_get", lambda path, _p=payload: _p)
    return src


_HK_PAYLOAD = {
    "code": "00700", "market": "HK", "quote_quality": "last_only",
    "last_price": "421.20", "prev_close": "431.00",
    "event_time": "2026-10-02T16:08:10+08:00", "source": "tencent-qt",
}


def test_http_source_passes_market_and_quality_through(monkeypatch):
    q = _src(monkeypatch, _HK_PAYLOAD).fetch("00700.HK")
    assert q.market == "HK" and q.quote_quality == "last_only"
    assert str(q.last_price) == "421.20" and str(q.prev_close) == "431.00"


def test_http_source_canonicalises_a_share_market(monkeypatch):
    """api 给 `CN_A` / `A` / `cn` 都归一到本仓统一三值。"""
    for raw in ("CN_A", "A", "cn"):
        payload = {**_HK_PAYLOAD, "code": "600519", "market": raw}
        q = _src(monkeypatch, payload).fetch("600519")
        assert q.market == "CN_A", raw


def test_http_source_derives_market_and_quality_when_source_omits(monkeypatch):
    """老端点（假行情服务）不给这两个字段 → 按代码形态判市场、按盘口判质量。"""
    payload = {"code": "00700", "last_price": "10.0", "prev_close": "9.8",
               "bid1_price": "9.99", "ask1_price": "10.01",
               "event_time": "2026-10-02T10:00:00+08:00"}
    q = _src(monkeypatch, payload).fetch("00700.HK")
    assert q.market == "HK" and q.quote_quality == "full"


def test_http_source_unknown_market_falls_back_to_code_shape(monkeypatch):
    payload = {**_HK_PAYLOAD, "market": "zzz"}
    q = _src(monkeypatch, payload).fetch("00700.HK")
    assert q.market == "HK"          # 按代码形态判，不因为上游写错就丢掉市场


# ── ② 落库：market / quote_quality 写进 fin_snapshot ───────────────────────

@pytest.mark.db
def test_capture_writes_market_and_quote_quality(pg):
    when = uniq_when("prj_test_600008abcdef12", 4)
    src = _Static(quote(code="00700", when=when, market="HK", quote_quality="last_only"))
    snap = snapshot.capture(pg, "00700", source=src)
    pg.connection.rollback()
    assert snap is not None
    assert snap["market"] == "HK" and snap["quote_quality"] == "last_only"


@pytest.mark.db
def test_capture_derives_market_from_code_when_quote_omits_it(pg):
    """行情没带市场 → 按代码形态判（判据只有一份：`market_time.market_of`）。"""
    when = uniq_when("prj_test_600009abcdef12", 5)
    src = _Static(quote(code="00700", when=when, market=None))
    snap = snapshot.capture(pg, "00700", source=src)
    pg.connection.rollback()
    assert snap["market"] == "HK"


@pytest.mark.db
def test_capture_full_quality_when_book_present(pg):
    when = uniq_when("prj_test_60000aabcdef12", 6)
    src = _Static(quote(code="600519", when=when, bid1="9.99", ask1="10.01",
                        market="CN_A", quote_quality="full"))
    snap = snapshot.capture(pg, "600519", source=src)
    pg.connection.rollback()
    assert snap["market"] == "CN_A" and snap["quote_quality"] == "full"


# ── ③ 专项：报价时间戳只到日期 → 不得用于成交 ──────────────────────────────

@pytest.mark.db
def test_date_only_timestamp_is_not_stored_and_records_a_gap(pg):
    """**M8 的坑**：只有日期的时刻落成当天 00:00 → 交易时段与新鲜度双杀、永远不成交。

    现在这种报价**不落快照**（与「没有时刻」同一处理），并记一条 `no_timestamp` 缺口
    写明「只到日期、不得用于成交」。于是它**不可能**被撮合用到。
    """
    src = _Static(quote(code="00700", when="2026-10-02T00:00:00+08:00"))
    snap = snapshot.capture(pg, "00700", source=src)
    assert snap is None

    # 快照表是**全局只追加**的（别的用例也会写 00700），所以按**这一张的编号**断言，
    # 不按代码数总数。
    sid = snapshot.snapshot_id_for("00700", datetime.fromisoformat("2026-10-02T00:00:00+08:00"))
    assert sid == "SNAP-20261002-000000-00700"
    pg.execute("SELECT count(*) AS n FROM fin_snapshot WHERE snapshot_id = %s", (sid,))
    assert pg.fetchone()["n"] == 0

    pg.execute(
        "SELECT kind, detail FROM fin_data_gap "
        " WHERE code = '00700' AND detail LIKE '%%只到日期%%' ORDER BY id DESC LIMIT 1"
    )
    gap = pg.fetchone()
    pg.connection.rollback()
    assert gap is not None and gap["kind"] == "no_timestamp"
    assert "只到日期" in gap["detail"] and "不得用于成交" in gap["detail"]


def test_is_date_only_matches_the_api_judgement():
    """与 `apps/api.fin_data._is_date_only` 同一判据。"""
    from app.snapshot.store import is_date_only

    assert is_date_only(datetime(2026, 10, 2, 0, 0, 0)) is True
    assert is_date_only(datetime(2026, 10, 2, 0, 0, 1)) is False
    assert is_date_only(None) is False
