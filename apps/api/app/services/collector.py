import asyncio
import json
import redis
from datetime import datetime, time, timezone, timedelta
from loguru import logger
from app.services.database import get_stocks
from app.services.finance_data_client import get_quote, get_reliable_close, register_stocks

_redis = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
_task = None

# A 股 + 港股交易时段以北京时间 (CST) 判定；服务器若为 UTC 也能正确比较
CST = timezone(timedelta(hours=8))


def _today_cst() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d")


def _is_trading_time() -> bool:
    now = datetime.now(CST)
    if now.weekday() >= 5:
        return False
    t = now.time()
    morning   = time(9, 25) <= t <= time(11, 35)
    afternoon = time(12, 55) <= t <= time(15, 5)
    hk_session = time(9, 25) <= t <= time(16, 5)
    return morning or afternoon or hk_session


_STALE_THRESHOLD_DAYS = 3  # 超过 3 个自然日无更新 → 判定为陈旧，拒绝写入 Redis


def _is_severely_stale(ts_str: str, today: str) -> bool:
    """判定 quote 是否严重陈旧（相差 > 3 自然日）。"""
    try:
        ts_date = datetime.strptime(ts_str[:10], "%Y-%m-%d").date()
        today_date = datetime.strptime(today, "%Y-%m-%d").date()
        return (today_date - ts_date).days > _STALE_THRESHOLD_DAYS
    except (ValueError, TypeError):
        return False


def _refresh_one(code: str, today: str) -> dict | None:
    """获取最新行情；若 quote ts 不是今天，自动 kline 兜底。
    V2：severe stale 保护——如果 quote/kline 都无法给出近 3 天内的数据，
    不写入 Redis，避免上游对个别股票停更时把很久以前的数据当"最新"展示。"""
    data = get_quote(code)
    if data and data.get("ts", "")[:10] == today:
        return data
    # quote 接口数据过期 → kline 兜底（含 akshare 二级兜底）
    kq = get_reliable_close(code, today)
    if kq:
        logger.info("📈 kline 补齐今日收盘 {} → {}", code, kq.get("price"))
        return kq
    # 二级兜底也失败：判断旧 quote 严重陈旧性
    if data:
        ts = data.get("ts", "")
        if _is_severely_stale(ts, today):
            logger.warning(
                "🚫 上游数据严重陈旧（{}，超 {} 天未更新）→ 拒绝写入 Redis {} ",
                ts[:10], _STALE_THRESHOLD_DAYS, code,
            )
            return None
        logger.debug("⚠️ quote/kline 均非今日，维持近期缓存 {} (ts={})", code, ts[:10])
    return data


async def _collect_loop():
    loop = asyncio.get_running_loop()
    logger.info("Collector started (finance-data backend, kline fallback enabled)")
    while True:
        try:
            today = _today_cst()
            stocks = get_stocks()
            register_stocks(stocks)
            for stock in stocks:
                data = await loop.run_in_executor(None, _refresh_one, stock["code"], today)
                if data:
                    _redis.set(f"quote:{stock['code']}", json.dumps(data))
                    logger.debug("Updated quote: {} {} ts={}", stock["code"], data.get("price"), data.get("ts", "")[:10])
                await asyncio.sleep(0.5)
            interval = 30 if _is_trading_time() else 600
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Collector error: {} {}", type(e).__name__, e)
            await asyncio.sleep(30)


async def start_collector():
    global _task
    _task = asyncio.create_task(_collect_loop())


async def stop_collector():
    if _task:
        _task.cancel()
