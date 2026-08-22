"""给单只股票按需补数据 —— 用户加自选时用。

## 为什么需要

因子只算「核心池」(沪深300 ∪ 中证500 = 800 只)。用户往自选里加一只
核心池外的股票(比如某只小盘股),它在 `factor_value` 里一条记录都没有:

    自选 10 只 → 打分时 7 只没数据 → 只选出 3 只

而界面不会说为什么,用户以为是自己权重配错了。

全 A 股一次性算掉不现实:K 线实测 1.81 秒/只,5400 只要 2.7 小时;
基本面因子 8.6 秒/只,单个因子就 12.9 小时。

所以改成**加一只补一只** —— 单只票拉 K 线 + 算 8 个技术因子,
在用户点「加入自选」那一刻同步做完。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from app.services.database import get_conn

log = logging.getLogger(__name__)


def has_factor_data(codes: list[str], within_days: int = 45) -> set[str]:
    """这些票里,哪些**已经有**可用的因子数据。"""
    if not codes:
        return set()
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            """SELECT DISTINCT code FROM factor_value
                WHERE code = ANY(%s) AND trade_date >= %s""",
            (codes, date.today() - timedelta(days=within_days)))
        return {r[0] for r in cur.fetchall()}
    finally:
        cur.close(); conn.close()


def ensure_stock(code: str, months: int = 14, user_id: str | None = None) -> dict:
    """把一只票补齐到「能参与选股」的状态:K 线 + 8 个技术因子。

    只补技术因子。基本面走 AKShare,实测 8.6 秒/只 —— 挂在用户点击的
    请求里会让他等十几秒。它们由每周任务覆盖核心池,自选里的票
    暂时只有技术因子,这一点 UI 要说清楚。

    失败**返回结构化结果而不是抛** —— 加自选这个动作本身不该因为
    补数据失败而回滚,用户加的票还是要加进去的。
    """
    from app.services.quant import factor_engine, local_kline

    end = date.today()
    start = end - timedelta(days=months * 31)
    try:
        rows = local_kline.fetch_daily(code, start, end, user_id)
    except Exception as e:                                    # noqa: BLE001
        return {"code": code, "ok": False, "why": f"拉 K 线失败: {type(e).__name__}"}
    if not rows:
        return {"code": code, "ok": False, "why": "拿不到这只股票的 K 线"}

    conn = get_conn(); cur = conn.cursor()
    try:
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
    except Exception as e:                                    # noqa: BLE001
        conn.rollback()
        return {"code": code, "ok": False, "why": f"K 线入库失败: {type(e).__name__}"}
    finally:
        cur.close(); conn.close()

    # 因子要算在**调仓日**上,只算今天的话回测仍然选不出票
    from app.services.quant import backtest_engine as bt
    days = sorted(set(bt._rebalance_dates(start, end, "W"))
                  | set(bt._rebalance_dates(start, end, "M")))
    n = 0
    for d in days:
        for k in factor_engine.LOCAL_ONLY:
            try:
                n += factor_engine.compute_and_store(k, [code], d)
            except Exception as e:                            # noqa: BLE001
                log.warning("[on_demand] %s %s @ %s 失败: %s", code, k, d, e)
    return {"code": code, "ok": n > 0, "klines": len(rows),
            "factor_rows": n, "dates": len(days),
            "why": "" if n > 0 else "K 线拿到了但因子算不出来(历史可能不够长)"}
