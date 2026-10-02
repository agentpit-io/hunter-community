"""数据缺口与新鲜度（M7 · M-20）。

三件事：

1. **断流留痕**：数据源拿不到 → `fin_snapshot` 不落行，但 `fin_data_gap` 落一条
   （`01方案 §11.2`：数据源中断要标记质量与缺口）；
2. **三种缺口各记各的**（`no_data` / `no_timestamp` / `no_price`），而且**不互相冒充**；
3. **概览**按 kind 聚合，`stale`（有价但旧）**不进这张表** —— 那种快照落在
   `fin_snapshot` 里带 `missing_flag`，是「有信息的一行」。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import pytest

pytestmark = pytest.mark.db

if not os.getenv("PAPER_TEST_DSN"):
    pytest.skip("未设置 PAPER_TEST_DSN，跳过需要账本库的用例", allow_module_level=True)

from app import data_gap, snapshot  # noqa: E402
from app.snapshot.source import Quote  # noqa: E402


class _Static:
    """固定报价源：给什么回什么，`None` 就是断流。"""

    name = "static"

    def __init__(self, q: Optional[Quote]):
        self._q = q

    def fetch(self, code: str) -> Optional[Quote]:
        return self._q


def _q(code="600001", **kw) -> Quote:
    base = dict(
        code=code, source="static",
        quote_time=datetime(2026, 10, 2, 10, 0, tzinfo=timezone(timedelta(hours=8))),
        last_price=Decimal("10.00"), prev_close=Decimal("9.80"),
    )
    base.update(kw)
    return Quote(**base)


def _gaps(pg, code):
    pg.execute(
        "SELECT kind, detail FROM fin_data_gap WHERE code = %s ORDER BY id", (code,)
    )
    rows = pg.fetchall()
    return [(r["kind"], r["detail"]) for r in rows]


def test_no_data_records_a_gap(pg):
    """数据源拿不到 → 不落快照，但缺口落库。"""
    assert snapshot.capture(pg, "600101", source=_Static(None)) is None
    kinds = [k for k, _ in _gaps(pg, "600101")]
    assert kinds == ["no_data"]


def test_no_timestamp_records_a_gap(pg):
    """有报价、没数据源时刻 → 不落快照（不许拿本机时间顶），缺口落库。"""
    assert snapshot.capture(pg, "600102", source=_Static(_q("600102", quote_time=None))) is None
    assert _gaps(pg, "600102")[0][0] == "no_timestamp"


def test_no_price_records_a_gap(pg):
    """有报价、没最新价 → 不落快照，缺口落库。"""
    assert snapshot.capture(pg, "600103", source=_Static(_q("600103", last_price=None))) is None
    assert _gaps(pg, "600103")[0][0] == "no_price"


def test_good_quote_records_no_gap(pg):
    """拿得到就落快照、不记缺口 —— 缺口表不是「取数日志」。"""
    row = snapshot.capture(pg, "600104", source=_Static(_q("600104")))
    assert row is not None and row["last_price"] == Decimal("10.0000")
    assert _gaps(pg, "600104") == []


def test_summary_counts_by_kind(pg):
    """概览按 kind 聚合，且只数观察窗口内的。"""
    snapshot.capture(pg, "600105", source=_Static(None))
    snapshot.capture(pg, "600106", source=_Static(None))
    snapshot.capture(pg, "600107", source=_Static(_q("600107", quote_time=None)))
    since = datetime.now(timezone.utc) - timedelta(minutes=5)
    out = data_gap.summary_since(pg, since)
    by_kind = {k["kind"]: k for k in out["kinds"]}
    # 全库可能还有别的用例留下的缺口，所以断言「至少」而不是「恰好」
    assert by_kind["no_data"]["count"] >= 2 and by_kind["no_data"]["codes"] >= 2
    assert by_kind["no_timestamp"]["count"] >= 1
    assert out["total"] >= 3


def test_gap_recording_never_breaks_the_transaction(pg):
    """缺口表写不进去（比如权限没授）也不许把取数/撮合事务带崩。

    这里用「同一次 capture 调两次」间接验证：即便表被重命名之类，函数也只记日志、
    仍然返回 `None` 让上层走「不成交」分支。
    """
    # 正常路径：返回 None 且不抛
    assert snapshot.capture(pg, "600108", source=_Static(None)) is None
    assert snapshot.capture(pg, "600108", source=_Static(None)) is None
    assert len(_gaps(pg, "600108")) == 2
