"""L09 · 采集补齐（新闻 / 基本面 / 情绪）。

两块：

① **纯函数 / 入参校验**（不连库）：情绪登记口的必填校验（**拒绝不猜**：缺 `model_version` /
   `quality` / `generated_at` / `as_of` → 报错，绝不填默认值）；代码形态过滤；市场映射。
② **真库**（`TEST_DATABASE_URL`，无则整体 skip）：
   · 情绪：登记 → 读回 → **只追加**（UPDATE / DELETE 被触发器拒）；`sentiment` 省略时
     落 `NULL`；`quality` / `model_version` / `evidence_ref` 真的落库。
   · 新闻：`get_news` 有数据 → 插入 `news`（`available_at` / `revision_id` 恒 `NULL`、
     `fetched_at` 有真值）；**同一份再采一次不重复插**；`get_news` 返回空 → **记缺口、
     `news` 行数不变**（红线 5：不写假行）。
   · 基本面：港 / 美股 → 如实「数据源未接」（`ok=False`），**不记缺口**。

红线 5 是本文件的重点：每一条「行数不变 / 值是 NULL」的断言都在盯着「没有偷偷填」。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

from app.services.fin import collection as coll
from app.services.fin import sentiment as sent

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
UTC = timezone.utc


# ════════════════════════════════════════════════════════════════════════
# 一 · 纯函数（不连库）
# ════════════════════════════════════════════════════════════════════════

class _NoCur:
    """给「只走 codes 分支」的调用一个哑游标 —— 一旦真用它就报错（防止测试意外连库）。"""
    def execute(self, *a, **k):
        raise AssertionError("这条路径不该碰数据库")


def test_valid_code_filters_by_market_shape():
    # A 股 6 位、港股 5 位、美股含字母 —— 形态过滤，**不猜标的**
    assert [c for c in ["600519", "000001", "6155515", "1"] if coll._valid_code(c, "cn")] == ["600519", "000001"]
    assert [c for c in ["00700", "09988", "600519"] if coll._valid_code(c, "hk")] == ["00700", "09988"]
    assert [c for c in ["AAPL", "BRK.B", "600519"] if coll._valid_code(c, "us")] == ["AAPL", "BRK.B"]


def test_market_label_maps_channel_to_three_values():
    assert coll.market_label("cn") == "CN_A"
    assert coll.market_label("hk") == "HK"
    assert coll.market_label("us") == "US"


def test_collection_universe_with_explicit_codes_filters_and_caps():
    codes = ["600519", "000001", "000001", "999", "abc"]
    assert coll.collection_universe(_NoCur(), "cn", 10, codes) == ["600519", "000001"]
    assert coll.collection_universe(_NoCur(), "cn", 1, codes) == ["600519"]      # 截断


def test_collection_universe_rejects_unknown_market():
    with pytest.raises(coll.CollectionError):
        coll.collection_universe(_NoCur(), "jp", 10, ["600519"])


# ── 情绪登记口：必填校验（拒绝，不猜）────────────────────────────────────

def _payload(**over):
    base = {
        "code": "600519", "market": "CN_A",
        "as_of": "2026-10-04T09:30:00+08:00",
        "sentiment": 0.42, "model_version": "manual:v1",
        "evidence_ref": "news:12345",
        "generated_at": "2026-10-04T09:35:00+08:00",
        "quality": "ok", "source": "manual",
    }
    base.update(over)
    return base


@pytest.mark.parametrize("drop", ["code", "as_of", "model_version", "quality", "source", "generated_at"])
def test_sentiment_requires_provenance_fields(drop):
    """§6.2 的「模型版本 / 生成时间 / 质量」与来源**必填** —— 缺一个就拒，不填默认值。

    校验**全部在连库之前**完成（`register_sentiment` 先校验、后 `get_conn()`），
    所以本条**不需要数据库**：入参非法时在 `get_conn()` 之前就抛 `SentimentError`。
    """
    p = _payload()
    p.pop(drop)
    with pytest.raises(sent.SentimentError):
        sent.register_sentiment(p)


def test_sentiment_rejects_naive_timestamp():
    with pytest.raises(sent.SentimentError):
        sent.register_sentiment(_payload(as_of="2026-10-04T09:30:00"))   # 无时区


def test_sentiment_rejects_bad_market():
    with pytest.raises(sent.SentimentError):
        sent.register_sentiment(_payload(market="JP"))


def test_sentiment_rejects_non_numeric_value():
    """情绪值必须是数字或省略 —— 字符串 / 布尔一律拒（**别拿涨跌幅当字符串塞进来**）。"""
    with pytest.raises(sent.SentimentError):
        sent.register_sentiment(_payload(sentiment="bullish"))


def test_sentiment_json_conversion_keeps_id_int_and_value_float():
    """`id`（BIGINT）必须是 int；情绪值（NUMERIC→Decimal）才转 float。

    回归：`_jsonable` 曾把所有 int 也转 float，`id` 回来变成 `1.0`。
    """
    from decimal import Decimal
    row = sent.to_json({"id": 7, "code": "x", "sentiment": Decimal("0.42"),
                        "created_at": None})
    assert row["id"] == 7 and isinstance(row["id"], int)      # **不是 7.0**
    assert isinstance(row["sentiment"], float) and row["sentiment"] == 0.42


# ════════════════════════════════════════════════════════════════════════
# 二 · 真库
# ════════════════════════════════════════════════════════════════════════

psycopg2 = pytest.importorskip("psycopg2")


def _has_db() -> bool:
    if not TEST_DATABASE_URL:
        return False
    try:
        conn = psycopg2.connect(TEST_DATABASE_URL)
    except Exception:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('fin_sentiment'), to_regclass('news'), "
                        "to_regclass('fin_data_gap')")
            return all(x is not None for x in cur.fetchone())
    finally:
        conn.close()


_db = pytest.mark.skipif(not _has_db(),
                         reason="需要 TEST_DATABASE_URL 指向一个已跑过 0053 迁移的 postgres")

if _has_db():
    sent.DATABASE_URL = TEST_DATABASE_URL
    coll.DATABASE_URL = TEST_DATABASE_URL


def _count(table: str, where: str = "", params=()) -> int:
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {table} {where}", params)
            return int(cur.fetchone()[0])
    finally:
        conn.close()


# ── 情绪：登记 → 读回 → 只追加 ────────────────────────────────────────────

@_db
def test_sentiment_register_then_read_roundtrip():
    code = "600519"
    row = sent.register_sentiment(_payload(code=code, sentiment=0.42,
                                            quality="ok", model_version="manual:v1",
                                            evidence_ref="news:12345"))
    assert row["code"] == code
    assert float(row["sentiment"]) == 0.42
    # 模型版本 / 质量 / 证据引用 / 生成时间 **真的落库**（§6.2）
    assert row["model_version"] == "manual:v1"
    assert row["quality"] == "ok"
    assert row["evidence_ref"] == "news:12345"
    assert row["generated_at"] is not None
    assert row["source"] == "manual"

    back = sent.read_sentiment(code, limit=5)
    assert any(r["id"] == row["id"] for r in back)
    # 库里的真值也核对一遍（跨过 Python 层）
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT model_version, quality, evidence_ref, sentiment "
                        "FROM fin_sentiment WHERE id = %s", (row["id"],))
            mv, q, ev, sv = cur.fetchone()
        assert (mv, q, ev) == ("manual:v1", "ok", "news:12345")
        assert float(sv) == 0.42
    finally:
        conn.close()


@_db
def test_sentiment_value_can_be_null_but_provenance_required():
    """**只有标签没有数值**是合法的（值落 `NULL`）—— 但来路四项一个都不能少。"""
    row = sent.register_sentiment(_payload(sentiment=None, sentiment_label="neutral"))
    assert row["sentiment"] is None            # 没值就是 NULL，**不是 0**
    assert row["sentiment_label"] == "neutral"


@_db
def test_sentiment_is_append_only():
    row = sent.register_sentiment(_payload())
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.errors.RaiseException):
                cur.execute("UPDATE fin_sentiment SET quality='x' WHERE id=%s", (row["id"],))
            conn.rollback()
            with pytest.raises(psycopg2.errors.RaiseException):
                cur.execute("DELETE FROM fin_sentiment WHERE id=%s", (row["id"],))
            conn.rollback()
    finally:
        conn.close()


@_db
def test_sentiment_freshness_reports_real_counts():
    before = sent.freshness()
    sent.register_sentiment(_payload())
    after = sent.freshness()
    assert after["rows"] == before["rows"] + 1
    assert after["latest"] is not None and after["latest"]["code"] == "600519"


# ── 新闻：采到就插、重复不插、采不到记缺口不写假行 ─────────────────────────

@_db
def test_news_collection_inserts_and_dedups(monkeypatch):
    from app.services import finance_data_client as fdc

    code = "600519"
    # 用一个真实标题（`news` 表按 (code, title, published_at) 去重）
    title = f"L09 单测新闻 {uuid.uuid4().hex[:8]}"
    items = [{"title": title, "source": "测试源", "url": "http://x/1",
              "published_at": "2026-10-04T08:00:00+08:00", "content": "正文"}]
    monkeypatch.setattr(fdc, "get_news", lambda c, limit=20: list(items))

    before = _count("news", "WHERE code=%s", (code,))
    r1 = coll.collect_news("cn", codes=[code], limit=1, per_code=5)
    after1 = _count("news", "WHERE code=%s", (code,))
    assert r1["inserted"] == 1 and r1["gaps"] == 0
    assert after1 == before + 1

    # 同一份再采一次 → **不重复插**
    before_gap = _count("fin_data_gap", "WHERE source='news' AND code=%s", (code,))
    r2 = coll.collect_news("cn", codes=[code], limit=1, per_code=5)
    after2 = _count("news", "WHERE code=%s", (code,))
    after_gap = _count("fin_data_gap", "WHERE source='news' AND code=%s", (code,))
    assert r2["inserted"] == 0 and after2 == after1
    # **去重命中不是缺口**：源正常返回、只是全是已有行 → 一行缺口都不该记
    assert r2["gaps"] == 0 and after_gap == before_gap

    # 时间字段口径：available_at / revision_id 是 NULL（源不给）；fetched_at 有真值
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT published_at, fetched_at, available_at, revision_id "
                        "FROM news WHERE code=%s AND title=%s", (code, title))
            pub, fet, av, rev = cur.fetchone()
        assert pub is not None and fet is not None     # 发布时刻（源给）与入库时刻（我们写）
        assert av is None and rev is None              # 拿不到真值 → NULL（红线 5）
    finally:
        conn.close()


@_db
def test_news_collection_records_gap_and_writes_no_row(monkeypatch):
    """**数据源没给数据 → 记缺口、`news` 行数不变**（红线 5 的正靶）。"""
    from app.services import finance_data_client as fdc
    monkeypatch.setattr(fdc, "get_news", lambda c, limit=20: [])   # 源不通 / 该票无新闻

    code = "600519"
    before_news = _count("news", "WHERE code=%s", (code,))
    before_gap = _count("fin_data_gap", "WHERE source='news' AND code=%s", (code,))
    r = coll.collect_news("cn", codes=[code], limit=1, per_code=5)
    after_news = _count("news", "WHERE code=%s", (code,))
    after_gap = _count("fin_data_gap", "WHERE source='news' AND code=%s", (code,))

    assert r["ok"] is True and r["inserted"] == 0 and r["gaps"] == 1
    assert after_news == before_news                       # **一行假新闻都没插**
    assert after_gap == before_gap + 1                     # 缺口记下了

    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT kind, market, source FROM fin_data_gap "
                        "WHERE source='news' AND code=%s ORDER BY id DESC LIMIT 1", (code,))
            kind, market, src = cur.fetchone()
        assert kind == "no_data" and market == "CN_A" and src == "news"
    finally:
        conn.close()


# ── 基本面：港美股如实「未接」，不记缺口 ───────────────────────────────────

@_db
def test_fundamentals_unsupported_market_reports_not_wired():
    before = _count("fin_data_gap", "WHERE source='fundamental'")
    out = coll.collect_fundamentals("us", codes=["AAPL"], limit=1)
    after = _count("fin_data_gap", "WHERE source='fundamental'")
    assert out["ok"] is False and "未接" in out["reason"]
    assert after == before        # 「没有源」不是「这一轮没采到」—— **不记缺口**
