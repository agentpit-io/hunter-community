"""L03 · `DataSnapshot` 对象（技术方案 §10.2 · 工具名 `data.snapshot_create/get`）。

两块：

① **纯函数 / 守门**（不连库）：`reject_forged_available_at` —— 「不许用当前时间冒充
   `available_at`」的**可失败**测试（故意传 `now()`，它必须报错）；以及 `create` 的入参校验。
② **真库**（`TEST_DATABASE_URL`，无则整体 skip）：落一张快照 → 读回 → 断言
   `revision_id` / `available_at` 在数据源不给时**确实是 `NULL`**；只追加触发器挡下改 / 删。

红线 5：拿不到真值一律 `NULL` —— 本文件里每一条 `IS NULL` 断言都是盯着「没有偷偷填」。

清理：`_purge` **用同一个连接**（先关触发器再删，最后 commit）—— 另开连接会与测试
游标持有的行锁互等（`ALTER TABLE … DISABLE TRIGGER` 要 ACCESS EXCLUSIVE）。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services.fin import data_snapshot as ds

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

UTC = timezone.utc


# ════════════════════════════════════════════════════════════════════════
# 一 · 纯函数：守门「不许用当前时间冒充 available_at」+ 入参校验
# ════════════════════════════════════════════════════════════════════════

def test_forged_available_at_is_rejected():
    """**这是那条能失败的测试**：把 `available_at` 写成 `now()`，必须报错。

    守卫对「落在 now() 附近（±容差）」的值判为疑似冒充。删掉守卫 → 本断言当场失败。
    """
    now = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(ds.ForgedTimestamp):
        ds.reject_forged_available_at(now, now=now)                 # 正好是 now


def test_near_now_available_at_is_rejected():
    now = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(ds.ForgedTimestamp):
        ds.reject_forged_available_at(now + timedelta(seconds=1), now=now)


def test_real_source_time_passes_and_none_passes():
    now = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
    ds.reject_forged_available_at(None, now=now)                 # 没值 → 放行（NULL 是诚实的）
    ds.reject_forged_available_at(now - timedelta(days=3), now=now)  # 真实来源时刻 → 放行


def test_create_rejects_forged_available_at_before_inserting():
    """守门在 `create` 里也生效：伪造的 `available_at` 在落库前就被挡下。"""
    class _Cur:
        def execute(self, *a, **k):        # 不该被调到
            raise AssertionError("伪造的 available_at 不该走到 INSERT")

    with pytest.raises(ds.ForgedTimestamp):
        ds.create(
            _Cur(),
            data_cutoff_at=datetime(2026, 10, 4, 12, 0, tzinfo=UTC),
            source="tencent-qt",
            quality="ok",
            available_at=datetime.now(UTC),   # ← 冒充
        )


@pytest.mark.parametrize("kwargs,msg", [
    ({"data_cutoff_at": None, "source": "s", "quality": "ok"}, "data_cutoff_at"),
    ({"data_cutoff_at": datetime(2026, 10, 4, tzinfo=UTC), "source": "  ", "quality": "ok"},
     "source"),
    ({"data_cutoff_at": datetime(2026, 10, 4, tzinfo=UTC), "source": "s", "quality": ""},
     "quality"),
    ({"data_cutoff_at": datetime(2026, 10, 4, tzinfo=UTC), "source": "s", "quality": "ok",
      "market": "XX"}, "未知市场"),
])
def test_create_rejects_bad_input(kwargs, msg):
    class _Cur:
        def execute(self, *a, **k):
            raise AssertionError("非法入参不该走到 INSERT")

    with pytest.raises(ds.SnapshotError) as ei:
        ds.create(_Cur(), **kwargs)
    assert msg in str(ei.value)


# ════════════════════════════════════════════════════════════════════════
# 二 · 真库：落库 → 读回 → NULL 断言 → 只追加
# ════════════════════════════════════════════════════════════════════════

psycopg2 = pytest.importorskip("psycopg2")
import psycopg2.extras  # noqa: E402


def _tables_ready() -> bool:
    if not TEST_DATABASE_URL:
        return False
    try:
        conn = psycopg2.connect(TEST_DATABASE_URL)
    except Exception:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('fin_data_snapshot')")
            return cur.fetchone()[0] is not None
    finally:
        conn.close()


_db = pytest.mark.skipif(not _tables_ready(),
                         reason="需要 TEST_DATABASE_URL 指向一个已跑过 0048 迁移的 postgres")

if _tables_ready():
    ds.DATABASE_URL = TEST_DATABASE_URL


@pytest.fixture
def cur():
    """测试库连接。**不清理落下的行** —— 本表只追加，用例 id 随机、互不干扰，
    且 `TEST_DATABASE_URL` 指向的是一次性库。（曾经用 `ALTER TABLE … DISABLE TRIGGER`
    清理，它要 ACCESS EXCLUSIVE，与同事务里的写互等 —— 那正是「DDL 撞上一个未提交的
    写」的老毛病，索性不做。）
    """
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            yield c
        conn.rollback()
    finally:
        conn.close()


@_db
def test_create_then_get_roundtrip_and_null_when_not_provided(cur):
    cutoff = datetime(2026, 10, 4, 1, 30, tzinfo=UTC)
    row = ds.create(
        cur, data_cutoff_at=cutoff, source="tencent-qt", quality="ok",
        market="CN_A", code="600519", note="单测",
        # revision_id / available_at / artifact_ref **不给** → 必须是 NULL
    )
    cur.connection.commit()
    assert row["data_snapshot_id"].startswith("DSNAP-")
    assert row["source"] == "tencent-qt"
    assert row["quality"] == "ok"
    # 数据源不给的三样：**确实是 NULL**（不是被填了默认值）。
    assert row["revision_id"] is None
    assert row["available_at"] is None
    assert row["artifact_ref"] is None
    # 再经 get 读回，一致。
    again = ds.get(cur, row["data_snapshot_id"])
    assert again["data_snapshot_id"] == row["data_snapshot_id"]
    assert again["available_at"] is None
    # 库里的真值也确认为 NULL（跨过 Python 层的 ISO 转换）。
    cur.execute("SELECT available_at, revision_id FROM fin_data_snapshot "
                " WHERE data_snapshot_id = %s", (row["data_snapshot_id"],))
    raw = cur.fetchone()
    assert raw["available_at"] is None and raw["revision_id"] is None


@_db
def test_real_values_are_stored_when_provided(cur):
    cutoff = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)
    available = datetime(2026, 10, 3, 8, 5, tzinfo=UTC)   # 过去时刻 → 不是冒充
    row = ds.create(
        cur, data_cutoff_at=cutoff, source="akshare", quality="ok",
        revision_id="rev-2026-10-03", artifact_ref="s3://bucket/snap.parquet",
        available_at=available, market="HK", code="00700",
    )
    cur.connection.commit()
    assert row["revision_id"] == "rev-2026-10-03"
    assert row["artifact_ref"] == "s3://bucket/snap.parquet"
    assert row["available_at"] is not None


@_db
def test_get_missing_returns_none(cur):
    assert ds.get(cur, "DSNAP-does-not-exist-" + uuid.uuid4().hex) is None


@_db
def test_append_only_update_and_delete_are_rejected(cur):
    row = ds.create(cur, data_cutoff_at=datetime(2026, 10, 4, tzinfo=UTC),
                    source="t", quality="ok")
    pid = row["data_snapshot_id"]
    cur.connection.commit()          # 先落库，让触发器对「真实存在的行」生效
    with pytest.raises(psycopg2.Error) as ei:
        cur.execute("UPDATE fin_data_snapshot SET quality='stale' "
                    " WHERE data_snapshot_id = %s", (pid,))
    assert "只追加" in str(ei.value)
    cur.connection.rollback()

    with pytest.raises(psycopg2.Error) as ei2:
        cur.execute("DELETE FROM fin_data_snapshot WHERE data_snapshot_id = %s", (pid,))
    assert "只追加" in str(ei2.value)
    cur.connection.rollback()
