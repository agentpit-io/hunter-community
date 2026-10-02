"""快照落库：编号、质量判定、**只追加**写入。

编号口径（任务书 §1）：`SNAP-{日期}-{时刻}-{代码}`，例 `SNAP-20260930-093000-600519`。
日期与时刻都取自**数据源回报的时刻**（上海时间），所以同一秒里的同一只票拿到的是
同一张快照 —— 这正是「同一快照 + 同一委托 → 同一成交价」可复现的前提。

质量判定是**两条独立的判据**，别混：

| 情形 | `quality` | `missing_flag` | 落库？ |
|---|---|---|---|
| 数据源给了时刻，且足够新鲜 | `ok` | false | 落 |
| 数据源给了时刻，但旧了（断流：喂价停了） | `stale` | **true** | 落 |
| 数据源**没给时刻** | — | — | **不落**（`capture` 返回 `None`） |
| 数据源拿不到 / 超时 / 没这只票 | — | — | **不落**（`capture` 返回 `None`） |

后两行是「数据源没给时间戳就别落」（`09 §六-6`）的直接兑现：`fin_snapshot.snapshot_time`
是 `NOT NULL`，填它就得有一个真实时刻，没有就宁可不写这一行。上层拿到 `None` 时
**必须走「不成交」分支**（`09 §4.4`：宁可挂单，绝不用过期价成交）。

`missing_flag=true` 的行仍然写 —— 它是有信息的：它记下了「那一刻我们看到的价是多少、
已经旧了多久」。估值（`valuation.py`）与对账都靠它区分「没价」和「有价但旧」。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from loguru import logger

from app.snapshot.source import CST, Quote, QuoteSource, get_source

# 快照「新鲜」的上限。超过它 = 喂价停了（断流），标 stale 并且**不许用于成交**。
# 15 分钟对应 A 股行情链路的延迟量级（`CLAUDE.md` 记的扫描源 update_mode 也是
# delayed_streaming_900）。execution区宁可保守：不确定新鲜就不成交。
DEFAULT_STALE_SECONDS = 900


def stale_after_seconds() -> int:
    raw = (os.getenv("PAPER_SNAPSHOT_STALE_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_STALE_SECONDS
    try:
        value = int(raw)
    except ValueError:
        # 配错了按**默认**（保护性的一侧）走，不按 0 —— 0 会让所有快照都"新鲜"。
        logger.warning("[paper.snapshot] PAPER_SNAPSHOT_STALE_SECONDS={!r} 不是整数，按默认 {}",
                       raw, DEFAULT_STALE_SECONDS)
        return DEFAULT_STALE_SECONDS
    return value if value > 0 else DEFAULT_STALE_SECONDS


def snapshot_id_for(code: str, quote_time: datetime) -> str:
    """`SNAP-{YYYYMMDD}-{HHMMSS}-{代码}`。时刻按上海时间取。"""
    local = quote_time.astimezone(CST)
    return f"SNAP-{local:%Y%m%d}-{local:%H%M%S}-{code}"


def classify(
    quote_time: Optional[datetime],
    now: datetime,
    stale_after: int,
) -> tuple[str, bool]:
    """`(quality, missing_flag)`。没有时刻 → 调用方根本不该走到这里。"""
    if quote_time is None:
        return "missing", True
    age = (now - quote_time).total_seconds()
    if age > stale_after:
        return "stale", True
    return "ok", False


def capture(
    cur,
    code: str,
    *,
    source: Optional[QuoteSource] = None,
    now: Optional[datetime] = None,
) -> Optional[dict]:
    """取一张快照并落 `fin_snapshot`。拿不到 → `None`（**不落行**）。

    `now` 只用于**判断新鲜度**（断流判定），**绝不用来填 `snapshot_time`** ——
    `snapshot_time` 只有一个来源：`quote.quote_time`（数据源给的）。
    测试用 `now=` 注入一个固定时刻，好让「旧了 → stale」这件事可复现。
    """
    src = source or get_source()
    quote: Optional[Quote] = src.fetch(code)
    if quote is None:
        logger.info("[paper.snapshot] {} 拿不到行情（断流/未接通），不落快照", code)
        return None

    if quote.quote_time is None:
        # 数据源没给时刻 —— 不许拿本机时间顶上（`09 §六-6`）。
        logger.warning("[paper.snapshot] {} 的行情没有数据源时间戳，按「不落快照」处理", code)
        return None

    if quote.last_price is None:
        logger.warning("[paper.snapshot] {} 的行情没有最新价，按「不落快照」处理", code)
        return None

    stamp = now or datetime.now(timezone.utc)
    quality, missing_flag = classify(quote.quote_time, stamp, stale_after_seconds())
    snapshot_id = snapshot_id_for(code, quote.quote_time)

    cur.execute(
        """
        INSERT INTO fin_snapshot
          (snapshot_id, code, snapshot_time, source, last_price, prev_close,
           bid1_price, bid1_volume, ask1_price, ask1_volume, quality, missing_flag)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (snapshot_id) DO NOTHING
        """,
        (
            snapshot_id, code, quote.quote_time, quote.source,
            _money(quote.last_price),
            _money(quote.prev_close),
            _money(quote.bid1_price), quote.bid1_volume,
            _money(quote.ask1_price), quote.ask1_volume,
            quality, missing_flag,
        ),
    )
    row = get(cur, snapshot_id)
    if row is None:                       # 理论上不可能：刚 INSERT 过
        return None
    return _view(row)


def _money(value: Optional[Decimal]) -> Optional[Decimal]:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.0001"))


# ── 读 ────────────────────────────────────────────────────────────────────

_SELECT = (
    "SELECT snapshot_id, code, snapshot_time, source, last_price, prev_close, "
    "bid1_price, bid1_volume, ask1_price, ask1_volume, quality, missing_flag, created_at "
    "FROM fin_snapshot "
)


def get(cur, snapshot_id: str) -> Optional[dict]:
    cur.execute(_SELECT + "WHERE snapshot_id = %s", (snapshot_id,))
    return cur.fetchone()


def latest_for_code(cur, code: str) -> Optional[dict]:
    cur.execute(_SELECT + "WHERE code = %s ORDER BY snapshot_time DESC LIMIT 1", (code,))
    return cur.fetchone()


def _view(row: dict) -> dict:
    return {**row, "tradable": tradable(row)}


def tradable(row: dict) -> bool:
    """这张快照能不能用来成交。

    三个条件缺一不可：**没被标缺失**、**质量为 ok**、**有最新价**。
    断流（`missing_flag=true`）时这里为假，撮合必须走「不成交」分支。
    """
    return (
        not row.get("missing_flag")
        and row.get("quality") == "ok"
        and row.get("last_price") is not None
    )
