"""地缘冲突数据读取(financedata geo_* 表, 读库only)
采集端: finance-data collector/geo_daily.py (每日cron 03:20 UTC)
  geo_divergence_daily — 背离度 = BWET(油运费ETF)20日收益 - 船东股组合20日收益
  geo_jwc_event        — Lloyd's JWC 战争险 Listed Areas 通函
"""
import logging
from app.services.gm.findata_db import _conn

log = logging.getLogger(__name__)


def divergence(days: int = 60) -> dict | None:
    """近N日背离度序列(时间升序) + 最新值"""
    try:
        conn = _conn(); cur = conn.cursor()
        cur.execute("""SELECT trade_date, bwet_ret20, basket_ret20, divergence_pp, regime
                       FROM geo_divergence_daily ORDER BY trade_date DESC LIMIT %s""", (days,))
        rows = cur.fetchall(); conn.close()
        if not rows:
            return None
        rows.reverse()
        series = [{"date": r[0].isoformat(), "bwet": float(r[1]), "basket": float(r[2]),
                   "div": float(r[3]), "regime": r[4]} for r in rows]
        return {"latest": series[-1], "series": series}
    except Exception as e:
        log.warning("geo divergence read failed: %s", e)
        return None


def jwc_events(limit: int = 6) -> list[dict]:
    try:
        conn = _conn(); cur = conn.cursor()
        cur.execute("""SELECT circular_id, title, url, published
                       FROM geo_jwc_event ORDER BY circular_id DESC LIMIT %s""", (limit,))
        rows = cur.fetchall(); conn.close()
        return [{"id": r[0], "title": r[1], "url": r[2],
                 "published": r[3].isoformat() if r[3] else None} for r in rows]
    except Exception as e:
        log.warning("geo jwc read failed: %s", e)
        return []
