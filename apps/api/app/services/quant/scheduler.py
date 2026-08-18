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


def weekly_report_job():
    """E-2 · 每周一 08:00 CST · 生成质量周报 · 打印 + 写文件"""
    try:
        import sys, os
        script_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "scripts")
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        from quant_weekly_report import send_report
        path = send_report()
        log.info("[quant.scheduler] 周报 → %s", path)
    except Exception as e:
        log.warning("[quant.scheduler] 周报生成失败: %s", e)


def daily_sanity_check():
    """E-1 · 每日 18:00 CST · 检查 factor_value 今日覆盖 · 少于 15 因子 → 告警"""
    from datetime import date
    from app.services.database import get_conn
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(DISTINCT factor_key) FROM factor_value WHERE trade_date = CURRENT_DATE
    """)
    n = cur.fetchone()[0]
    cur.close(); conn.close()
    if n < 15:
        log.warning("[quant.sanity] 🚨 今日 factor_value 只 %d 因子 · APScheduler 可能故障", n)
    else:
        log.info("[quant.sanity] ✅ 今日 %d 因子有增量", n)
    return {"today_factors": n, "ok": n >= 15}


def monthly_index_refresh():
    """E-4 · 每月 1 号 09:00 CST · 3 指数 reconcile"""
    from app.services.quant import universe
    results = {}
    for key in ("000300", "000905", "000852"):
        results[key] = universe.reconcile_current(key)
    log.info("[quant.scheduler] monthly index refresh · %s", results)
    return results


def daily_index_kline() -> dict:
    """每日补指数日线(`_17` §2)。

    只补最近 10 天 —— 全量 5900+ 行首次回填时拉一次就够,
    之后每天只可能多一根。拉全量既慢又给上游添无谓压力。
    """
    from datetime import date, timedelta
    from app.services.quant import index_kline as ik
    out = {}
    end = date.today()
    start = end - timedelta(days=10)
    for code in ik.INDEX_CODES:
        r = ik.backfill(code, start, end)
        out[code] = r.get("written", 0) if not r.get("error") else r["error"]
    log.info("[quant.scheduler] 指数日线: %s", out)
    return out


def register(scheduler):
    """外部传入已有 AsyncIOScheduler · 我只加 job(不 start · 由 caller 决定)"""
    from apscheduler.triggers.cron import CronTrigger
    scheduler.add_job(
        lambda: asyncio.create_task(asyncio.to_thread(daily_recompute)),
        CronTrigger(hour=17, minute=0),   # 17:00 CST · 收盘后 30 分钟
        id="quant_daily_recompute",
        replace_existing=True,
    )
    # `_17` · 指数日线 —— 回测基准的数据源。
    # 16:30 跑,比因子(17:00)早半小时:回测要用它,而它只依赖交易所收盘,
    # 不依赖我们自己的任何计算,没必要排在后面。
    scheduler.add_job(
        lambda: asyncio.create_task(asyncio.to_thread(daily_index_kline)),
        CronTrigger(hour=16, minute=30),
        id="quant_daily_index_kline",
        replace_existing=True,
    )
    # D-2 · IC 30 分钟后跑(等 factor_value 写完)
    scheduler.add_job(
        lambda: asyncio.create_task(asyncio.to_thread(daily_ic_recompute)),
        CronTrigger(hour=17, minute=30),
        id="quant_daily_ic",
        replace_existing=True,
    )
    # E-4 · 每月 1 号 09:00 · 指数成分 reconcile
    scheduler.add_job(
        lambda: asyncio.create_task(asyncio.to_thread(monthly_index_refresh)),
        CronTrigger(day=1, hour=9, minute=0),
        id="quant_monthly_index_refresh",
        replace_existing=True,
    )
    # E-2 · 每周一 08:00 · 数据质量周报
    scheduler.add_job(
        lambda: asyncio.create_task(asyncio.to_thread(weekly_report_job)),
        CronTrigger(day_of_week="mon", hour=8, minute=0),
        id="quant_weekly_report",
        replace_existing=True,
    )
    # E-1 · 每日 18:00 · sanity check
    scheduler.add_job(
        lambda: asyncio.create_task(asyncio.to_thread(daily_sanity_check)),
        CronTrigger(hour=18, minute=0),
        id="quant_daily_sanity",
        replace_existing=True,
    )
    log.info("[quant.scheduler] APScheduler 已注册:17:00 factor + 17:30 IC + 18:00 sanity + 每月 1 号 09:00 指数 + 每周一 08:00 周报")
