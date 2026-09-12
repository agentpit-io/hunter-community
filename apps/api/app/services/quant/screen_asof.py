"""时间回溯 —— 跳到过去某一天,按那天收盘的数据跑同一份筛选脚本。

2026-09-12 用户要求:扫描筛选界面加「时间回溯」,选一天(默认当天),跳过去之后能运行扫描。

## 数据从哪来 · 边界在哪

扫描源只有**当前快照**,没有历史(screen_source 文件头第 1 条)。能回溯的只有我们自己
每晚落库的全市场日线 `rs_daily`(rs_history:收 / 高 / 低 / 量,约 320 个交易日,
RS 排名池约 4000 只美股 / 5200 只 A 股 / 1400 只港股)。所以:

1. **只有价格 / 成交量类字段能回溯**:收盘、最高最低、涨跌幅、均线、EMA、RSI、固定窗口高低、
   Perf.*、均量、量比、日均振幅、RS 评级 / RS 线天数、VCP 全套、精确交易日窗口。
   市值、PE、ROE、股息率、财报……**没有历史值,回溯时整批为空**,用到它们的条件会全部「算不出」。
   返回体里把这些字段列出来,前端要显眼地提示。
2. **股票池是 RS 排名池**(美股剔 OTC 与微盘),不是快照的 7400 只。退市的票只要还在
   480 天窗口里就在,所以回溯不会漏掉"当时在、现在没了"的票。
3. **越往前回溯,长窗口字段越算不出**:日线只保留约 320 根,回溯到 60 个交易日前只剩 260 根,
   52 周高低、精确 RS 评级(要 253 根)就没有了。每个字段需要多少根写在 `_NEED` 里,
   不够就给空 —— 不拿短窗口冒充。
4. **拆股修正照做**:先对完整序列(到今天)按扫描源锚点核对拆股(repair_splits),修好再截到回溯日。
   截了再修会因为锚点日期对不上而全部核对失败。

## 与扫描源快照的已知差异(回溯结果里要说出来)

- 固定窗口(High.5D / 1M / 3M / 6M / 52 周)按 5 / 21 / 63 / 126 / 252 个**交易日**算;
  扫描源实测是 4 / 21 / 61 根(按日历往回数,碰节假日会少)。
- EMA / RSI 从可用日线的起点递推,扫描源用更长的历史。周期越长、回溯越远,差异越大;
  所以要求日线根数 ≥ 周期 + 30,否则给空。
- 「今日量比」= 当天量 ÷ 前 10 天日均量;扫描源口径相近但未逐位核对。
- RS 排名池用**今天**的交易所 / 市值判断谁在池里(池子本身没有历史)。

## 缓存

日线整窗(到今天)+ 拆股修正的结果按市场缓存 `_CACHE_TTL`,用 numpy 存(美股约 50 MB);
回溯日只影响"截到哪一根",所以同一个市场换日期不用重新拉库。
这里的数据是**不可变的历史**,和对照表那条「不许加内存缓存」(多 worker 下纠错不生效)不是一回事:
多进程各缓存一份、最多差 20 分钟拿到昨晚新落的日线,没有正确性问题。
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from bisect import bisect_right
from datetime import date

log = logging.getLogger(__name__)

_CACHE_TTL = 20 * 60
_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()

EMA_RSI_EXTRA = 30          # EMA / RSI 递推要求的额外根数(周期 + 30)

# 快照里**不随时间变**的字段,原样带进回溯行。别的快照字段一律不带 —— 今天的市值 / PE
# 混进"那天"的行里,就是把现在的信息泄漏给过去(回测最典型的错误)
STATIC_COLS = ("name", "description", "currency", "exchange", "sector", "industry", "country")

# 固定字段 → 需要多少根日线。写在这里的才算「能回溯」
_NEED: dict[str, int] = {
    "close": 1, "high": 1, "low": 1, "volume": 1, "change": 2,
    "High.5D": 5, "Low.5D": 5, "High.1M": 21, "Low.1M": 21, "High.3M": 63, "Low.3M": 63,
    "High.6M": 126, "Low.6M": 126, "price_52_week_high": 252, "price_52_week_low": 252,
    "Perf.5D": 6, "Perf.W": 6, "Perf.1M": 22, "Perf.3M": 64, "Perf.6M": 127, "Perf.Y": 253,
    "Perf.YTD": 2,
    "Volatility.D": 1, "Volatility.W": 5, "Volatility.M": 21,
    "relative_volume_10d_calc": 11,
    "rs_raw": 253, "rs_rating": 253, "rs_line_up_days": 21,
}
_WINDOW_OF = {"High.5D": 5, "High.1M": 21, "High.3M": 63, "High.6M": 126, "price_52_week_high": 252,
              "Low.5D": 5, "Low.1M": 21, "Low.3M": 63, "Low.6M": 126, "price_52_week_low": 252}
_PERF_OF = {"Perf.5D": 5, "Perf.W": 5, "Perf.1M": 21, "Perf.3M": 63, "Perf.6M": 126, "Perf.Y": 252}
_VOLAT_OF = {"Volatility.D": 1, "Volatility.W": 5, "Volatility.M": 21}
_RE_SMA = re.compile(r"^SMA([0-9]+)$")
_RE_EMA = re.compile(r"^EMA([0-9]+)$")
_RE_RSI = re.compile(r"^RSI([0-9]*)$")
_RE_AVGVOL = re.compile(r"^average_volume_([0-9]+)d_calc$")


def need_bars(field: str) -> int | None:
    """这个字段回溯时需要多少根日线;None = 没有历史值,回溯不了。"""
    if field in _NEED:
        return _NEED[field]
    from app.services.quant import vcp
    if field in vcp.FIELDS or field == vcp.DISPLAY:
        m = re.match(r"^(?:high|low)_([0-9]+)d$", field)
        if m:
            return int(m.group(1))
        if field in ("up_days_20d", "down_days_20d", "ud_vol_ratio_20d"):
            return vcp.PV_DAYS + 1
        return vcp.MIN_BARS
    for rx, extra in ((_RE_SMA, 0), (_RE_AVGVOL, 0), (_RE_EMA, EMA_RSI_EXTRA), (_RE_RSI, EMA_RSI_EXTRA)):
        m = rx.match(field)
        if m:
            n = int(m.group(1)) if m.group(1) else 14
            return n + extra
    return None


def reconstructable(field: str) -> bool:
    return field in STATIC_COLS or need_bars(field) is not None


# ═══════════════════════════════════════════════════════════════
# 纯计算(tests/test_screen_asof.py 直接测)
# ═══════════════════════════════════════════════════════════════

def _ema(xs, n: int) -> float:
    a = 2.0 / (n + 1)
    e = xs[0]
    for x in xs[1:]:
        e = a * x + (1 - a) * e
    return e


def _rsi(xs, n: int) -> float | None:
    """Wilder RSI:前 n 个涨跌用简单平均做种子,之后按 (n-1)/n 递推 —— 和 TradingView 的 RMA 同一算法。"""
    if len(xs) < n + 1:
        return None
    gains, losses = [], []
    for a, b in zip(xs, xs[1:]):
        d = b - a
        gains.append(d if d > 0 else 0.0)
        losses.append(-d if d < 0 else 0.0)
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        ag = (ag * (n - 1) + g) / n
        al = (al * (n - 1) + l) / n
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def _ok(xs) -> bool:
    return all(x is not None and x == x for x in xs)      # x == x 排掉 NaN


def compute_fields(bars: list[tuple], fields: list[str], as_of_year: int) -> dict:
    """一只票截到回溯日的日线 → {字段: 值}。算不出的字段 = None(不猜)。

    bars = [(日期, 收, 高, 低, 量)] 升序,最后一根就是回溯日;高低量缺失是 None。
    这里不管 RS 评级 / RS 线天数 / VCP —— 它们要全市场或基准,由 build_rows 补。
    """
    n = len(bars)
    c = [b[1] for b in bars]
    h = [b[2] for b in bars]
    lo = [b[3] for b in bars]
    v = [b[4] for b in bars]
    out: dict = {}
    for f in fields:
        need = need_bars(f)
        if need is None or n < need:
            out[f] = None
            continue
        val = None
        if f == "close":
            val = c[-1]
        elif f == "high":
            val = h[-1]
        elif f == "low":
            val = lo[-1]
        elif f == "volume":
            val = v[-1]
        elif f == "change":
            val = (c[-1] / c[-2] - 1) * 100 if c[-2] else None
        elif f in _WINDOW_OF:
            k = _WINDOW_OF[f]
            is_high = f.startswith("High") or f == "price_52_week_high"
            seg = h[-k:] if is_high else lo[-k:]
            if _ok(seg):
                val = max(seg) if is_high else min(seg)
        elif f in _PERF_OF:
            k = _PERF_OF[f]
            base = c[-1 - k]
            val = (c[-1] / base - 1) * 100 if base else None
        elif f == "Perf.YTD":
            prev = [b[1] for b in bars if b[0].year < as_of_year]
            val = (c[-1] / prev[-1] - 1) * 100 if prev and prev[-1] else None
        elif f in _VOLAT_OF:
            k = _VOLAT_OF[f]
            seg = [(hh - ll) / cc * 100 for hh, ll, cc in zip(h[-k:], lo[-k:], c[-k:])
                   if hh is not None and ll is not None and cc]
            val = sum(seg) / len(seg) if len(seg) == k else None
        elif f == "relative_volume_10d_calc":
            seg = v[-11:-1]
            val = v[-1] / (sum(seg) / 10) if _ok(seg) and v[-1] is not None and sum(seg) > 0 else None
        elif (m := _RE_SMA.match(f)):
            k = int(m.group(1))
            val = sum(c[-k:]) / k
        elif (m := _RE_AVGVOL.match(f)):
            k = int(m.group(1))
            seg = v[-k:]
            val = sum(seg) / k if _ok(seg) else None
        elif (m := _RE_EMA.match(f)):
            val = _ema(c, int(m.group(1)))
        elif (m := _RE_RSI.match(f)):
            val = _rsi(c, int(m.group(1)) if m.group(1) else 14)
        out[f] = val
    return out


def cut_bars(dates: list, arr, as_of: date) -> int:
    """→ 截到回溯日的根数(日期 ≤ as_of 的个数)。dates 是升序的 date 列表。"""
    return bisect_right(dates, as_of)


# ═══════════════════════════════════════════════════════════════
# 连库:整窗日线 + 拆股修正,按市场缓存
# ═══════════════════════════════════════════════════════════════

def _load_store(market_key: str, perf: dict) -> dict:
    """→ {"codes": {code: (dates, np.ndarray n×4)}, "bench": {date: close}, "loaded_at", "last"}"""
    import numpy as np
    from app.services.database import get_conn
    from app.services.quant import rs_history as rh

    conn = get_conn()
    rh._ensure_tables(conn)
    scan = conn.cursor(name="asof_scan")
    scan.itersize = 20000
    scan.execute("SELECT code, trade_date, close, high, low, volume FROM rs_daily "
                 "WHERE market=%s ORDER BY code, trade_date", (market_key,))
    codes: dict = {}
    bench: dict = {}
    bench_bars: list = []          # 基准整根 (日期, 收, 高, 低, 量) —— 小鹿方向 A 的市场过滤要量(分布日)
    cur_code, buf = None, []

    def flush(code, full):
        if code == rh.BENCH_CODE:
            bench.update({d: c for d, c, _h, _l, _v in full})
            bench_bars.extend(full)
            return
        p = perf.get(code)
        series = [(d, c) for d, c, _h, _l, _v in full]
        if p is not None:
            series, _n = rh.repair_splits(series, rh.perf_anchors(series[-1][0], p))
            if series is None:
                return                       # 对不上又修不好 —— 这只票不给数(和每晚任务同一口径)
        raw = {d: (c, h, lo, v) for d, c, h, lo, v in full}
        bars = rh.adjust_bars(raw, series)
        dates = [b[0] for b in bars]
        arr = np.array([[b[1], b[2] if b[2] is not None else np.nan,
                         b[3] if b[3] is not None else np.nan,
                         b[4] if b[4] is not None else np.nan] for b in bars], dtype=float)
        codes[code] = (dates, arr)

    for code, d, c, h, lo, v in scan:
        if code != cur_code:
            if cur_code is not None:
                flush(cur_code, buf)
            cur_code, buf = code, []
        buf.append((d, c, h, lo, v))
    if cur_code is not None:
        flush(cur_code, buf)
    scan.close()
    conn.close()
    last = max(bench) if bench else None
    return {"codes": codes, "bench": bench, "bench_bars": sorted(bench_bars), "loaded_at": time.time(), "last": last}


def get_store(market_key: str, perf: dict) -> dict:
    now = time.time()
    with _cache_lock:
        hit = _cache.get(market_key)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    t0 = time.time()
    store = _load_store(market_key, perf)
    log.info("[screen_asof] %s 日线整窗载入 %d 只 · %.1fs", market_key, len(store["codes"]), time.time() - t0)
    with _cache_lock:
        _cache[market_key] = (now, store)
    return store


def history_range(market_key: str) -> dict:
    """→ {min_date, max_date, n_codes, full_from}。full_from = 从这天起 252 根窗口的字段才算得出。"""
    from app.services.database import get_conn
    from app.services.quant import rs_history as rh
    conn = get_conn()
    rh._ensure_tables(conn)
    cur = conn.cursor()
    cur.execute("SELECT trade_date FROM rs_daily WHERE market=%s AND code=%s ORDER BY trade_date",
                (market_key, rh.BENCH_CODE))
    ds = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT COUNT(DISTINCT code) FROM rs_daily WHERE market=%s AND code<>%s",
                (market_key, rh.BENCH_CODE))
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    if not ds:
        return {"min_date": None, "max_date": None, "n_codes": 0, "full_from": None}
    return {"min_date": str(ds[0]), "max_date": str(ds[-1]), "n_codes": n,
            "full_from": str(ds[252]) if len(ds) > 252 else None,
            "bars": len(ds)}


def _tuples(dates, arr, k: int) -> list[tuple]:
    out = []
    for i in range(k):
        r = arr[i]
        out.append((dates[i], float(r[0]),
                    None if r[1] != r[1] else float(r[1]),
                    None if r[2] != r[2] else float(r[2]),
                    None if r[3] != r[3] else float(r[3])))
    return out


def bars_upto(store: dict, code: str, as_of: date) -> list[tuple]:
    """缓存里的一只票截到 as_of 的日线 → [(日期, 收, 高, 低, 量)];没有这只票 → []。"""
    item = store["codes"].get(code)
    if not item:
        return []
    dates, arr = item
    return _tuples(dates, arr, bisect_right(dates, as_of))


def build_rows(market_key: str, as_of: date, fields: list[str],
               snap_rows: list[dict], perf: dict) -> tuple[list[dict], dict]:
    """→ (回溯日的全市场行, 信息 {as_of, requested, n, unavailable, short})

    fields     脚本用到 + 要展示的字段(扫描源字段名)
    snap_rows  今天的快照(只取 STATIC_COLS 和排名池判断用的 exchange / market_cap_basic)
    perf       {code: {Perf.*}} 拆股锚点(来自同一次快照)
    """
    from app.services.quant import rs_history as rh, screen_rs, vcp

    store = get_store(market_key, perf)
    bench_dates = sorted(store["bench"])
    k_b = bisect_right(bench_dates, as_of)
    if k_b == 0:
        raise ValueError(f"{as_of} 早于日线的起点 {bench_dates[0] if bench_dates else '(无)'}")
    as_of_actual = bench_dates[k_b - 1]
    bench_cut = {d: c for d, c in store["bench"].items() if d <= as_of_actual}

    unavailable = sorted({f for f in fields if not reconstructable(f)})
    plain = [f for f in fields if f not in STATIC_COLS and need_bars(f) is not None
             and f not in screen_rs.RS_FIELDS and f not in vcp.FIELDS and f != vcp.DISPLAY]
    uses_rating = any(f in ("rs_rating", "rs_raw") for f in fields)
    uses_line = "rs_line_up_days" in fields
    vcp_used = [f for f in fields if f in vcp.FIELDS or f == vcp.DISPLAY]
    uses_vcp_core = any(f.startswith("vcp_") for f in vcp_used)
    uses_pv = any(f in ("up_days_20d", "down_days_20d", "ud_vol_ratio_20d") for f in vcp_used)
    uses_win = any(f in vcp.WINDOW_FIELDS for f in vcp_used)

    snap = {r["_code"]: r for r in snap_rows}
    rows: list[dict] = []
    pool_idx: list[int] = []
    raw_exact: dict = {}
    short = 0
    young = 0
    for code, (dates, arr) in store["codes"].items():
        k = bisect_right(dates, as_of_actual)
        if k == 0 or dates[k - 1] != as_of_actual:
            continue                        # 那天没有收盘(停牌 / 还没上市 / 已退市)
        bars = _tuples(dates, arr, k)
        s = snap.get(code) or {}
        row = {"_code": code, "_symbol": s.get("_symbol") or code}
        for col in STATIC_COLS:
            if col in fields:
                row[col] = s.get(col)
        row.update(compute_fields(bars, plain, as_of_actual.year))
        for f in unavailable:
            row[f] = None
        if uses_line:
            st = rh.rs_line_stats([(b[0], b[1]) for b in bars], bench_cut)
            row["rs_line_up_days"] = st["up_days"] if st else None
        if uses_vcp_core:
            vs = vcp.vcp_stats(bars) or {}
            for f in vcp.FIELDS:
                if f.startswith("vcp_"):
                    row[f] = vs.get(f[4:])
            row[vcp.DISPLAY] = vs.get("depths") or None
        if uses_pv:
            pv = vcp.pv_stats(bars)
            row["up_days_20d"], row["down_days_20d"] = pv["up_days"], pv["down_days"]
            row["ud_vol_ratio_20d"] = pv["ud_vol_ratio"]
        if uses_win:
            row.update(vcp.window_stats(bars))
        if uses_rating:
            in_pool = bool(s) and screen_rs.in_population(s, market_key)
            if in_pool:
                # 覆盖率的分母只算「够老」的票:第一根日线在回溯日 253 根之前的。
                # 快照那条路把次新股也算进分母,是因为快照分不清「次新」和「数据没拉到」;
                # 这里有整段日线,次新股是能确认的事实,不是缺数据 —— 2026-09-12 实测:池里 11% 是
                # 2025 年 6 月之后上市的票,把它们算进分母后 6~7 月的覆盖率永远 89%,评级整批被门槛挡掉
                closes = [b[1] for b in bars]
                if len(closes) > 252:
                    pool_idx.append(len(rows))
                    raw_exact[len(rows)] = rh.rs_raw_exact(closes)
                else:
                    young += 1
            row["rs_raw"] = raw_exact.get(len(rows))
            row["rs_rating"] = None
        if len(bars) < 60:
            short += 1
        rows.append(row)

    rs_info = None
    if uses_rating:
        ratings, coverage = screen_rs.rs_ratings(raw_exact, len(pool_idx))
        for idx, rt in ratings.items():
            rows[idx]["rs_rating"] = rt
        rs_info = {"universe": len(pool_idx), "young": young, "coverage": coverage,
                   "gated": not ratings and bool(raw_exact),
                   "eligible": sum(1 for v in raw_exact.values() if v is not None)}

    return rows, {"as_of": as_of_actual, "requested": as_of, "n": len(rows),
                  "unavailable": unavailable, "short": short, "rs": rs_info,
                  "bench_bars": k_b, "store_last": store["last"]}
