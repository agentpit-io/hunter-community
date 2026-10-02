"""内网 · 取数触发与交易日历（给 `fin-worker` 的 Temporal 工作流调用）。

**这是 `01方案 §5.3`「上层调度只有一个权威」的落点。** M4 之前，K 线 ETL 由
`main.py` 里一个进程内 asyncio 循环按窗口触发（M0 报告 §2.1）。现在那个循环**已删除**，
改由 Temporal 工作流打这里的 `POST /internal/etl/run-market` —— 而
**拉数的实现函数（`klines_etl.run_market`）一个字符都没改**。
改的是「谁决定什么时候拉」，不是「怎么拉」。

交易日历同理：`GET /internal/calendar/trading-days` 用 akshare 的 A 股交易日历
（**前视**，含节假日与未来日期）—— 从 klines 反推的日历只能回答过去，
回答不了「今天早上是不是交易日」。fin-worker 的 `preopen` 时点把结果写进
`fin_market_calendar`，六个时点与 ETL 都读它。

鉴权与 `/api/internal/*` 其余端点同一把口令（`X-Hunter-Internal-Key`）。
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger

from app.services.data import klines_etl

router = APIRouter(prefix="/internal", tags=["internal-etl"])

_INTERNAL_KEY = os.getenv("HUNTER_INTERNAL_KEY", "")


def _auth(request: Request) -> None:
    key = request.headers.get("X-Hunter-Internal-Key", "")
    if not _INTERNAL_KEY or key != _INTERNAL_KEY:
        raise HTTPException(401, "internal auth failed")


@router.post("/etl/run-market")
async def run_market(
    request: Request,
    market: Literal["cn", "hk", "us"],
    limit: Optional[int] = Query(None, description="只跑股票池前 N 只 · 默认全量"),
    bars: int = Query(klines_etl.MAX_BARS, description="每只拉多少根日线 · 默认 800"),
):
    """由 Temporal 工作流触发某市场的 K 线 ETL。

    **复用 `klines_etl.run_market`，不重写**（`run_market` 是阻塞函数，放线程里跑）。
    并发由调用方（Temporal Schedule 的 `SKIP` 策略）保证，这里不额外加锁。
    """
    _auth(request)
    logger.info("[internal.etl] Temporal 触发 · market={} limit={} bars={}", market, limit, bars)
    result = await asyncio.to_thread(klines_etl.run_market, market, bars, 100, limit)
    return result


@router.get("/calendar/trading-days")
async def trading_days(
    request: Request,
    market: Literal["a", "hk", "us"] = Query("a"),
    start: date = Query(...),
    end: date = Query(...),
):
    """区间内的交易日（升序 `YYYY-MM-DD`）。

    目前只有 **A 股**有可信的前视日历（akshare `tool_trade_date_hist_sina`，
    覆盖到当年年底）。港股 / 美股**没有**，于是直接报错 —— 拿 A 股日历冒充
    港股/美股日历就是编数据（`总控规则 §六-1`）。补真实日历是 M7 的事。
    """
    _auth(request)
    if market != "a":
        raise HTTPException(
            501,
            f"目前只有 A 股（market=a）有前视交易日历；{market} 的日历数据源待接（M7）。"
            "不许用 A 股日历顶替。",
        )
    if end < start:
        raise HTTPException(400, "end 必须不早于 start")

    def _fetch() -> list[str]:
        import akshare as ak

        df = ak.tool_trade_date_hist_sina()
        days = []
        for value in df["trade_date"].tolist():
            d = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
            if start <= d <= end:
                days.append(d.isoformat())
        return sorted(days)

    try:
        days = await asyncio.to_thread(_fetch)
    except Exception as exc:  # noqa: BLE001 —— 拉不到就如实报错，不返回空日历
        logger.error("[internal.calendar] akshare 交易日历拉取失败：{}", exc)
        raise HTTPException(503, f"交易日历数据源不可用：{exc}") from exc
    return {"market": market, "start": start.isoformat(), "end": end.isoformat(),
            "trading_days": days, "count": len(days), "source": "akshare.tool_trade_date_hist_sina"}
