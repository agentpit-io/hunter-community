"""回测引擎 · 向量化 · 月频等权 · Sharpe/Sortino/MaxDD/Calmar
(见 doc/开源hunter-community/参考/11量化策略/quant-strategy-tech-plan.md §6)

Phase A 最简版:
- 只支持 A 股
- rebalance 只支持 M(月频)
- 等权持仓
- 用 stocks 表 · 未来加真 hs300 成分股(v2)
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.services.database import get_conn
from app.services.quant.strategy_engine import score_and_select

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# spec hash
# ═══════════════════════════════════════════════════════════════

def compute_spec_hash(strategy: dict, start: date, end: date) -> str:
    canonical = json.dumps({
        "factors": sorted(strategy["factors"], key=lambda f: f["key"]),
        "config": strategy["config"],
        "start": start.isoformat(),
        "end": end.isoformat(),
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


# ═══════════════════════════════════════════════════════════════
# rebalance 日历
# ═══════════════════════════════════════════════════════════════

def _rebalance_dates(start: date, end: date, freq: str = "M") -> list[date]:
    """生成 rebalance 日期 · Phase A 只支持 M(每月第一个交易日)"""
    if freq != "M":
        # v2 加 W / Q / H
        freq = "M"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT DISTINCT ts FROM klines
           WHERE period='daily' AND ts >= %s AND ts <= %s
           ORDER BY ts""",
        (start, end),
    )
    all_days = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    if not all_days:
        return []
    # 每月第一个交易日
    seen = set()
    result = []
    for d in all_days:
        key = (d.year, d.month)
        if key not in seen:
            seen.add(key)
            result.append(d)
    return result


# ═══════════════════════════════════════════════════════════════
# 收益计算
# ═══════════════════════════════════════════════════════════════

def _period_return(codes: list[str], dt0: date, dt1: date) -> float:
    """等权持仓 · 从 dt0 到 dt1 的收益率"""
    if not codes or dt0 >= dt1:
        return 0.0
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT code, ts, close FROM klines
           WHERE code = ANY(%s) AND period='daily' AND ts IN (
             (SELECT MAX(ts) FROM klines WHERE code = ANY(%s) AND period='daily' AND ts <= %s),
             (SELECT MAX(ts) FROM klines WHERE code = ANY(%s) AND period='daily' AND ts <= %s)
           )""",
        (codes, codes, dt0, codes, dt1),
    )
    by_code: dict[str, list] = {}
    for c, t, close in cur.fetchall():
        by_code.setdefault(c, []).append((t, float(close) if close else None))
    cur.close()
    conn.close()

    rets = []
    for c, series in by_code.items():
        if len(series) < 2:
            continue
        series.sort(key=lambda x: x[0])
        p0, p1 = series[0][1], series[-1][1]
        if p0 and p1 and p0 > 0:
            rets.append(p1 / p0 - 1)
    return sum(rets) / len(rets) if rets else 0.0


# ═══════════════════════════════════════════════════════════════
# 指标
# ═══════════════════════════════════════════════════════════════

def _calc_metrics(nav: list[float]) -> dict:
    """回测指标。

    **返回的每一项都必须是真算出来的。**`_17` 的教训:前端有一行
    `Object.assign({ir: 0.98, win_rate: 0.62, ...}, realResult.metrics)`——
    后端不返回的字段被 mock 里的常量补上了,于是页面同屏出现
    "年化 -1.2%(真)" 和 "信息比率 0.98(假)"。用户没法分辨哪个是算的。

    所以这里补齐前端要用的全部字段。**算不出来的宁可给 None 也不给数** ——
    None 让前端显示"—",一个假数会被当成结论。
    """
    if len(nav) < 2:
        return {"ann_ret": 0, "sharpe": 0, "sortino": 0, "max_dd": 0,
                "calmar": 0, "vol": 0, "win_rate": None, "n_periods": 0}
    rets = [nav[i] / nav[i-1] - 1 for i in range(1, len(nav))]
    n = len(rets)
    # 假设月频 · 12 段/年
    ann_ret = (nav[-1] / nav[0]) ** (12.0 / n) - 1 if n > 0 else 0
    mean_r = sum(rets) / n
    var = sum((r - mean_r) ** 2 for r in rets) / n
    ann_vol = math.sqrt(var * 12)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    downside = [r for r in rets if r < 0]
    downside_std = math.sqrt(sum(r*r for r in downside) / len(downside)) * math.sqrt(12) if downside else 0
    sortino = ann_ret / downside_std if downside_std > 0 else 0
    peak = nav[0]
    max_dd = 0.0
    for v in nav:
        if v > peak: peak = v
        dd = v / peak - 1
        if dd < max_dd: max_dd = dd
    calmar = ann_ret / abs(max_dd) if max_dd < 0 else 0
    # 胜率:**绝对胜率**(赚钱的期数占比),不是"跑赢基准的期数"。
    # 两者不是一回事,前端那句"36 月中 22 月跑赢基准"用的是后者的说法 ——
    # 而后者要有基准才算得出(见 §2,基准还没实现)。
    # 这里给绝对胜率并在字段名上分清楚,免得再被当成超额胜率。
    win_rate = sum(1 for r in rets if r > 0) / n
    return {
        "ann_ret": round(ann_ret, 4),
        "vol": round(ann_vol, 4),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "max_dd": round(max_dd, 4),
        "calmar": round(calmar, 3),
        "win_rate": round(win_rate, 4),
        # **实际跑了几期**。用户那次只有 8 期,而页面写着"36 月中 22 月" ——
        # 把真实期数摆出来,用户自己就能判断这个结果有没有意义
        "n_periods": n,
        # 超额胜率与信息比率要有基准才算得出。显式给 None 而不是省略 ——
        # 省略了前端 `metrics.ir` 拿到 undefined,又会走回落逻辑
        "ir": None,
        "excess_win_rate": None,
    }


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def _calc_turnover(prev: list[str], curr: list[str]) -> float:
    """单边换手率 · 简版:出仓比例"""
    if not prev:
        return 1.0
    prev_set = set(prev)
    curr_set = set(curr)
    out = len(prev_set - curr_set) / len(prev_set)
    return out


def run_backtest(strategy: dict, start: date, end: date, user_id: str | None = None) -> dict:
    """执行回测 · 返回完整结果 · positions 含 factor_contrib(C4.2)"""
    t0 = time.time()
    schedule = _rebalance_dates(start, end)
    if len(schedule) < 2:
        return {"error": "no_dates", "message": f"起止时间内无 rebalance 日 · start={start} end={end}"}

    cost_bps = strategy["config"].get("cost_bps", 15)
    nav = [1.0]
    nav_series = []
    positions_hist = []          # 每期 code list
    picks_hist = []              # 每期完整 picks(含 factor_contrib) · 用于最后一期展示
    turnover_hist = []
    for i in range(len(schedule) - 1):
        dt0, dt1 = schedule[i], schedule[i+1]
        picks = score_and_select(strategy, dt0, user_id)
        codes = [p["code"] for p in picks]
        prev_codes = positions_hist[-1] if positions_hist else []
        turnover = _calc_turnover(prev_codes, codes)
        cost = turnover * cost_bps / 10000
        period_ret = _period_return(codes, dt0, dt1)
        nav.append(nav[-1] * (1 - cost) * (1 + period_ret))
        positions_hist.append(codes)
        picks_hist.append(picks)
        turnover_hist.append(turnover)
        nav_series.append({"date": dt0.isoformat(), "nav": round(nav[-1], 4)})

    metrics = _calc_metrics(nav)
    metrics["turnover"] = round(sum(turnover_hist) / len(turnover_hist), 3) if turnover_hist else 0

    # C4.2 · 最后一期持仓保留 factor_contrib · 便于前端"贡献表"
    last_picks = picks_hist[-1] if picks_hist else []
    n_last = max(1, len(last_picks))
    positions = [{
        "code": p["code"],
        "weight": round(1.0 / n_last, 4),
        "score": p.get("score", 0),
        "factor_contrib": p.get("factor_contrib", {}),
    } for p in last_picks]

    # ── 基准(`_17` §2)────────────────────────────────────────
    # 之前后端完全没有基准,而前端画了一条 `1 + i*0.005` 的假直线,
    # 并显示"基准 +6.2% · 超额 -7.4%"。现在用真指数日线算。
    #
    # **同一套调仓日**:基准和策略在完全相同的时点取值,否则超额收益里
    # 会混进日期错配带来的噪音。
    bench = _benchmark_nav(strategy["config"].get("benchmark", "000300"), schedule)
    if bench:
        # 超额 = 策略每期收益 − 基准每期收益。IR = 超额均值 / 超额标准差(年化)
        # 用 _nav(含起点)而不是 nav_series —— 后者为了跟策略曲线对齐
        # 已经去掉了起点,拿它算逐期收益会少一期且首期算错
        b_nav = bench["_nav"]
        ex = [(nav[i] / nav[i-1] - 1) - (b_nav[i] / b_nav[i-1] - 1)
              for i in range(1, min(len(nav), len(b_nav)))]
        if ex:
            mu = sum(ex) / len(ex)
            sd = math.sqrt(sum((x - mu) ** 2 for x in ex) / len(ex))
            metrics["ir"] = round((mu * 12) / (sd * math.sqrt(12)), 3) if sd > 0 else 0
            metrics["excess_win_rate"] = round(sum(1 for x in ex if x > 0) / len(ex), 4)

    # ── 成色标记(`_17` §5)────────────────────────────────────
    # 用户拿到一个漂亮的回测,看不出里面有没有幸存者偏差。
    # **让他知道成色,比修好它更急** —— 修要等历史成分累积一两年,
    # 而误判"这策略能用"是现在就会发生的。
    from app.services.quant import universe as _universe
    quality = _universe.quality_at(strategy["config"].get("universe", "hs300"),
                                   schedule[0])
    # 换仓频率:UI 上能选 W/M/Q/H,而 _rebalance_dates 强制月频。
    # 选了没生效**必须说出来** —— 否则用户以为自己在对比不同频率,
    # 两次跑的其实是同一个东西
    want_freq = strategy["config"].get("rebalance", "M")
    if want_freq != "M":
        quality = {**quality, "rebalance_note": (
            f"你选了「{want_freq}」,但回测引擎目前只支持月频 —— "
            f"这次跑的是月度调仓。")}

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "metrics": metrics,
        "nav_series": nav_series,
        "positions": positions,
        "cost_used": cost_bps * sum(turnover_hist),
        "duration_ms": int((time.time() - t0) * 1000),
        # 取不到指数数据时显式给 null(而不是省略,也不是补一条平线)——
        # 前端据此把基准线整块隐藏。补出来的基准会让超额收益看起来很漂亮。
        # `_nav` 是内部字段,不发给前端(它含起点,与曲线不对齐,发过去只会误用)
        "benchmark": {k: v for k, v in bench.items() if k != "_nav"} if bench else None,
        "quality": quality,
        # 实际调仓频率 · 与用户所选可能不同,见 quality.rebalance_note
        "rebalance_used": "M",
    }


# ═══════════════════════════════════════════════════════════════
# C4.1 · 分档收益(quantile returns)
# 单因子分档:每期按 z-score 分 10 档 · 持仓等权到下一次 rebalance
# 输出各档年化 · 若 Q10 显著 > Q1 → 因子有效(单调)
# ═══════════════════════════════════════════════════════════════

def compute_quantile_returns(
    factor_key: str,
    universe: str = "hs300",
    start: date | None = None,
    end: date | None = None,
    n_buckets: int = 10,
    user_id: str | None = None,
) -> dict:
    """返 {q1..q_n: annualized_return_pct, cover_periods: n}
    因子若在期间数据不足 · 返 {'error': ..., 'periods': 0}
    """
    from app.services.quant.strategy_engine import _resolve_universe, _fetch_z_scores
    if end is None: end = date.today()
    if start is None: start = end - timedelta(days=365)

    schedule = _rebalance_dates(start, end)
    if len(schedule) < 2:
        return {"factor": factor_key, "error": "no_dates", "quantiles": {}}

    bucket_navs = {i: 1.0 for i in range(1, n_buckets + 1)}
    bucket_periods = {i: 0 for i in range(1, n_buckets + 1)}
    for i in range(len(schedule) - 1):
        dt0, dt1 = schedule[i], schedule[i+1]
        codes = _resolve_universe(universe, dt0, user_id)
        if not codes: continue
        zs = _fetch_z_scores(factor_key, dt0, codes)
        if len(zs) < n_buckets:
            continue
        sorted_codes = sorted(zs.items(), key=lambda x: x[1])   # 低 z 在前
        chunk = max(1, len(sorted_codes) // n_buckets)
        for b in range(1, n_buckets + 1):
            lo = (b - 1) * chunk
            hi = b * chunk if b < n_buckets else len(sorted_codes)
            bucket_codes = [c for c, _ in sorted_codes[lo:hi]]
            if not bucket_codes: continue
            period_ret = _period_return(bucket_codes, dt0, dt1)
            bucket_navs[b] *= (1 + period_ret)
            bucket_periods[b] += 1

    # 年化
    max_periods = max(bucket_periods.values()) if bucket_periods else 0
    quantiles = {}
    for b in range(1, n_buckets + 1):
        n = bucket_periods[b]
        if n < 2:
            quantiles[f"q{b}"] = None
            continue
        # 月频假设 · 12 段/年
        ann = (bucket_navs[b] ** (12.0 / n)) - 1
        quantiles[f"q{b}"] = round(ann, 4)

    return {
        "factor": factor_key,
        "universe": universe,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "n_buckets": n_buckets,
        "periods": max_periods,
        "quantiles": quantiles,
    }


# ═══════════════════════════════════════════════════════════════
# D-5 · Bootstrap 稳健性检验 · 100 次随机窗口
# ═══════════════════════════════════════════════════════════════

def bootstrap_backtest(
    strategy: dict, full_start: date, full_end: date,
    n_bootstrap: int = 100, sub_period_days: int = 365,
    user_id: str | None = None,
) -> dict:
    """在 [full_start, full_end] 内 · 随机取 n 个 sub_period 窗口 · 各跑一次
    返回:各指标 p5/p25/p50/p75/p95 + mean + std
    用户价值:告诉用户"最好 sharpe 2.1 · 最坏 0.6" · 而非只有中位 1.4
    """
    import random
    from datetime import timedelta

    total_days = (full_end - full_start).days
    if total_days < sub_period_days + 30:
        return {"error": "period_too_short",
                "message": f"完整期间 {total_days} 天 < sub_period {sub_period_days} + 30"}

    # kronos 因子 T-0 · 在 bootstrap 中无意义 · 剔除
    filtered_factors = [f for f in strategy["factors"] if f["key"] != "kronos"]
    substrategy = {**strategy, "factors": filtered_factors}

    results = []
    for i in range(n_bootstrap):
        offset = random.randint(0, total_days - sub_period_days)
        sub_start = full_start + timedelta(days=offset)
        sub_end = sub_start + timedelta(days=sub_period_days)
        r = run_backtest(substrategy, sub_start, sub_end, user_id)
        if "error" not in r:
            results.append(r["metrics"])

    if len(results) < 3:
        return {"error": "insufficient_bootstrap",
                "message": f"仅 {len(results)}/{n_bootstrap} 次成功 · 数据可能不够(至少需 3 次)"}

    def _percentile(sorted_lst, p):
        n = len(sorted_lst)
        if n == 0:
            return None
        k = (n - 1) * p / 100
        f = int(k); c = min(f + 1, n - 1)
        if f == c:
            return sorted_lst[f]
        return sorted_lst[f] + (sorted_lst[c] - sorted_lst[f]) * (k - f)

    percentiles = {}
    for key in ["ann_ret", "sharpe", "sortino", "max_dd", "calmar"]:
        values = sorted(r[key] for r in results if key in r and r[key] is not None)
        if not values:
            continue
        percentiles[key] = {
            "p5": _percentile(values, 5),
            "p25": _percentile(values, 25),
            "p50": _percentile(values, 50),
            "p75": _percentile(values, 75),
            "p95": _percentile(values, 95),
            "mean": sum(values) / len(values),
        }
        # std
        m = percentiles[key]["mean"]
        var = sum((v - m) ** 2 for v in values) / len(values)
        percentiles[key]["std"] = var ** 0.5

    return {
        "n_bootstrap": n_bootstrap,
        "n_success": len(results),
        "sub_period_days": sub_period_days,
        "full_start": full_start.isoformat(),
        "full_end": full_end.isoformat(),
        "kronos_excluded": "kronos" in [f["key"] for f in strategy["factors"]],
        "percentiles": percentiles,
    }


def _benchmark_nav(bench_code: str, schedule: list) -> dict | None:
    """按同一套调仓日算基准净值(`_17` §2)。

    **取不到就返回 None,绝不补平线。** 之前前端用 `1 + i*0.005` 画基准 ——
    那条线让每张图都显得"策略跑输了一条稳稳向上的大盘",而它是编的。
    宁可不画,也不能画一条假的:用户会据此判断策略好坏。

    只要有一个调仓日取不到指数收盘价,整段就作废 —— 缺一个点用前值补,
    那一期的基准收益会变成 0,超额收益凭空多出一截。
    """
    from app.services.quant import index_kline as ik

    if not bench_code or bench_code not in ik.INDEX_CODES:
        return None
    closes = [ik.close_on_or_before(bench_code, d) for d in schedule]
    if any(c is None for c in closes):
        missing = sum(1 for c in closes if c is None)
        log.info("[backtest] 基准 %s 缺 %d/%d 个调仓日的收盘价 · 不画基准",
                 bench_code, missing, len(schedule))
        return None

    nav = [1.0]
    for i in range(1, len(closes)):
        nav.append(nav[-1] * (closes[i] / closes[i - 1]))

    m = _calc_metrics(nav)
    # ⚠️ **和策略的 nav_series 对齐**。
    #
    # 策略那边是 `for i in range(len(schedule)-1)` 里 append,
    # 每条记的是 `(schedule[i], 走完 i→i+1 之后的净值)` —— 共 len-1 条。
    # 基准的 nav 有 len(schedule) 个点(含起点 1.0)。
    # 直接返回全部就会多出一个点:echarts 的 xAxis 只有 42 个日期,
    # 基准 43 个值 —— 两条线整体错位一格,而图上看不出来。
    series = [{"date": schedule[i].isoformat(), "nav": round(nav[i + 1], 4)}
              for i in range(len(nav) - 1)]
    return {
        "code": bench_code,
        "name": ik.INDEX_CODES[bench_code][1],
        "nav_series": series,
        # 原始 nav(含起点)· 算超额收益时要用,前端不看
        "_nav": nav,
        "ann_ret": m["ann_ret"],
        "max_dd": m["max_dd"],
        "sharpe": m["sharpe"],
    }
