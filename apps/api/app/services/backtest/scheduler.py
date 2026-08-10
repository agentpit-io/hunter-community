"""回测流水线定时调度 —— 每个交易日收盘后跑一次。

时间: 北京时间 16:30(A股 15:00 收盘, 留足行情落库时间)
范围: 全部用户自选股(去重), 这是真正有人关心的股票池
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))
RUN_HOUR = 16
RUN_MINUTE = 30
CHECK_INTERVAL = 600      # 10分钟检查一次


def collect_symbols(scope: str = "watchlist") -> list[str]:
    """收集要跑预测的股票池。watchlist=全部用户自选的A股(去重)"""
    from app.services.database import get_conn
    conn = get_conn(); cur = conn.cursor()
    if scope == "hot":
        cur.execute("""SELECT code, count(*) c FROM stocks
                       WHERE enabled = TRUE AND (market IS NULL OR market = 'A')
                       GROUP BY code ORDER BY c DESC LIMIT 200""")
    else:
        cur.execute("""SELECT DISTINCT code FROM stocks
                       WHERE enabled = TRUE AND (market IS NULL OR market = 'A')""")
    rows = [r[0] for r in cur.fetchall() if r[0]]
    conn.close()
    return rows


async def run_loop():
    """常驻循环: 每交易日按配置时间跑一次流水线(当天只跑一次)。
    运行时间、股票池、是否启用 全部从 backtest_config 读取, admin 后台可改。"""
    from app.services.backtest import jobs
    from app.services.backtest.config import get_config, resolve_pool
    log.info("[backtest] scheduler started")
    last_run_date = None
    await asyncio.sleep(60)   # 起服后缓一下

    while True:
        try:
            cfg = await asyncio.to_thread(get_config)
            if not cfg.get("enabled", True):
                await asyncio.sleep(CHECK_INTERVAL)
                continue
            hh = int(cfg.get("run_hour") or RUN_HOUR)
            mm = int(cfg.get("run_minute") or RUN_MINUTE)
            now = datetime.now(CST)
            today = now.date()
            is_weekday = now.weekday() < 5
            past_time = (now.hour > hh) or (now.hour == hh and now.minute >= mm)
            if is_weekday and past_time and last_run_date != today:
                block = jobs.trading_session_guard()
                if block:
                    log.info("[backtest] 跳过本次: %s", block)
                    await asyncio.sleep(CHECK_INTERVAL)
                    continue
                last_run_date = today
                symbols = await asyncio.to_thread(resolve_pool, cfg)
                if symbols:
                    # 记录用户池占比: 逼近时间窗上限时(约800只)要提前扩并发或分批
                    try:
                        from app.services.backtest.user_store import all_user_symbols
                        u = len(await asyncio.to_thread(all_user_symbols))
                    except Exception:
                        u = -1
                    log.info("[backtest] 开始每日流水线, 池=%s 共%d只(其中用户池贡献%s只), 预计%d分钟",
                             cfg.get("pool_mode"), len(symbols),
                             u if u >= 0 else "?", max(1, len(symbols) // 6))
                    result = await jobs.daily_pipeline(symbols)
                    log.info("[backtest] 流水线完成: %s", result)
                else:
                    log.info("[backtest] 股票池为空, 跳过")
        except Exception as e:
            log.warning("[backtest] scheduler error: %s", e)
        await asyncio.sleep(CHECK_INTERVAL)
