"""klines 每日 ETL —— 让回测有数据可跑。

方案见 doc/开源hunter-community/04开源比赛/
      2026-08-31_真生产化技术方案与开发计划.md §2.1

## 为什么这是 P0

生产 klines 表 0 行 → `backtest_engine._trading_days()` 的
`SELECT DISTINCT ts FROM klines` 返回空 → 回测直接报 `no_dates`。

**交易成本模块做得再对,没有 K 线也跑不了一次。** 评委点「跑一次回测」
会撞上空表 —— 这是整条演示链上最硬的一堵墙。

## 源优先级:腾讯第一,不是 akshare

方案文档原本写的是 `akshare → sina → yahoo`。**这里改了顺序**,
依据是 2026-08-28 在国内 IP(腾讯云 134.175.198.216)的实测:

    腾讯直连     30/30 成功 · 中位 70ms · 每只稳定 801 根
    东财(akshare 主要底层)  0/10 · 全部 RemoteDisconnected
    新浪         美股全历史可用 · A 股分钟线可用

而且东财在这个项目里一路不稳(指数日线、行业板块、北交所、美股历史
都栽过同一个错)。**把它放第一位会让 ETL 每天先失败一轮再兜底。**

所以顺序是:

    A 股 / 港股   腾讯(同一个接口,只差前缀)→ akshare 兜底
    美股         新浪(全历史)→ 腾讯报价兜底

## 三个必须记住的坑

1. **腾讯字段顺序是 [date, open, close, high, low, volume]** ——
   close 排在 high 前面。搞错会把收盘价当最高价,而这种错**在图上
   看不出来**:数字都在合理范围,K 线照样能画。

2. **请求上限 800,不是越多越好。** 实测请求 1200/1500 反而只给 641 条,
   请求 3000 直接返回空。

3. **北交所(4/8/92 开头)免费源只给当天一条**,换 bj 前缀、换东财、
   换新浪都一样。跑之前挡掉,不然每轮都有 331 只"失败"。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone

from app.services.database import get_conn

log = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))

# 单次请求的最大 K 线根数 —— 见模块头第 2 条
MAX_BARS = 800
# 每只之间的间隔 · 不限速的话几百只连打必被掐(量化那边实测全 A 股 43% 失败)
SLEEP_SEC = 0.15
# 连续失败这么多只 = 判定被限流
MISS_TRIGGER = 8
# 退避梯度 · 掐是暂时的,等就能恢复
BACKOFF = (30, 120, 300)


# ═══════════════════════════════════════════════════════════
# 股票池
# ═══════════════════════════════════════════════════════════

def seed_universe(codes: list[tuple[str, str, str, int]]) -> int:
    """codes: [(code, market, name, priority)]"""
    conn = get_conn(); cur = conn.cursor()
    n = 0
    try:
        for code, market, name, pri in codes:
            cur.execute(
                """INSERT INTO stock_universe (code, market, name, priority)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (code) DO UPDATE
                     SET market=EXCLUDED.market, name=EXCLUDED.name,
                         priority=LEAST(stock_universe.priority, EXCLUDED.priority)""",
                (code, market, name, pri))
            n += 1
        conn.commit()
    finally:
        cur.close(); conn.close()
    return n


def universe(market: str | None = None, max_priority: int = 100,
             limit: int | None = None) -> list[tuple[str, str]]:
    """返回 [(code, market)] · 按 priority 升序 —— 核心的先跑完。

    全 A 股 5500 只跑一轮是小时级,而演示只需要那几十只。
    分优先级之后,就算后面被限流,前面核心的已经拿到了。
    """
    conn = get_conn(); cur = conn.cursor()
    try:
        sql = ("SELECT code, market FROM stock_universe "
               "WHERE enabled AND priority <= %s")
        args: list = [max_priority]
        if market:
            sql += " AND market = %s"
            args.append(market)
        sql += " ORDER BY priority, code"
        if limit:
            sql += " LIMIT %s"
            args.append(limit)
        cur.execute(sql, args)
        return [(r[0], r[1]) for r in cur.fetchall()]
    finally:
        cur.close(); conn.close()


# ═══════════════════════════════════════════════════════════
# 取数 · 三源兜底
# ═══════════════════════════════════════════════════════════

def _is_unsupported(code: str) -> bool:
    """北交所拿不到历史日线 —— 见模块头第 3 条。跳过并**说明原因**,
    不算失败。算失败的话每轮都报几百次,用户会以为系统坏了。"""
    c = str(code).split(".")[0].zfill(6)
    return c.startswith(("4", "8", "92"))


def _tencent_symbol(code: str, market: str) -> str | None:
    c = str(code).split(".")[0].strip().upper()
    if market == "hk":
        return "hk" + c.zfill(5)
    if market == "us":
        return "us" + c
    if not c.isdigit() or len(c) != 6:
        return None
    return ("sh" if c[0] in "69" else "sz") + c


def fetch_tencent(code: str, market: str, bars: int = MAX_BARS) -> list[dict]:
    """腾讯前复权日线。A 股和港股是**同一个接口**,只差前缀。"""
    import requests
    sym = _tencent_symbol(code, market)
    if not sym:
        return []
    try:
        r = requests.get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            params={"param": f"{sym},day,,,{min(bars, MAX_BARS)},qfq"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        data = (r.json() or {}).get("data") or {}
    except Exception as e:                                    # noqa: BLE001
        log.debug("[etl] 腾讯失败 %s · %s", code, e)
        return []
    node = data.get(sym) or (next(iter(data.values()), {}) if data else {})
    bars_raw = node.get("qfqday") or node.get("day") or []
    out = []
    for b in bars_raw:
        try:
            # ⚠ [date, open, close, high, low, volume] —— close 在 high 前
            out.append({
                "ts": str(b[0])[:10],
                "open": float(b[1]), "close": float(b[2]),
                "high": float(b[3]), "low": float(b[4]),
                "volume": int(float(b[5] or 0)),
                "source": "tencent",
            })
        except (ValueError, IndexError, TypeError):
            continue
    return out


def fetch_sina_us(code: str, bars: int = MAX_BARS) -> list[dict]:
    """美股走新浪 —— 实测给全历史(NVDA 5751 行 · MSFT 9637 行),
    比腾讯只给 2 根慷慨得多(腾讯对美股只有报价没有历史)。"""
    try:
        import akshare as ak
        df = ak.stock_us_daily(symbol=str(code).split(".")[0].upper())
    except Exception as e:                                    # noqa: BLE001
        log.debug("[etl] 新浪美股失败 %s · %s", code, e)
        return []
    if df is None or len(df) == 0:
        return []
    df = df.tail(bars)
    out = []
    for r in df.to_dict(orient="records"):
        try:
            out.append({
                "ts": str(r.get("date"))[:10],
                "open": float(r.get("open") or 0), "high": float(r.get("high") or 0),
                "low": float(r.get("low") or 0), "close": float(r.get("close") or 0),
                "volume": int(float(r.get("volume") or 0)),
                "source": "sina",
            })
        except (ValueError, TypeError):
            continue
    return out


def fetch_akshare_cn(code: str, bars: int = MAX_BARS) -> list[dict]:
    """兜底 —— 走 akshare 的腾讯通道(不是东财,东财实测 0/10)。"""
    try:
        import akshare as ak
        c = str(code).split(".")[0]
        pre = "sh" if c[0] in "69" else "sz"
        df = ak.stock_zh_a_hist_tx(symbol=pre + c, adjust="qfq")
    except Exception as e:                                    # noqa: BLE001
        log.debug("[etl] akshare 兜底失败 %s · %s", code, e)
        return []
    if df is None or len(df) == 0:
        return []
    out = []
    for r in df.tail(bars).to_dict(orient="records"):
        try:
            out.append({
                "ts": str(r.get("date"))[:10],
                "open": float(r.get("open") or 0), "high": float(r.get("high") or 0),
                "low": float(r.get("low") or 0), "close": float(r.get("close") or 0),
                "volume": int(float(r.get("amount") or r.get("volume") or 0)),
                "source": "akshare",
            })
        except (ValueError, TypeError):
            continue
    return out


def fetch_one(code: str, market: str, bars: int = MAX_BARS) -> tuple[list[dict], bool]:
    """按市场选源 · 返回 (rows, 是否走了兜底)。

    **拿不到就返回空,不编数字** —— 补一行假价格会让回测的收益凭空出现,
    而且在曲线上完全看不出来。
    """
    if market == "us":
        rows = fetch_sina_us(code, bars)
        return rows, False
    rows = fetch_tencent(code, market, bars)
    if rows:
        return rows, False
    if market == "cn":
        rows = fetch_akshare_cn(code, bars)
        return rows, bool(rows)
    return [], False


# ═══════════════════════════════════════════════════════════
# 落库
# ═══════════════════════════════════════════════════════════

def save(code: str, rows: list[dict]) -> int:
    """UPSERT。**adj_close 直接写 close** —— 我们拿的本来就是前复权价
    (qfq),不需要再算复权因子。哪天换成不复权的源,这里才要动。"""
    if not rows:
        return 0
    conn = get_conn(); cur = conn.cursor()
    n = 0
    try:
        from psycopg2.extras import execute_batch
        execute_batch(cur, """
            INSERT INTO klines (code, period, ts, open, high, low, close, volume,
                                source, ingested_at, adj_close)
            VALUES (%s,'daily',%s,%s,%s,%s,%s,%s,%s,now(),%s)
            ON CONFLICT (code, period, ts) DO UPDATE
              SET open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                  close=EXCLUDED.close, volume=EXCLUDED.volume,
                  source=EXCLUDED.source, ingested_at=now(),
                  adj_close=EXCLUDED.adj_close
        """, [(code, r["ts"], r["open"], r["high"], r["low"], r["close"],
               r["volume"], r.get("source"), r["close"]) for r in rows],
            page_size=200)
        n = len(rows)
        conn.commit()
    finally:
        cur.close(); conn.close()
    return n


def _log_run(run_date: date, market: str, total: int, ok: int, failed: int,
             source: str, fallback: int, ms: int, errors: list[str]) -> None:
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""INSERT INTO klines_etl_log
            (run_date, market, codes_total, codes_success, codes_failed,
             source_used, fallback_count, duration_ms, error_sample)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
            (run_date, market, total, ok, failed, source, fallback, ms,
             json.dumps(errors[:10], ensure_ascii=False)))
        conn.commit()
    finally:
        cur.close(); conn.close()


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════

def run_market(market: str = "cn", bars: int = MAX_BARS,
               max_priority: int = 100, limit: int | None = None) -> dict:
    """跑一个市场。返回统计。

    **被限流时退避而不是硬打** —— 免费源的限流是开关式的
    (要么全成要么全败,而且会自己恢复),硬打只会延长被掐的时间。
    """
    t0 = time.time()
    codes = universe(market, max_priority, limit)
    if not codes:
        return {"market": market, "error": "股票池是空的 —— 先跑 seed_universe"}

    ok = failed = skipped = fallback = 0
    miss_streak = 0
    backoff_i = 0
    errors: list[str] = []

    for i, (code, mkt) in enumerate(codes, 1):
        if mkt == "cn" and _is_unsupported(code):
            skipped += 1
            continue
        try:
            rows, used_fb = fetch_one(code, mkt, bars)
        except Exception as e:                                # noqa: BLE001
            rows, used_fb = [], False
            errors.append(f"{code}: {type(e).__name__}")

        if rows:
            save(code, rows)
            ok += 1
            if used_fb:
                fallback += 1
            # 成功就立刻恢复全速 —— 掐是暂时的,恢复了不该继续慢
            miss_streak = backoff_i = 0
        else:
            failed += 1
            if len(errors) < 10:
                errors.append(f"{code}: 三源都没数据")
            miss_streak += 1
            if miss_streak >= MISS_TRIGGER:
                wait = BACKOFF[min(backoff_i, len(BACKOFF) - 1)]
                backoff_i += 1
                miss_streak = 0
                log.warning("[etl] 连续失败 %d 只 · 疑似限流 · 等 %d 秒",
                            MISS_TRIGGER, wait)
                time.sleep(wait)

        if i % 50 == 0:
            log.info("[etl] %s %d/%d · 成 %d 败 %d 跳 %d",
                     market, i, len(codes), ok, failed, skipped)
        time.sleep(SLEEP_SEC)

    ms = int((time.time() - t0) * 1000)
    _log_run(date.today(), market, len(codes), ok, failed,
             "sina" if market == "us" else "tencent", fallback, ms, errors)

    rate = ok / max(1, ok + failed)
    if rate < 0.9:
        # 成功率低要看得见 —— 静默的话"今天数据少了"没人知道为什么
        log.error("[etl] %s 成功率只有 %.0f%%(%d/%d)· 检查上游是否限流",
                  market, rate * 100, ok, ok + failed)

    return {"market": market, "total": len(codes), "ok": ok,
            "failed": failed, "skipped": skipped, "fallback": fallback,
            "success_rate": round(rate, 4), "duration_ms": ms}


def health() -> dict:
    """数据新鲜度 —— 给 /api/health 和监控用。"""
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""SELECT count(DISTINCT code), max(ts), count(*)
                         FROM klines WHERE period='daily'""")
        n_code, latest, n_row = cur.fetchone() or (0, None, 0)
        cur.execute("""SELECT run_date, market, codes_success, codes_failed
                         FROM klines_etl_log ORDER BY id DESC LIMIT 3""")
        last = [{"run_date": str(r[0]), "market": r[1],
                 "ok": r[2], "failed": r[3]} for r in cur.fetchall()]
    finally:
        cur.close(); conn.close()
    stale_days = (date.today() - latest).days if latest else None
    return {
        "codes": n_code or 0, "rows": n_row or 0,
        "latest": str(latest) if latest else None,
        # 超过 5 天没新数据就是不正常(长假最多 9 天,但那时候本来也没交易)
        "stale_days": stale_days,
        "ok": bool(latest) and (stale_days is not None and stale_days <= 5),
        "recent_runs": last,
    }
