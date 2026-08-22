"""quant 每日调度 · Phase B B4
(见 doc/开源hunter-community/参考/11量化策略/03_20260817_phase-b-detailed-plan.md §B4)

每交易日 17:00 CST · hs300 · 16 因子全算 → factor_value upsert

用法:main.py lifespan 里调 register_scheduler(sched)
(复用已有 AsyncIOScheduler · 避免多实例)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from app.services.database import get_conn
from app.services.quant import factor_engine
from app.services.quant import universe as _uv

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


def daily_local_pipeline() -> dict:
    """**不需要任何 key 的每日流水线** —— 开源实例靠它自给自足。

    背景:开源版 `.env` 默认 `HUNTER_MINIMAL_BOOT=1`,启动时跳过**全部**
    后台定时任务。它的本意是"跳过需要外部凭据的任务",但量化的因子重算
    被一起关掉了 —— 于是因子数据停在最后一次手动回填,永远不会更新,
    而界面上没有任何地方提示这一点(实测:停在 2026-07-15,过期一个多月)。

    可是这几步**根本不需要凭据**:

        ① 成分股      AKShare 直连
        ② 个股日线    腾讯直连(或用户自己配的源)
        ③ 指数日线    腾讯直连
        ④ 技术因子    只读本地 klines

    所以把它们单独拎出来,放在 MINIMAL_BOOT 那道门之外。

    每一步失败都不阻断后面 —— 拿不到指数日线不该导致因子不算。
    但**每一步的结果都要 log**,静默跳过的结果是第二天看起来一切正常
    而表里什么都没有。

    ## 用谁的数据源

    **固定走免费公开源(腾讯 / AKShare),不读任何用户配置。**

    `local_kline.fetch_daily()` 支持"用户源优先",但这里刻意不传 user_id:
    这是个全局定时任务,不属于任何用户,拿某一个人的 key 去刷全量数据
    既不合理也不安全(他的额度、他的授权)。

    用户想用自己的源,现在的方式是手工跑
    `scripts/backfill_klines_local.py <月数> <user_id>`。
    以后要做成自动的,得先想清楚多用户下该用谁的 —— 那是产品决策,
    不是在这里随手 `user_id=某个人` 能定的。
    """
    from datetime import date as _date
    out: dict = {}
    today = _date.today()

    # ① 股票池
    try:
        codes = _uv.query_current("000300")
        if len(codes) < 100:
            codes = _uv.query_current("000300") if _uv.seed_current("000300") else []
        out["universe"] = len(codes)
    except Exception as e:                                    # noqa: BLE001
        log.error("[quant.local] 股票池失败: %s", e)
        codes = []
        out["universe"] = 0
    if not codes:
        log.error("[quant.local] 没有股票池 · 后面全部跳过")
        return out

    # ② 个股日线 · 只补最近一段,不是全量重拉
    try:
        from app.services.quant import local_kline
        from app.services.database import get_conn
        lo = today - timedelta(days=45)
        ok = 0
        conn = get_conn(); cur = conn.cursor()
        try:
            for code in codes:
                rows = local_kline.fetch_daily(code, lo, today)
                if not rows:
                    continue
                for r in rows:
                    cur.execute(
                        """INSERT INTO klines (code, period, ts, open, high, low, close, volume)
                           VALUES (%s,'daily',%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (code, period, ts) DO UPDATE
                             SET open=EXCLUDED.open, high=EXCLUDED.high,
                                 low=EXCLUDED.low, close=EXCLUDED.close,
                                 volume=EXCLUDED.volume""",
                        (code, r["ts"], r["open"], r["high"], r["low"],
                         r["close"], int(r["volume"] or 0)))
                conn.commit()
                ok += 1
        finally:
            cur.close(); conn.close()
        out["klines"] = ok
        log.info("[quant.local] 日线更新 %d/%d 只", ok, len(codes))
    except Exception as e:                                    # noqa: BLE001
        log.error("[quant.local] 日线更新失败: %s", e)
        out["klines"] = 0

    # ③ 指数日线(基准)
    try:
        from app.services.quant import index_kline
        n = 0
        for ic in ("000300", "000905", "399006"):
            r = index_kline.backfill(ic, today - timedelta(days=45), today)
            n += r.get("written", 0)
        out["index_klines"] = n
    except Exception as e:                                    # noqa: BLE001
        log.error("[quant.local] 指数日线失败: %s", e)
        out["index_klines"] = 0

    # ④ 技术因子
    try:
        res = {k: factor_engine.compute_and_store(k, codes, today)
               for k in factor_engine.LOCAL_ONLY}
        out["factors"] = res
        log.info("[quant.local] 技术因子 @ %s · %s", today, res)
    except Exception as e:                                    # noqa: BLE001
        log.error("[quant.local] 因子计算失败: %s", e)
        out["factors"] = {}
    return out


def weekly_akshare_factors() -> dict:
    """基本面因子 · 每周一次 —— **不需要 key,只是慢**。

    这 10 个因子(PE/PB/ROE/毛利率/营收同比…)走 AKShare 直连,和技术因子
    一样不需要任何凭据,但 AKShare 对财务接口有限流:300 只跑一个日期是
    分钟级。放进每日任务会让本来几秒的流水线变成几十分钟,而且一旦卡住,
    连技术因子也跟着不更新 —— 所以单独排。

    为什么必须排上:因子是选股打分的输入。基本面这 10 个停更,策略就只能
    靠技术面,而用户在界面上选了 PE、营收同比这些,拿到的是一份
    "少了一半权重"的回测。

    只算**当天**一个日期。历史回填用 scripts/backfill_akshare_factors.py。
    """
    from datetime import date as _date
    today = _date.today()
    try:
        codes = _uv.query_current("000300")
    except Exception as e:                                    # noqa: BLE001
        log.error("[quant.akshare] 取股票池失败: %s", e)
        return {}
    if not codes:
        log.error("[quant.akshare] 股票池为空 · 跳过")
        return {}

    out = {}
    for k in factor_engine.AKSHARE_ONLY:
        try:
            out[k] = factor_engine.compute_and_store(k, codes, today)
        except Exception as e:                                # noqa: BLE001
            # 一个因子失败不带走其余 —— 但要打出来。AKShare 限流是常态,
            # 静默跳过的结果是下周看起来"跑过了"而表里还是空的
            log.error("[quant.akshare] %s 失败: %s %s", k, type(e).__name__, str(e)[:80])
            out[k] = 0
    log.info("[quant.akshare] 基本面因子 @ %s · %s", today, out)
    return out


def register_local(scheduler):
    """只注册不需要凭据的任务。

    两个任务都不碰我们自己的服务:

      每日 17:10  daily_local_pipeline    成分股/日线/指数/技术因子 · 几秒
      每周六 02:00 weekly_akshare_factors  基本面因子 · 分钟到小时级

    周六凌晨:这个任务要跑很久,放在非交易日的低峰,跑挂了也有一整个
    周末可以重试,不影响周一开盘前的数据。
    """
    from apscheduler.triggers.cron import CronTrigger
    scheduler.add_job(
        daily_local_pipeline,
        CronTrigger(hour=17, minute=10),
        id="quant_local_daily", replace_existing=True,
    )
    scheduler.add_job(
        weekly_akshare_factors,
        CronTrigger(day_of_week="sat", hour=2, minute=0),
        id="quant_akshare_weekly", replace_existing=True,
    )


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
