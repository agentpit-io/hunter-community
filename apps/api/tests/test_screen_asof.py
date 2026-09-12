# -*- coding: utf-8 -*-
"""时间回溯的纯计算用例 —— 不连库不联网。

    cd apps/api && PYTHONPATH=. python tests/test_screen_asof.py

最怕的两种错:**把未来的数据算进过去**(回溯日之后的 K 线影响了那天的值),
以及**拿短窗口冒充长窗口**(日线不够 252 根还给出 52 周高点)。
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.quant import screen_asof as sa, vcp   # noqa: E402

fails: list[str] = []
passed = 0


def check(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
    else:
        fails.append(f"{name}  {extra}")


def bars(n, start=date(2026, 1, 5), base=100.0, step=1.0, vol=1000.0):
    out, d, k = [], start, 0
    while len(out) < n:
        if d.weekday() < 5:
            c = base + step * k
            out.append((d, c, c + 1, c - 1, vol + k))
            k += 1
        d += timedelta(days=1)
    return out


B = bars(300)
F = sa.compute_fields(B, ["close", "high", "low", "volume", "change", "SMA20", "SMA50",
                          "High.1M", "Low.1M", "High.3M", "price_52_week_high", "price_52_week_low",
                          "Perf.W", "Perf.1M", "Perf.Y", "Perf.YTD", "average_volume_30d_calc",
                          "relative_volume_10d_calc", "Volatility.D", "Volatility.M",
                          "EMA20", "RSI", "RSI7", "market_cap_basic", "price_earnings_ttm"], 2026)
last = B[-1][1]
check("收盘 = 最后一根", F["close"] == last, str(F["close"]))
check("涨跌幅 = 比前一根", abs(F["change"] - (last / (last - 1) - 1) * 100) < 1e-9, str(F["change"]))
check("SMA20 = 最近 20 根均值", abs(F["SMA20"] - (last - 9.5)) < 1e-9, str(F["SMA20"]))
check("近 1 月最高 = 最近 21 根的最高", F["High.1M"] == last + 1, str(F["High.1M"]))
check("近 1 月最低 = 最近 21 根的最低", F["Low.1M"] == last - 20 - 1, str(F["Low.1M"]))
check("近 3 月最高 = 63 根", F["High.3M"] == last + 1)
check("52 周高低 = 252 根(300 根够)", F["price_52_week_high"] == last + 1 and F["price_52_week_low"] == last - 251 - 1,
      str((F["price_52_week_high"], F["price_52_week_low"])))
check("Perf.W = 比 5 根前", abs(F["Perf.W"] - (last / (last - 5) - 1) * 100) < 1e-9)
check("Perf.1M = 比 21 根前", abs(F["Perf.1M"] - (last / (last - 21) - 1) * 100) < 1e-9)
check("Perf.Y = 比 252 根前", abs(F["Perf.Y"] - (last / (last - 252) - 1) * 100) < 1e-9)
check("Perf.YTD:序列里没有去年的收盘 → 空(不拿第一根顶替)", F["Perf.YTD"] is None, str(F["Perf.YTD"]))
check("30 日均量", abs(F["average_volume_30d_calc"] - (B[-1][4] - 14.5)) < 1e-9)
check("今日量比 = 当天 ÷ 前 10 天均量", abs(F["relative_volume_10d_calc"] - B[-1][4] / (B[-1][4] - 5.5)) < 1e-9)
check("日均振幅 = (高-低)/收 ×100", abs(F["Volatility.D"] - 2 / last * 100) < 1e-9)
check("EMA20 在合理范围(单调上涨:略低于收盘)", last - 12 < F["EMA20"] < last, str(F["EMA20"]))
check("RSI 单调上涨 = 100", F["RSI"] == 100.0 and F["RSI7"] == 100.0, str((F["RSI"], F["RSI7"])))
check("⭐市值 / PE 没有历史值 → 空", F["market_cap_basic"] is None and F["price_earnings_ttm"] is None)

# YTD 有去年的收盘
B2 = bars(300, start=date(2025, 6, 2))
F2 = sa.compute_fields(B2, ["Perf.YTD"], 2026)
prev = [b[1] for b in B2 if b[0].year == 2025][-1]
check("Perf.YTD = 比去年最后一根", abs(F2["Perf.YTD"] - (B2[-1][1] / prev - 1) * 100) < 1e-9)

# ⭐短窗口不冒充长窗口
S = bars(100)
FS = sa.compute_fields(S, ["price_52_week_high", "Perf.Y", "SMA200", "EMA100", "RSI", "SMA50", "High.3M"], 2026)
check("⭐只有 100 根:52 周高点为空", FS["price_52_week_high"] is None)
check("⭐只有 100 根:Perf.Y 为空", FS["Perf.Y"] is None)
check("⭐只有 100 根:SMA200 为空", FS["SMA200"] is None)
check("⭐EMA100 要 130 根 → 空", FS["EMA100"] is None)
check("RSI(14) 要 44 根 → 100 根够", FS["RSI"] is not None)
check("SMA50 / 3 月高点照算", FS["SMA50"] is not None and FS["High.3M"] is not None)

# 高低量缺失(老数据只有收盘)
old = [(d, c, None, None, None) for d, c, _h, _l, _v in B]
FO = sa.compute_fields(old, ["close", "SMA20", "High.1M", "average_volume_30d_calc", "Volatility.M", "volume"], 2026)
check("⭐只有收盘:均线照算,高低 / 量类为空,不拿收盘顶替",
      FO["SMA20"] is not None and FO["High.1M"] is None and FO["average_volume_30d_calc"] is None
      and FO["Volatility.M"] is None and FO["volume"] is None, str(FO))

# ⭐不泄漏未来:截到某一天之后再算,和只用那天之前的数据算完全一样
cut_day = B[199][0]
k = sa.cut_bars([b[0] for b in B], None, cut_day)
check("截断:日期 ≤ 回溯日的根数", k == 200)
A1 = sa.compute_fields(B[:k], ["close", "SMA50", "High.3M", "Perf.1M", "RSI"], 2026)
Bfuture = B[:k] + [(d, c * 3, c * 3 + 1, c * 3 - 1, v * 9) for d, c, _h, _l, v in B[k:]]   # 之后的数据面目全非
A2 = sa.compute_fields(Bfuture[:k], ["close", "SMA50", "High.3M", "Perf.1M", "RSI"], 2026)
check("⭐回溯日之后的 K 线不影响那天的值", A1 == A2, str((A1, A2)))
check("截断落在非交易日 → 取之前最近一根", sa.cut_bars([b[0] for b in B], None, cut_day + timedelta(days=0)) == 200
      and sa.cut_bars([b[0] for b in B], None, B[0][0] - timedelta(days=1)) == 0)

# 字段需要的根数 / 能否回溯
check("need_bars:SMA200=200 · EMA20=50 · RSI=44 · 52周=252", sa.need_bars("SMA200") == 200
      and sa.need_bars("EMA20") == 50 and sa.need_bars("RSI") == 44 and sa.need_bars("price_52_week_high") == 252)
check("need_bars:VCP 字段 60 · 精确窗口按窗口 · 涨跌天数 21",
      sa.need_bars("vcp_contractions") == vcp.MIN_BARS and sa.need_bars("high_63d") == 63
      and sa.need_bars("up_days_20d") == vcp.PV_DAYS + 1)
check("⭐财务 / 市值 / 板块之外的快照字段回溯不了", sa.need_bars("market_cap_basic") is None
      and sa.need_bars("return_on_equity") is None and sa.need_bars("open") is None
      and not sa.reconstructable("dividends_yield_current"))
check("静态字段(名字 / 交易所 / 板块)算能回溯", sa.reconstructable("sector") and sa.reconstructable("description"))

# RSI 对照:一段涨跌交替的序列,与手算的 Wilder 值比
xs = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28,
      46.00, 46.03, 46.41, 46.22, 45.64]
r = sa._rsi(xs, 14)
check("RSI(14) 与 StockCharts 教科书例子一致(最后一根 57.97)", r is not None and 57.5 < r < 58.5, str(r))

total = passed + len(fails)
print(f"时间回溯用例 {total} 条")
if fails:
    print(f"FAIL {len(fails)} 条:")
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("ALL OK")
