"""quant 每日调度 · Phase B B4
(见 doc/开源hunter-community/参考/11量化策略/03_20260817_phase-b-detailed-plan.md §B4)

每交易日 17:00 CST · hs300 · 16 因子全算 → factor_value upsert

用法:main.py lifespan 里调 register_scheduler(sched)
(复用已有 AsyncIOScheduler · 避免多实例)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

from app.services.database import get_conn
from app.services.quant import factor_engine

log = logging.getLogger(__name__)


def _get_hs300_codes() -> list[str]:
    """兜底:stocks 表所有 enabled A 股(v2 · 独立 index_component 表)"""
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT DISTINCT code FROM stocks WHERE enabled AND market='A'")
    codes = [r[0] for r in cur.fetchall()]
    cur.close(); conn.close()
    return codes


def daily_recompute():
    """算全启用因子 · 供 APScheduler 调"""
    codes = _get_hs300_codes()
    if not codes:
        log.warning("[quant.scheduler] 无 hs300 codes · 跳过")
        return {}
    trade_date = date.today()
    log.info("[quant.scheduler] 开跑 %d 只 × 16 因子 @ %s", len(codes), trade_date)
    result = factor_engine.compute_daily(codes, trade_date)
    total = sum(result.values())
    log.info("[quant.scheduler] 完成 · 各因子行数 %s · 合计 %d", result, total)
    return result


def daily_ic_recompute():
    """D-2 · 每日 17:30 CST · 算全启用因子 × [5,10,20] horizon IC"""
    from datetime import date
    from app.services.quant import ic_engine
    today = date.today()
    log.info("[quant.scheduler] IC 重算 @ %s", today)
    result = ic_engine.compute_daily(today, "hs300", horizons=[5, 10, 20])
    total = sum(result.values())
    log.info("[quant.scheduler] IC 完成 · 写入 %d 行", total)
    return result


def register(scheduler):
    """外部传入已有 AsyncIOScheduler · 我只加 job(不 start · 由 caller 决定)"""
    from apscheduler.triggers.cron import CronTrigger
    scheduler.add_job(
        lambda: asyncio.create_task(asyncio.to_thread(daily_recompute)),
        CronTrigger(hour=17, minute=0),   # 17:00 CST · 收盘后 30 分钟
        id="quant_daily_recompute",
        replace_existing=True,
    )
    # D-2 · IC 30 分钟后跑(等 factor_value 写完)
    scheduler.add_job(
        lambda: asyncio.create_task(asyncio.to_thread(daily_ic_recompute)),
        CronTrigger(hour=17, minute=30),
        id="quant_daily_ic",
        replace_existing=True,
    )
    log.info("[quant.scheduler] APScheduler 已注册:17:00 factor + 17:30 IC")
