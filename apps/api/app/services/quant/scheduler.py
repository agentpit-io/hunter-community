"""quant 每日调度 · Phase B B4
(见 doc/开源hunter-community/参考/11量化策略/03_20260817_phase-b-detailed-plan.md §B4)

每交易日 17:00 CST · hs300 · 16 因子全算 → factor_value upsert

用法:main.py lifespan 里调 register_scheduler(sched)
(复用已有 AsyncIOScheduler · 避免多实例)
"""
from __future__ import annotations

import asyncio
import logging
import time
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
    from app.services.quant import init_state as _st
    out: dict = {}
    today = _date.today()

    # ① 要更新哪些票 —— **用户下过什么就更新什么**
    #
    # 原来这里发现池子空就自己去 seed 沪深300 + 中证500,然后开始下 800 只。
    # 那是"替用户决定要什么"的另一种形式 —— 和开机自动跑一个毛病。
    # 现在:用户一只都没下过,就什么都不做,而且这不是错误,是正常状态。
    _st.step("检查已有数据", 0)
    try:
        codes = _uv.covered_codes()
        out["universe"] = len(codes)
    except Exception as e:                                    # noqa: BLE001
        log.error("[quant.local] 读已有数据失败: %s", e)
        codes = []
        out["universe"] = 0
    if not codes:
        log.info("[quant.local] 用户还没下载任何数据 · 本次不做任何事"
                 "(到「数据」页选范围下载后,这里会自动跟着更新)")
        out["skipped"] = "no_data_yet"
        _st.step("完成", 4)
        return out

    # ② 个股日线 · 只补最近一段,不是全量重拉
    _st.step("拉取 K 线", 1, f"0/{len(codes)}")
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
                if ok % 20 == 0:
                    _st.detail(f"{ok}/{len(codes)}")
                # 对上游客气一点 —— 回填脚本有这个 sleep 所以 800/800 全成功,
                # 这里原来没有,结果清一色 ReadTimeout
                time.sleep(0.15)
        finally:
            cur.close(); conn.close()
        out["klines"] = ok
        if ok == 0:
            # **一只都没拿到不是"跑完了"**。原来这里只 log 一句就继续往下走,
            # 用户会看到进度条走完然后什么数据都没有
            log.error("[quant.local] 日线一只都没拿到(%d 只全失败)—— "
                      "上游可能在限流,稍后重试", len(codes))
            out["klines_error"] = f"{len(codes)} 只全部失败"
        elif ok < len(codes) * 0.5:
            log.warning("[quant.local] 日线只拿到 %d/%d 只 · 覆盖不足一半", ok, len(codes))
            out["klines_error"] = f"只拿到 {ok}/{len(codes)}"
        else:
            log.info("[quant.local] 日线更新 %d/%d 只", ok, len(codes))
    except Exception as e:                                    # noqa: BLE001
        log.error("[quant.local] 日线更新失败: %s", e)
        out["klines"] = 0

    # ③ 指数日线(基准)
    _st.step("拉取指数(基准)", 2)
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
    _st.step("计算因子", 3, f"0/{len(factor_engine.LOCAL_ONLY)}")
    try:
        res = {}
        for _i, k in enumerate(factor_engine.LOCAL_ONLY, 1):
            _st.detail(f"{_i}/{len(factor_engine.LOCAL_ONLY)} · {k}")
            res[k] = factor_engine.compute_and_store(k, codes, today)
        out["factors"] = res
        log.info("[quant.local] 技术因子 @ %s · %s", today, res)
    except Exception as e:                                    # noqa: BLE001
        log.error("[quant.local] 因子计算失败: %s", e)
        out["factors"] = {}
    _st.step("完成", 4)
    return out


def run_initial_setup() -> dict:
    """⚠ **已被「数据」页的下载任务取代 · 保留只为兼容老的 POST /quant/init**

    它原来的作用是"库是空的就自己去 seed 沪深300 + 中证500 并下 800 只"。
    老板砍掉了这个行为:「刚下载启动容器自动跑不太好,用户都不知道你就
    占用他的资源很不好」。

    而且现在 `covered_codes()` 读的是 `data_coverage`(用户下过什么),
    所以在一个没下过数据的实例上,**这个函数什么都不会做** ——
    这是有意的,不是 bug。

    要下数据请走「数据」页:用户自己选范围、时长、要不要财报。
    等第 3 步的下载任务系统上线后,这个函数和 /quant/init 一起删掉。

    历史因子那段逻辑仍然有价值(只算当天一个截面的话回测一期都选不出票),
    下载任务实现时要把它搬过去。
    """
    from app.services.quant import init_state as _st
    if not _st.begin():
        return {"skipped": "已经在跑"}
    try:
        out = daily_local_pipeline()
        # 补历史因子 —— 回测要用
        _st.step("补历史因子(回测要用)", 3, "")
        try:
            from datetime import date as _date, timedelta as _td
            from app.services.quant import backtest_engine as _bt
            codes = _uv.covered_codes()
            end = _date.today(); start = end - _td(days=370)
            days = sorted(set(_bt._rebalance_dates(start, end, "W"))
                          | set(_bt._rebalance_dates(start, end, "M")))
            for i, d in enumerate(days, 1):
                _st.detail(f"{i}/{len(days)} · {d}")
                for k in factor_engine.LOCAL_ONLY:
                    factor_engine.compute_and_store(k, codes, d)
            out["history_dates"] = len(days)
        except Exception as e:                                # noqa: BLE001
            log.error("[quant.init] 补历史因子失败: %s", e)
        # 补历史因子是第 4 步 —— daily_local_pipeline 结束时把 steps_done
        # 设成了 4,这里又覆盖回 3,结果跑完了进度条停在 75%
        _st.step("完成", 4)
        # 跑完了不等于成功。K 线一只没拿到、因子一行没写 —— 这两种情况下
        # 显示"初始化完成"会让用户去点回测,然后得到"选不出股票",
        # 而他刚看着进度条走完
        if out.get("klines_error"):
            _st.finish(f"K 线没补上:{out['klines_error']} · 上游可能在限流,"
                       f"稍后在设置里重新同步")
            log.error("[quant.init] 初始化未达成 · %s", out)
            return out
        _st.finish()
        log.info("[quant.init] 首次初始化完成 · %s", out)
        return out
    except Exception as e:                                    # noqa: BLE001
        log.error("[quant.init] 首次初始化失败: %s", e)
        _st.finish(str(e)[:200])
        return {"error": str(e)[:200]}


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
    # **只算有财报覆盖的票**。用 kline 覆盖的话,会给那些用户只下了日线、
    # 明确没要财报的股票去拉财报 —— 那是替他做决定,而且很慢(8.6 秒/只)
    try:
        codes = _uv.covered_codes_financial()
    except Exception as e:                                    # noqa: BLE001
        log.error("[quant.akshare] 读财报覆盖失败: %s", e)
        return {}
    if not codes:
        log.info("[quant.akshare] 还没有下载过财报数据 · 本次跳过"
                 "(到「数据」页选「日线 + 财报」下载后,这里会自动跟着更新)")
        return {"skipped": "no_financial_data"}

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
