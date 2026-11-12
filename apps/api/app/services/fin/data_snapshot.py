"""`DataSnapshot` 对象（五期 L03 · 技术方案 §10.2 / §10.1）。

§10.2 要的「**数据截止时间 + 来源 + 版本 + 质量 + 产物引用**」五合一对象。落成一张
**只追加**的表 `fin_data_snapshot`（迁移 `0048`），由两个入口操作（§10.1 的工具名）：

| 工具 | HTTP | 谁调 |
|---|---|---|
| `data.snapshot_create` | `POST /internal/fin/data/snapshot` | 策略 / 研究工作流（`L04` 起） |
| `data.snapshot_get`    | `GET  /internal/fin/data/snapshot/{id}` | 任何要核对「这份快照是什么」的地方 |

**这是不可变对象**：只 `INSERT`，改 / 删由 `0048` 的触发器抛异常挡下。

## 红线 5 的落点：**算不出的一律 NULL**

| 列 | 口径 | 数据源不给时 |
|---|---|---|
| `data_cutoff_at` | 数据截止时间（**必填**，没有它就不是快照） | — |
| `source` | 来源（**必填**） | — |
| `quality` | 质量（**必填**） | — |
| `revision_id` | 数据修订版本 | **`NULL`**（不许编自增号） |
| `artifact_ref` | 产物引用（不可变文件 / 对象） | **`NULL`**（没有落盘产物） |
| `available_at` | 当时系统**实际可获取**该信息的时间 | **`NULL`**（不许拿 `now()` 顶替） |

`available_at` 另有一道**启发式防线** `reject_forged_available_at`：调进来的值若落在
「现在」附近（±`FORGE_TOLERANCE_SECONDS` 秒）判为疑似用本机时钟冒充 → 拒绝。它是防线、
**不是证明** —— 真值只能来自数据源。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import psycopg2

# 连接方式与 `app/services/database.py` / `app/services/fin/store.py` 同口径：
# psycopg2 直连，模块级 `DATABASE_URL`（测试按同一套手法 patch 它）。
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://hunter:hunter@localhost:5432/hunter")

MARKET_VALUES = ("CN_A", "HK", "US")

# 「疑似用当前时间冒充 available_at」的判定容差（秒）。见 `reject_forged_available_at`。
FORGE_TOLERANCE_SECONDS = 2


def get_conn():
    return psycopg2.connect(DATABASE_URL)


class SnapshotError(ValueError):
    """`DataSnapshot` 输入不合法（路由层翻 400）。"""


class ForgedTimestamp(SnapshotError):
    """拿不到真值却用本机当前时间冒充 —— 红线 5。"""


def reject_forged_available_at(available_at: Optional[datetime],
                               *, now: Optional[datetime] = None) -> None:
    """`available_at` **不许用「当前时间」冒充**（红线 5，L03 正中靶心）。

    「当时系统实际可获取该信息的时间」是**数据源的事实**，不是我们能造的。
    数据源不报它、我们也没测可获取延迟时，唯一诚实的值是 `NULL`。

    本函数是**启发式防线**（不是证明）：传进来的 `available_at` 若落在「现在」附近
    （±`FORGE_TOLERANCE_SECONDS` 秒），判为疑似拿本机时钟顶替 → 抛 `ForgedTimestamp`。
    有真实来源的时刻（过去某一天）自然通过。
    """
    if available_at is None:
        return
    reference = now or datetime.now(timezone.utc)
    if abs((reference - available_at).total_seconds()) <= FORGE_TOLERANCE_SECONDS:
        raise ForgedTimestamp(
            f"available_at 疑似用当前时间冒充（落在 now() 的 ±{FORGE_TOLERANCE_SECONDS} 秒内）。"
            "数据源没给「实际可获取时间」时只能写 NULL，不许拿本机时钟顶替（红线 5）"
        )


def new_id() -> str:
    """`DSNAP-<hex>`。**不可变快照编号**（§6.2 的 `snapshot_id`）——`L04` 拿它绑定决策。"""
    return "DSNAP-" + uuid.uuid4().hex[:24]


def create(
    cur,
    *,
    data_cutoff_at: datetime,
    source: str,
    quality: str,
    revision_id: Optional[str] = None,
    artifact_ref: Optional[str] = None,
    available_at: Optional[datetime] = None,
    market: Optional[str] = None,
    code: Optional[str] = None,
    note: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict:
    """`data.snapshot_create` 的服务层：落一张 `DataSnapshot`，返回落库后的行。

    三个必填项是**事实**（缺一个就不是快照）：`data_cutoff_at` / `source` / `quality`。
    其余**算不出就传 `None` → `NULL`**（本表与函数都不设默认值 —— 填一个「看起来正常」的
    默认就是编数字）。
    """
    if data_cutoff_at is None:
        raise SnapshotError("data_cutoff_at 必填（没有数据截止时间就不是快照）")
    if not (source or "").strip():
        raise SnapshotError("source 必填")
    if not (quality or "").strip():
        raise SnapshotError("quality 必填")
    if market not in (None, "") and market not in MARKET_VALUES:
        raise SnapshotError(f"未知市场 {market!r}（可选 {'/'.join(MARKET_VALUES)}）")
    reject_forged_available_at(available_at, now=now)

    sid = new_id()
    cur.execute(
        """
        INSERT INTO fin_data_snapshot
          (data_snapshot_id, data_cutoff_at, source, revision_id, quality,
           artifact_ref, available_at, market, code, note)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (sid, data_cutoff_at, source.strip(),
         (revision_id or None), quality.strip(), (artifact_ref or None),
         available_at, (market or None), (code or None), note),
    )
    return get(cur, sid)


_SELECT = (
    "SELECT data_snapshot_id, data_cutoff_at, source, revision_id, quality, "
    "artifact_ref, available_at, market, code, note, created_at "
    "FROM fin_data_snapshot "
)


def get(cur, data_snapshot_id: str) -> Optional[dict]:
    """`data.snapshot_get` 的服务层：按编号取一张快照；没有 → `None`（路由翻 404）。"""
    cur.execute(_SELECT + "WHERE data_snapshot_id = %s", (data_snapshot_id,))
    row = cur.fetchone()
    return _row_to_dict(row) if row else None


def _row_to_dict(row: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}
