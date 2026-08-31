"""Admin ETL 手动触发端点 · 供 demo + 补跑。

⚠ 适配说明:klines_etl.py(小王 bc19dc0)对外只有同步的
``run_market(market, bars, max_priority, limit)`` 和 ``health()``,
**没有** ``daily_etl`` / ``target_date`` —— run_market 每次从三源直拉最近
MAX_BARS(800)根日线并 UPSERT,天然覆盖当日最新一根,不按日期取数。
该文件属小王代码,此处只 import 不改,故按其真实签名暴露端点。
"""
import asyncio
from typing import Literal

from fastapi import APIRouter, Query, Request

from app.services.data import klines_etl
# 管理员判定沿用回测配置同一口径(JWT role==ADMIN 或 HUNTER_ADMIN_EMAILS 白名单)
from app.routers.backtest import _require_admin

router = APIRouter(prefix="/admin/etl", tags=["admin-etl"])


@router.post("/run-market")
async def run_market(
    request: Request,
    market: Literal["cn", "hk", "us"],
    limit: int | None = Query(None, description="只跑股票池前 N 只 · 默认全量 · demo 建议给小值"),
    bars: int = Query(klines_etl.MAX_BARS, description="每只拉多少根日线 · 默认 800"),
):
    """手动触发某市场 ETL · 供 demo + 补跑。

    run_market 是阻塞函数(逐只 sleep + 网络 IO),放线程里跑,别堵事件循环。
    """
    _require_admin(request)
    result = await asyncio.to_thread(
        klines_etl.run_market, market, bars, 100, limit
    )
    return result


@router.get("/health")
async def etl_health(request: Request):
    """暴露最近数据新鲜度 + 最近 3 次 run · 供监控告警。"""
    _require_admin(request)
    return await asyncio.to_thread(klines_etl.health)
