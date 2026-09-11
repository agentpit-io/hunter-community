"""美股日线 → `klines`(数据页「全美股」下载 · 每晚刷新 · 美股回测的数据来源)。

2026-09-11 用户要求:数据页能勾选全美股(约 4069 只)下载到本地,并能回测。

## 和 rs_history 的关系

`rs_history` 是 RS 线用的管线(只要收盘价,存 `rs_daily`)。这里要完整的开高低收量,存 `klines`,
供因子与回测用。两者打同一个腾讯接口,所以**复用它的四个公共件**,一个都不另写:
  `_throttle`(全局 1 次/秒)· `_session`(keep-alive)· `WafBlocked`(见 501 即停)· `tx_symbol`(交易所后缀)
以及拆股修正 `repair_splits` / `perf_anchors`。背景见 rs_history 顶部的 2026-09-11 WAF 事故说明。

## 四条约定(改之前先读)

1. **整只重写,不补尾巴。** 拆股 / 分拆后,腾讯的前复权会改写整段历史;只补最近几天,
   新旧两段复权基准就拼在一起,拆股日凭空多出一个 -50%。所以下载任务一律整只 DELETE + 重插;
   每晚刷新先比对重叠段,基准没变才只换尾部,变了就补一次长请求整只重写。
2. **拆股必须核对。** 腾讯美股的"前复权"约 3% 的票没复权拆股(2026-09-11 实测 4069 只修正 130 只)。
   用扫描源复权过的 Perf.W … Perf.5Y 当锚点核对(实测腾讯美股前复权**不含分红**,与扫描源同口径,
   MO/O/VZ 五年差 < 1%,所以远端锚点可以用同一容差)。修不好的票**不入库**。
3. **同一时刻只有一个批量任务在打腾讯** —— Postgres advisory lock(`tencent_lock`)。
   用户下载任务、每晚刷新各自在不同线程/进程里,宿主机的 flock 在容器里看不见。
4. **美股基准存成 `.INX`**,美股交易日历就是它的日期(见 market.py)。
"""
from __future__ import annotations

import logging
import math
import time
from datetime import date, datetime, timedelta

from app.services.quant import market as mk
from app.services.quant import rs_history as rh

log = logging.getLogger(__name__)

N_MAX = 1500                    # 腾讯美股单次最多约 1500 根(2026-09-11 实测 1500 ✓ / 3000 → 0 根)
N_NIGHTLY = 320                 # 每晚刷新只取最近这么多根,够和库里做重叠比对
LONG_ANCHORS = (("Perf.3Y", 36), ("Perf.5Y", 60))
PERF_COLS = ("Perf.W", "Perf.1M", "Perf.3M", "Perf.6M", "Perf.YTD", "Perf.Y", "Perf.3Y", "Perf.5Y")

# advisory lock 的键 —— 随便一个固定整数,只要全库唯一。'USKL' 的 ASCII
LOCK_KEY = 0x55534B4C

# OHLC 自洽:价格按 NUMERIC(12,3) 入库会四舍五入,留一点余量
_OHLC_EPS = 0.002
_OHLC_BAD_MAX = 0.01            # 超过 1% 的 K 线不自洽 = 这只票的数据格式有问题,整只不要

_DDL = """
CREATE TABLE IF NOT EXISTS us_symbol (
    code       TEXT PRIMARY KEY,
    tx_symbol  TEXT NOT NULL,
    exchange   TEXT,
    last_seen  DATE NOT NULL
);
CREATE TABLE IF NOT EXISTS us_refresh_log (
    trade_date  DATE PRIMARY KEY,
    finished_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    stats       TEXT
);
"""


def _ensure_tables(conn) -> None:
    cur = conn.cursor()
    cur.execute(_DDL)
    conn.commit()
    cur.close()


# ═══════════════════════════════════════════════════════════════
# 纯计算(不联网、不连库 —— tests/test_us_kline.py 直接测)
# ═══════════════════════════════════════════════════════════════

def parse_bars(raw_bars) -> list[tuple]:
    """腾讯日线 → [(date, open, close, high, low, volume)]。

    ⚠ 腾讯字段顺序是 [日期, 开, **收**, 高, 低, 量] —— 收盘在最高前面。这里保持同样的顺序,
    写库时再按列名对号入座,免得读的人以为第三个是最高价。
    收盘价缺失或非正的那根丢掉;其余字段缺了也丢(回测和因子都要完整的一根)。
    """
    out = []
    for b in raw_bars or []:
        try:
            d = date.fromisoformat(str(b[0])[:10])
            o, c, h, l, v = (float(b[i]) for i in range(1, 6))
        except (ValueError, IndexError, TypeError):
            continue
        if c <= 0 or o <= 0 or h <= 0 or l <= 0 or v < 0:
            continue
        out.append((d, o, c, h, l, v))
    return out


def anchors_for(last: date, perf: dict) -> list[tuple[date, float]]:
    """rs_history 的 6 个锚点 + 3 年 / 5 年(下载跨度最长 5 年,锚点要够得着)。"""
    out = rh.perf_anchors(last, perf)
    for key, months in LONG_ANCHORS:
        v = perf.get(key)
        if v is not None and v > -100:
            out.append((rh._shift_months(last, months), 1.0 + v / 100.0))
    return out


def repair_ohlcv(bars: list[tuple], anchors) -> tuple[list[tuple] | None, int]:
    """拆股修正作用到整根 K 线 → (修好的 [(d,o,c,h,l,v)] | None, 修了几处)。

    拿收盘价跑 rs_history.repair_splits,得到每天的修正倍数 k = 修后收盘 / 原收盘,
    开高低收统一乘 k、成交量除以 k(1 拆 2:价格减半、股数翻倍,成交额不变)。
    repair_splits 截掉的头部(最老锚点之前、有 ±80% 跳变的那段)这里一并丢掉。
    修不好 / K 线大面积不自洽 → None,**这只票不入库**。
    """
    if not bars:
        return None, 0
    fixed, n = rh.repair_splits([(d, c) for d, _o, c, _h, _l, _v in bars], anchors)
    if fixed is None:
        return None, n
    k = {d: c_new for d, c_new in fixed}
    out = []
    bad = 0
    for d, o, c, h, l, v in bars:
        if d not in k:
            continue
        f = k[d] / c
        o2, c2, h2, l2 = o * f, c * f, h * f, l * f
        eps = _OHLC_EPS * c2
        if h2 + eps < max(o2, c2) or l2 - eps > min(o2, c2):
            bad += 1
            continue
        out.append((d, o2, c2, h2, l2, v / f))
    if not out or bad > _OHLC_BAD_MAX * len(bars):
        return None, n
    return out, n


def drop_partial(bars: list[tuple], now_et: datetime) -> list[tuple]:
    """美东今天、收盘(16:00)+ 半小时之前的那根是盘中半截 K 线,不入库。
    存进去的话,明天整只重写前,回测和因子会把盘中价当收盘价。"""
    if bars and bars[-1][0] == now_et.date() and (now_et.hour, now_et.minute) < (16, 30):
        return bars[:-1]
    return bars


def bars_needed(start: date, today: date) -> int:
    """从 start 到今天大约多少根日线(按 252/365 折算 + 余量),封顶 N_MAX。"""
    return max(60, min(N_MAX, int((today - start).days * 252 / 365) + 30))


def same_basis(new: list[tuple], old: dict) -> bool:
    """每晚刷新时判断复权基准变没变:重叠段收盘价的对数差中位数够小 = 没变。

    new  [(d,o,c,h,l,v)];old {date: close}(库里的)。
    NUMERIC(12,3) 入库有四舍五入,低价股 0.001 的误差折成比例能到千分之几,容差按价格放宽。
    """
    diffs = []
    for d, _o, c, _h, _l, _v in new:
        oc = old.get(d)
        if oc and oc > 0 and c > 0:
            tol = max(1e-4, 0.0006 / oc)
            diffs.append((abs(math.log(c / oc)), tol))
    if len(diffs) < 5:
        return False                     # 重叠太少判断不了 —— 当作变了,走整只重写(安全)
    diffs.sort()
    d_med, tol_med = diffs[len(diffs) // 2]
    return d_med <= tol_med


# ═══════════════════════════════════════════════════════════════
# 取数
# ═══════════════════════════════════════════════════════════════

def fetch_ohlcv(sym: str, n: int) -> list[tuple] | None:
    """→ [(d,o,c,h,l,v)];None = 请求失败;被 WAF 拦 → 抛 rh.WafBlocked(调用方立即停)。"""
    sess = rh._session()
    for attempt in range(rh._RETRY):
        rh._throttle()
        try:
            r = sess.get(rh._KLINE, params={"param": f"{sym},day,,,{int(n)},qfq"},
                         headers=rh._UA, timeout=rh._TIMEOUT)
        except Exception as e:                                # noqa: BLE001  网络错误才重试
            if attempt == rh._RETRY - 1:
                log.warning("[us_kline] %s 取数失败:%s", sym, e)
                return None
            time.sleep(1.5 * (2 ** attempt))
            continue
        if r.status_code == 501 or r.text.lstrip()[:1] == "<":
            raise rh.WafBlocked(f"腾讯 WAF 拦截({r.status_code})")
        try:
            data = (r.json() or {}).get("data") or {}
            node = data.get(sym) if isinstance(data, dict) else None
            return parse_bars((node or {}).get("qfqday") or (node or {}).get("day") or [])
        except (ValueError, AttributeError) as e:
            log.warning("[us_kline] %s 返回体解析失败:%s", sym, e)
            return None
    return None


def now_et() -> datetime:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York"))


# ── 池子 + 锚点(一次扫描源请求拿全)──────────────────────────
_POOL_TTL = 600
_pool_cache: dict = {"t": 0.0, "val": None}


def pool(max_age: float = _POOL_TTL) -> tuple[list[dict], dict]:
    """→ (池子 [{code, sym}], 全美股锚点 {code: perf 行})。

    池子 = RS 排名池同一口径(交易所上市、市值 ≥5000 万美元,screen_rs.in_population)。
    锚点给**全体**美股 —— 已下载但后来掉出池子的票,每晚刷新也要能核对。
    同时把代码 → 腾讯后缀存进 us_symbol,扫描源哪天拉不到时每晚刷新还能照常找到后缀。
    扫描源拉不到 → 抛 ScreenError(调用方照实告诉用户,不猜)。
    """
    now = time.time()
    if _pool_cache["val"] is not None and now - _pool_cache["t"] < max_age:
        return _pool_cache["val"]
    from app.services.quant import screen_rs, screen_source
    rows, _ = screen_source.fetch_rows("us", ["exchange", "market_cap_basic", *PERF_COLS])
    perf = {}
    members = []
    for r in rows:
        code = (r.get("_code") or "").upper().replace("/", ".")
        if not code or len(code) > 10:
            continue                                   # klines.code 是 VARCHAR(10)
        perf[code] = {k: r.get(k) for k in PERF_COLS}
        perf[code]["exchange"] = r.get("exchange")
        if screen_rs.in_population(r, "us"):
            sym = rh.tx_symbol("us", code, r.get("exchange"))
            if sym:
                members.append({"code": code, "sym": sym, "exchange": r.get("exchange")})
    _save_symbols(members)
    _pool_cache.update(t=now, val=(members, perf))
    return members, perf


def _save_symbols(members: list[dict]) -> None:
    if not members:
        return
    from psycopg2.extras import execute_values
    from app.services.database import get_conn
    conn = get_conn()
    try:
        _ensure_tables(conn)
        cur = conn.cursor()
        execute_values(cur, """INSERT INTO us_symbol (code, tx_symbol, exchange, last_seen) VALUES %s
                               ON CONFLICT (code) DO UPDATE SET tx_symbol=EXCLUDED.tx_symbol,
                                 exchange=EXCLUDED.exchange, last_seen=EXCLUDED.last_seen""",
                       [(m["code"], m["sym"], m["exchange"], date.today()) for m in members])
        conn.commit()
        cur.close()
    finally:
        conn.close()


def symbols(codes: list[str]) -> dict[str, str]:
    """代码 → 腾讯代码(从 us_symbol 表)。"""
    if not codes:
        return {}
    from app.services.database import get_conn
    conn = get_conn()
    try:
        _ensure_tables(conn)
        cur = conn.cursor()
        cur.execute("SELECT code, tx_symbol FROM us_symbol WHERE code = ANY(%s)", (list(codes),))
        return dict(cur.fetchall())
    finally:
        conn.close()


def pool_count_cached() -> int | None:
    """数据页打开时显示「全美股 约 N 只」—— **不为了一个数字去打扫描源**:
    有缓存用缓存,否则用 us_symbol 里最近 3 天见过的只数;都没有返回 None(页面显示「约 4000」)。"""
    if _pool_cache["val"] is not None:
        return len(_pool_cache["val"][0])
    from app.services.database import get_conn
    try:
        conn = get_conn()
        try:
            _ensure_tables(conn)
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM us_symbol WHERE last_seen >= current_date - 3")
            n = cur.fetchone()[0]
            return n or None
        finally:
            conn.close()
    except Exception:                                   # noqa: BLE001
        return None


# ── 跨进程互斥 ───────────────────────────────────────────────

def tencent_lock():
    """拿到锁 → 返回持锁的连接(用完 release);拿不到 → None。
    会话级 advisory lock:连接一断锁自动释放,进程崩了也不会死锁。"""
    from app.services.database import get_conn
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
    ok = cur.fetchone()[0]
    cur.close()
    if ok:
        return conn
    conn.close()
    return None


def release(conn) -> None:
    if conn is None:
        return
    try:
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
        cur.close()
    finally:
        conn.close()


# ── 写库 ─────────────────────────────────────────────────────

def write_series(code: str, bars: list[tuple], since: date | None = None) -> int:
    """写 klines。since=None → 整只重写;给了 since → 只替换 ts >= since 的那段(基准没变时的尾部更新)。
    同一事务里删 + 插,中途失败整体回滚,不会留下半只。"""
    if not bars:
        return 0
    from psycopg2.extras import execute_values
    from app.services.database import get_conn
    conn = get_conn()
    try:
        cur = conn.cursor()
        if since is None:
            cur.execute("DELETE FROM klines WHERE code=%s AND period='daily'", (code,))
        else:
            cur.execute("DELETE FROM klines WHERE code=%s AND period='daily' AND ts >= %s", (code, since))
            bars = [b for b in bars if b[0] >= since]
        execute_values(cur,
                       "INSERT INTO klines (code, period, ts, open, high, low, close, volume) VALUES %s "
                       "ON CONFLICT (code, period, ts) DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high, "
                       "low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume",
                       [(code, "daily", d, round(o, 3), round(h, 3), round(l, 3), round(c, 3),
                         int(round(v))) for d, o, c, h, l, v in bars],
                       page_size=1000)
        conn.commit()
        cur.close()
        return len(bars)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def stored_closes(code: str, since: date) -> dict:
    from app.services.database import get_conn
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT ts, close FROM klines WHERE code=%s AND period='daily' AND ts >= %s",
                    (code, since))
        return {d: float(c) for d, c in cur.fetchall() if c is not None}
    finally:
        conn.close()


def download_bench(n: int) -> int:
    """标普500 → klines code='.INX'。指数没有拆股,不用核对,整只重写。"""
    bars = fetch_ohlcv(mk.US_BENCH_TX, n)
    if not bars:
        return 0
    bars = drop_partial(bars, now_et())
    return write_series(mk.US_BENCH, bars)


def download_one(code: str, sym: str, n: int, perf: dict | None) -> tuple[str, int, date | None]:
    """整只下载 → ('ok'|'fail'|'bad'|'short', 修正处数, 最后一天)。

    没有锚点(扫描源里找不到这只)→ 'bad':核对不了拆股,宁可不给。
    """
    bars = fetch_ohlcv(sym, n)
    if bars is None:
        return "fail", 0, None
    bars = drop_partial(bars, now_et())
    if len(bars) < 5:
        return "short", 0, None          # 腾讯对写错后缀的代码返回 1–2 根,不是真数据
    if not perf:
        return "bad", 0, None
    fixed, nfix = repair_ohlcv(bars, anchors_for(bars[-1][0], perf))
    if fixed is None:
        return "bad", nfix, None
    write_series(code, fixed)
    return "ok", nfix, fixed[-1][0]


# ═══════════════════════════════════════════════════════════════
# 每晚刷新(api 进程内定时任务,见 scheduler.register_local)
# ═══════════════════════════════════════════════════════════════

def nightly_refresh(lock_wait_sec: int = 7200) -> dict:
    """更新**用户已经下过的**美股(data_coverage 里的 US 代码)。一只都没下过就什么都不做。

    每只取最近 N_NIGHTLY 根 → 核对拆股 → 和库里重叠段比对:
      基准没变 → 只替换尾部;变了(拆股 / 分拆后腾讯改写了历史)→ 按已覆盖区间补一次长请求,整只重写。
    修不好的票这次不动(库里旧数据仍然自洽,只是停在昨天),计数报出来。
    """
    from app.services.quant import universe as uv, factor_engine as fe
    from app.services.database import get_conn
    codes = uv.covered_codes(mk.US)
    if not codes:
        return {"skipped": "no_us_data"}

    lock = None
    waited = 0
    while lock is None:
        lock = tencent_lock()
        if lock is None:
            if waited >= lock_wait_sec:
                log.warning("[us_kline] 等锁 %d 秒仍被占用(下载任务或 RS 管线在跑)· 本次跳过", waited)
                return {"skipped": "lock_busy"}
            time.sleep(60)
            waited += 60
    stat = {"codes": len(codes), "tail": 0, "rewrite": 0, "bad": 0, "fail": 0}
    try:
        conn = get_conn()
        try:
            _ensure_tables(conn)
            cur = conn.cursor()
            cur.execute("SELECT code, covered_from FROM data_coverage WHERE data_type='kline' AND code = ANY(%s)",
                        (codes,))
            cov_from = dict(cur.fetchall())
        finally:
            conn.close()

        today = date.today()
        n_bench = bars_needed(min(cov_from.values(), default=today - timedelta(days=400)), today)
        download_bench(n_bench)
        bench_last = _latest(mk.US_BENCH)
        if bench_last and _already_done(bench_last):
            return {"skipped": "done", "trade_date": str(bench_last)}

        try:
            _members, perf = pool(max_age=0)
        except Exception as e:                           # noqa: BLE001
            log.error("[us_kline] 扫描源拉不到锚点,今晚不刷新(没有锚点核对不了拆股):%s", e)
            return {"error": "no_anchors"}
        syms = symbols(codes)
        for code in codes:
            sym = syms.get(code) or rh.tx_symbol("us", code, (perf.get(code) or {}).get("exchange"))
            p = perf.get(code)
            if not sym or not p:
                stat["bad"] += 1
                continue
            bars = fetch_ohlcv(sym, N_NIGHTLY)
            if bars is None:
                stat["fail"] += 1
                continue
            bars = drop_partial(bars, now_et())
            fixed, _ = repair_ohlcv(bars, anchors_for(bars[-1][0], p)) if len(bars) >= 5 else (None, 0)
            if fixed is None:
                stat["bad"] += 1
                continue
            old = stored_closes(code, fixed[0][0])
            if same_basis(fixed, old):
                write_series(code, fixed, since=fixed[0][0])
                stat["tail"] += 1
            else:
                st, _, last = download_one(code, sym, bars_needed(cov_from.get(code) or fixed[0][0], today), p)
                if st != "ok":
                    stat["bad"] += 1
                    continue
                stat["rewrite"] += 1
            _extend_coverage(code, fixed[-1][0])

        if bench_last:
            for k in fe.LOCAL_ONLY:
                try:
                    fe.compute_and_store(k, codes, bench_last)
                except Exception as e:                   # noqa: BLE001
                    log.warning("[us_kline] 因子 %s @ %s 失败:%s", k, bench_last, e)
            _mark_done(bench_last, stat)
        log.info("[us_kline] 每晚刷新 %s · %s", bench_last, stat)
        return stat
    except rh.WafBlocked as e:
        log.error("[us_kline] 每晚刷新被腾讯 WAF 拦截,本次中止:%s", e)
        return {**stat, "error": "waf"}
    finally:
        release(lock)


def _latest(code: str) -> date | None:
    from app.services.database import get_conn
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT max(ts) FROM klines WHERE code=%s AND period='daily'", (code,))
        return cur.fetchone()[0]
    finally:
        conn.close()


def _already_done(d: date) -> bool:
    from app.services.database import get_conn
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM us_refresh_log WHERE trade_date=%s", (d,))
        return cur.fetchone() is not None
    finally:
        conn.close()


def _mark_done(d: date, stat: dict) -> None:
    import json
    from app.services.database import get_conn
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO us_refresh_log (trade_date, stats) VALUES (%s, %s) "
                    "ON CONFLICT (trade_date) DO UPDATE SET finished_at=now(), stats=EXCLUDED.stats",
                    (d, json.dumps(stat, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()


def _extend_coverage(code: str, last: date) -> None:
    from app.services.database import get_conn
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE data_coverage SET covered_to = GREATEST(covered_to, %s), updated_at=now() "
                    "WHERE code=%s AND data_type='kline'", (last, code))
        conn.commit()
    finally:
        conn.close()
