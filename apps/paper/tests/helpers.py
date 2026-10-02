"""测试小工具：造参考数据、拼委托请求体、装一个假的行情来源。

M3 起**快照由服务端取**（`POST /api/v1/orders` 里没有 `snapshot` 字段了），
所以测试要控制成交价，就得换掉行情来源 —— `install_quote_source()` 干这件事。
它是把 `app.snapshot.source.get_source()` 换成一个固定报价的实现，
不是 mock 掉被测逻辑：走的路（capture → 落库 → 撮合 → 记账）与线上逐行相同。
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from app.snapshot.source import Quote, set_source

CST = timezone(timedelta(hours=8))

# 默认时刻：2026-10-02（周五）10:00 上海时间，落在上午交易时段内。
DEFAULT_AT = "2026-10-02T10:00:00+08:00"


def at(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=CST)


def quote(code="600519", *, price="10.00", prev_close="10.00", bid1=None, ask1=None,
          when: Optional[str] = DEFAULT_AT, source="test") -> Quote:
    """一张固定报价。`when=None` → 数据源**没给时间戳**（用来测「不落快照」）。"""
    return Quote(
        code=code,
        source=source,
        quote_time=None if when is None else at(when),
        last_price=Decimal(price),
        prev_close=None if prev_close is None else Decimal(prev_close),
        bid1_price=None if bid1 is None else Decimal(bid1),
        ask1_price=None if ask1 is None else Decimal(ask1),
        raw={"price": price},
    )


class FakeQuoteSource:
    """固定报价源。`quotes` 按代码覆盖；`default` 兜住其余代码。"""

    def __init__(self, default: Optional[Quote] = None, quotes: Optional[dict] = None):
        self.default = default
        self.quotes = dict(quotes or {})
        self.calls: list[str] = []

    def fetch(self, code: str) -> Optional[Quote]:
        self.calls.append(code)
        if code in self.quotes:
            return self.quotes[code]
        return self.default


def install_quote_source(**kw) -> FakeQuoteSource:
    """装一个固定报价源，返回它（测试可以改它的字段再重跑）。"""
    src = FakeQuoteSource(default=quote(**kw))
    set_source(src)
    return src


def seed_reference(pg, code="600519", trade_date="2026-10-02"):
    """把风控与撮合需要的参考数据写进库（标的 / 日历 / 费用模型 / 执行模型）。"""
    import psycopg2.extras

    pg.execute(
        """
        INSERT INTO fin_instrument
          (code, name, exchange, board, is_st, limit_up_pct, limit_down_pct, lot_size, source)
        VALUES (%s, '测试标的', 'SH', 'main', false, 0.10, 0.10, 100, 'test')
        ON CONFLICT (code) DO UPDATE SET limit_up_pct = EXCLUDED.limit_up_pct,
          limit_down_pct = EXCLUDED.limit_down_pct, lot_size = EXCLUDED.lot_size
        """,
        (code,),
    )
    pg.execute(
        """
        INSERT INTO fin_market_calendar (trade_date, is_trading, sessions)
        VALUES (%s, true, %s)
        ON CONFLICT (trade_date) DO UPDATE SET is_trading = EXCLUDED.is_trading,
          sessions = EXCLUDED.sessions
        """,
        (trade_date, psycopg2.extras.Json(
            [{"open": "09:30", "close": "11:30"}, {"open": "13:00", "close": "15:00"}]
        )),
    )
    pg.execute(
        """
        INSERT INTO fin_fee_model (version, commission_pct, commission_min, stamp_tax_pct, transfer_fee_pct)
        VALUES ('fee-cn-a-v1', 0.00025, 5.00, 0.0005, 0.00001)
        ON CONFLICT (version) DO NOTHING
        """
    )
    pg.execute(
        """
        INSERT INTO fin_execution_model (version, slippage_ticks, tick_size, part_fill, note)
        VALUES ('paper-model-v1', 1, 0.01, false, '一期默认（测试也用同一行，口径与线上一致）')
        ON CONFLICT (version) DO UPDATE SET slippage_ticks = EXCLUDED.slippage_ticks,
          tick_size = EXCLUDED.tick_size, part_fill = EXCLUDED.part_fill
        """
    )
    return code


def order_body(project_id, *, code="600519", side="buy", qty=100, price="10.00",
               price_type="limit", idempotency_key=None, expected_version=None, **kw):
    """一笔委托的请求体。**没有 snapshot 字段** —— 快照由服务端取。"""
    body = {
        "project_id": project_id,
        "code": code,
        "side": side,
        "qty": qty,
        "price_type": price_type,
    }
    if price_type == "limit":
        body["limit_price"] = price
    if idempotency_key is not None:
        body["idempotency_key"] = idempotency_key
    if expected_version is not None:
        body["expected_version"] = expected_version
    body.update(kw)
    return body


def relax_staleness(seconds: int = 365 * 24 * 3600 * 100) -> str:
    """把「快照新鲜度上限」放大，让固定日期的报价不会因为「距今太久」被判 stale。

    测试里用的是固定日期（比如 2026-10-02 10:00），而 `capture` 拿真时钟比新旧 ——
    不放大阈值的话，这些用例到了明天就会莫名其妙全变成「断流」。
    """
    previous = os.environ.get("PAPER_SNAPSHOT_STALE_SECONDS")
    os.environ["PAPER_SNAPSHOT_STALE_SECONDS"] = str(seconds)
    return previous if previous is not None else "\0"


def restore_staleness(previous: str) -> None:
    if previous == "\0":
        os.environ.pop("PAPER_SNAPSHOT_STALE_SECONDS", None)
    else:
        os.environ["PAPER_SNAPSHOT_STALE_SECONDS"] = previous


def _salt(project_id: str, salt: int) -> int:
    """项目 id 的随机十六进制 + 一个盐。不同的盐 = 不同的日期与时刻。

    盐是必要的：同一个项目在一个用例里可能换好几次报价（挂单 → 再撮合），
    换一次就得换一个 (日期, 秒)，否则会命中上一条快照、拿到旧价。
    """
    return int(project_id.rsplit("_", 1)[-1], 16) + salt * 7919


# 日期池：1990-01-01 起 12000 天（约 33 年）× 14400 个交易时段内秒 ≈ 1.7 亿个槽位。
# 项目 id 是随机 uuid，跨用例、跨轮次撞同一行的概率可以忽略。
_BASE_DAY = date(1990, 1, 1)
_DAY_SPAN = 12000


def _moment(project_id: str, salt: int):
    h = _salt(project_id, salt)
    day = _BASE_DAY + timedelta(days=h % _DAY_SPAN)
    offset = (h // _DAY_SPAN) % 14400                  # 上午 7200 秒 + 下午 7200 秒
    if offset < 7200:
        seconds = 9 * 3600 + 30 * 60 + offset          # 09:30:00 起
    else:
        seconds = 13 * 3600 + (offset - 7200)          # 13:00:00 起
    hh, rem = divmod(seconds, 3600)
    mm, ss = divmod(rem, 60)
    return day, f"{hh:02d}:{mm:02d}:{ss:02d}"


def uniq_date(project_id: str, salt: int = 0) -> str:
    return _moment(project_id, salt)[0].isoformat()


def uniq_when(project_id: str, salt: int = 0) -> str:
    """给一个测试项目派一个**独有的报价时刻**。

    为什么需要它：`fin_snapshot` 的编号只精确到秒（`SNAP-日期-时刻-代码`），
    而快照表是**全局**的、只追加的。两个用例如果用同一个 (代码, 秒)，
    后一个会命中 `ON CONFLICT DO NOTHING` 拿到前一个的价格 —— 测试之间就串了。
    从 project_id（随机 uuid）派生，跨进程、跨轮次都不会撞。
    """
    day, clock = _moment(project_id, salt)
    return f"{day.isoformat()}T{clock}+08:00"


def prime_quote(pg, project_id: str, *, salt: int = 1, when: Optional[str] = None, **kw):
    """给一个用例换一张报价：种下**该日期**的日历，再装上报价源。

    换一个新日期而不是只换秒，是为了让这张快照与别的用例、别的轮次都不撞
    （快照只追加、全库共享，撞了就会拿到别人的价格）。
    """
    when = when or uniq_when(project_id, salt)
    seed_reference(pg, code=kw.get("code", "600519"), trade_date=when[:10])
    pg.connection.commit()
    return install_quote_source(when=when, **kw)
