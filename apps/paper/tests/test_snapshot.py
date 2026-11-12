"""快照层（M-12 第①步）：编号口径、数据源时间戳、断流、只追加。

最要紧的一条：**`snapshot_time` 必须来自数据源**（`09 §六-6`）。
这里用两把锁钉它 ——
  ① 源码扫描：整个 `app/snapshot/` 里只允许出现**一次**时钟调用，而且那一次
     是「判断新鲜度」用的，不是填 `snapshot_time` 用的；
  ② 行为断言：数据源给的时刻是 1990 年，落库的 `snapshot_time` 就必须**逐位**
     是 1990 年那一秒 —— 用 `now()` 填的实现在这里当场露馅。
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import pytest

from app import snapshot
from app.snapshot import store
from app.snapshot.source import (
    CST,
    HttpQuoteSource,
    NullQuoteSource,
    Quote,
    _parse_quote_time,
)
from helpers import install_quote_source, quote, uniq_when

SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "app", "snapshot")

_CLOCK = re.compile(r"\b(datetime\.now|datetime\.utcnow|time\.time|utcnow)\s*\(")


def _read(name: str) -> str:
    with open(os.path.join(SNAPSHOT_DIR, name), encoding="utf-8") as fh:
        return fh.read()


# ── ① 源码扫描：时钟只有一个用途 ──────────────────────────────────────────

def test_clock_is_used_exactly_once_and_only_for_freshness():
    """`app/snapshot/` 里只许有一次时钟调用 —— 判断「这张快照旧了没有」那次。

    填 `snapshot_time` 只能用它自己的唯一来源：`quote.quote_time`。
    多出任何一次时钟调用，都意味着某处在拿本机时间当数据源时间。
    """
    hits = []
    for name in sorted(os.listdir(SNAPSHOT_DIR)):
        if not name.endswith(".py"):
            continue
        src = _read(name)
        for m in _CLOCK.finditer(src):
            hits.append(f"{name}:{src[: m.start()].count(chr(10)) + 1}")
    assert len(hits) == 1 and hits[0].startswith("store.py:"), (
        f"app/snapshot/ 的时钟调用应只有 store.py 那一处（新鲜度判定），实际：{hits}"
    )
    line = _read("store.py").splitlines()[int(hits[0].split(":")[1]) - 1]
    assert line.strip().startswith("stamp = now or datetime.now"), (
        f"那一处时钟调用必须是「算新鲜度用的 stamp」，实际是：{line.strip()!r}"
    )


def test_snapshot_time_is_bound_only_to_the_quote():
    """落库参数里 `snapshot_time` 那一格必须逐字是 `quote.quote_time`。"""
    src = _read("store.py")
    assert "snapshot_id, code, quote.quote_time, quote.source," in src
    # 新鲜度用的 stamp 不许出现在 INSERT 的参数里
    insert = src.split("INSERT INTO fin_snapshot")[1].split("row = get(")[0]
    assert "stamp" not in insert
    assert "quote.quote_time" in insert


# ── ② 纯函数：编号与质量 ──────────────────────────────────────────────────

def test_snapshot_id_format():
    at = datetime(2026, 9, 30, 9, 30, 0, tzinfo=CST)
    assert snapshot.snapshot_id_for("600519", at) == "SNAP-20260930-093000-600519"


def test_snapshot_id_uses_shanghai_time_not_utc():
    """UTC 与上海差 8 小时：编号必须按**上海时间**切年月日时分秒。"""
    utc = datetime(2026, 9, 30, 1, 30, 0, tzinfo=timezone.utc)   # == 上海 09:30
    assert snapshot.snapshot_id_for("600519", utc) == "SNAP-20260930-093000-600519"


def test_classify_marks_old_quotes_stale():
    now = datetime(2026, 10, 2, 10, 0, tzinfo=CST)
    assert store.classify(now - timedelta(seconds=30), now, 900) == ("ok", False)
    assert store.classify(now - timedelta(seconds=3600), now, 900) == ("stale", True)
    assert store.classify(None, now, 900) == ("missing", True)


def test_stale_threshold_falls_back_to_default_on_bad_env(monkeypatch):
    """配错了按**默认**（保护性的一侧）走，不按 0 —— 0 会让所有快照都「新鲜」。"""
    monkeypatch.setenv("PAPER_SNAPSHOT_STALE_SECONDS", "not-a-number")
    assert store.stale_after_seconds() == store.DEFAULT_STALE_SECONDS
    monkeypatch.setenv("PAPER_SNAPSHOT_STALE_SECONDS", "-1")
    assert store.stale_after_seconds() == store.DEFAULT_STALE_SECONDS
    monkeypatch.setenv("PAPER_SNAPSHOT_STALE_SECONDS", "60")
    assert store.stale_after_seconds() == 60


# ── 数据源解析 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("20260827155755", "2026-08-27T15:57:55+08:00"),
    ("2026/08/27 16:03:00", "2026-08-27T16:03:00+08:00"),
    ("2026-08-26 16:00:01", "2026-08-26T16:00:01+08:00"),
    ("2026-08-26 16:00", "2026-08-26T16:00:00+08:00"),
])
def test_parse_quote_time_formats(raw, expected):
    assert _parse_quote_time(raw).isoformat() == expected


@pytest.mark.parametrize("raw", ["", "   ", "昨天", "2026-13-45 99:99:99", None])
def test_parse_quote_time_gives_none_instead_of_guessing(raw):
    assert _parse_quote_time(raw) is None


def test_null_source_never_invents_a_quote():
    assert NullQuoteSource().fetch("600519") is None


def test_http_source_returns_none_when_unreachable():
    """端口上没有服务 → 返回 None（断流），不抛异常、也不给零价。"""
    src = HttpQuoteSource("http://127.0.0.1:9", timeout=0.2)
    assert src.fetch("600519") is None


# ── ③ 落库（需要账本库）─────────────────────────────────────────────────

class _Static:
    """只回答一个固定报价的来源（专给本文件用）。"""

    def __init__(self, fixed: Optional[Quote]):
        self.default = fixed

    def fetch(self, code):  # noqa: ARG002
        return self.default


@pytest.mark.db
def test_capture_persists_source_timestamp_verbatim(pg, monkeypatch):
    """数据源说 1990-01-15 09:30:00，库里就必须是那一秒 —— 不是「现在」。"""
    # 新鲜度上限临时收到 60 秒，让「数据源时刻 vs 本机现在」的差距真的被判出来
    monkeypatch.setenv("PAPER_SNAPSHOT_STALE_SECONDS", "60")
    when = "1990-01-15T09:30:00+08:00"
    snap = snapshot.capture(pg, "600001", source=_Static(quote(code="600001", when=when)),
                            now=datetime(2026, 10, 2, tzinfo=CST))
    pg.connection.rollback()

    assert snap is not None
    assert snap["snapshot_id"] == "SNAP-19900115-093000-600001"
    assert snap["snapshot_time"] == datetime.fromisoformat(when)
    # 这张快照比本机时间早 36 年：用了 now() 的实现在这一行会立刻不等
    assert snap["quality"] == "stale" and snap["missing_flag"] is True
    assert snap["tradable"] is False


@pytest.mark.db
def test_no_timestamp_means_no_row(pg):
    """数据源没给时刻 → **不落行**（`snapshot_time` NOT NULL，不许拿本机时间凑）。"""
    assert snapshot.capture(pg, "600002", source=_Static(quote(code="600002", when=None))) is None
    pg.execute("SELECT count(*) AS n FROM fin_snapshot WHERE code = '600002'")
    n = pg.fetchone()["n"]
    pg.connection.rollback()
    assert n == 0


@pytest.mark.db
def test_no_price_means_no_row(pg):
    bad = Quote(code="600003", source="test",
                quote_time=datetime(2026, 10, 2, 10, 0, tzinfo=CST),
                last_price=None, prev_close=Decimal("10.00"))
    assert snapshot.capture(pg, "600003", source=_Static(bad)) is None
    pg.execute("SELECT count(*) AS n FROM fin_snapshot WHERE code = '600003'")
    n = pg.fetchone()["n"]
    pg.connection.rollback()
    assert n == 0


@pytest.mark.db
def test_source_returning_none_writes_nothing(pg):
    assert snapshot.capture(pg, "600004", source=NullQuoteSource()) is None
    pg.execute("SELECT count(*) AS n FROM fin_snapshot WHERE code = '600004'")
    n = pg.fetchone()["n"]
    pg.connection.rollback()
    assert n == 0


@pytest.mark.db
def test_capture_is_idempotent_per_second(pg):
    """同一秒的同一只票 = 同一张快照（编号一样），重复采集不会多出一行。"""
    when = "1990-02-01T10:00:00+08:00"
    src = _Static(quote(code="600005", when=when))
    first = snapshot.capture(pg, "600005", source=src)
    second = snapshot.capture(pg, "600005", source=src)
    pg.execute("SELECT count(*) AS n FROM fin_snapshot WHERE code = '600005'")
    n = pg.fetchone()["n"]
    pg.connection.rollback()
    assert first["snapshot_id"] == second["snapshot_id"] == "SNAP-19900201-100000-600005"
    assert n == 1


@pytest.mark.db
def test_capture_writes_the_book_fields(pg):
    """盘口与昨收都落下来 —— 市价单的成交价要用对手价，风控要用昨收。"""
    when = "1990-02-02T10:00:00+08:00"
    src = _Static(quote(code="600006", when=when, price="10.00", prev_close="9.80",
                        bid1="9.99", ask1="10.01"))
    snap = snapshot.capture(pg, "600006", source=src)
    pg.connection.rollback()
    assert str(snap["last_price"]) == "10.0000"
    assert str(snap["prev_close"]) == "9.8000"
    assert str(snap["bid1_price"]) == "9.9900"
    assert str(snap["ask1_price"]) == "10.0100"
    assert snap["tradable"] is True


@pytest.mark.db
def test_latest_for_code_reads_by_snapshot_time(pg):
    when = uniq_when("prj_test_600007abcdef12", 3)
    install_quote_source(code="600007", when=when)
    captured = snapshot.capture(pg, "600007")
    latest = snapshot.latest_for_code(pg, "600007")
    pg.connection.rollback()
    assert captured["snapshot_id"] == latest["snapshot_id"]
