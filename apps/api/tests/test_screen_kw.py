# -*- coding: utf-8 -*-
"""关键词匹配(screen_kw)回归用例 —— 不联网,不依赖 pytest 也能跑。

    cd apps/api && PYTHONPATH=. python tests/test_screen_kw.py
    # 或 pytest tests/test_screen_kw.py

## 为什么要有它

本地关键词匹配最危险的失败方式**不是报错,是静默理解错**:
产出一个看起来很正常、但意思完全不对的条件,用户照着跑扫描,完全不会发现。
这些用例每一条都来自一次真实的错误(用户报的,或对抗测试挖出来的),
改 screen_kw.py 之后必须全过才能上线。

用例分三类:
  SHOULD_MATCH    应当识别,且表达式必须**逐字**等于期望
  SHOULD_REJECT   应当拒绝(交给用户改写或点 AI 识别)
                  —— 这一类里任何一条被识别出来,都是一个静默错误
字段表用固定集合模拟,与线上 metainfo 的周期取值一致。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_API = os.path.dirname(_HERE)


def _load():
    """只加载两个纯逻辑模块,不拖起 app 包(本机和部分容器没有全套依赖)。"""
    for n in ("app", "app.services", "app.services.quant"):
        if n not in sys.modules:
            m = types.ModuleType(n)
            m.__path__ = []
            sys.modules[n] = m
    mods = {}
    for name in ("screen_dsl", "screen_kw"):
        full = f"app.services.quant.{name}"
        path = os.path.join(_API, "app", "services", "quant", f"{name}.py")
        spec = importlib.util.spec_from_file_location(full, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        spec.loader.exec_module(mod)
        mods[name] = mod
    return mods["screen_dsl"], mods["screen_kw"]


SMA = [2, 3, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 20, 21, 25, 26, 30, 34, 40, 50,
       55, 60, 75, 89, 100, 120, 144, 150, 200, 250, 300]
EMA = list(SMA)
RSI = [2, 3, 4, 5, 7, 9, 10, 20, 21, 30]
FIELDS = set("""close open high low volume change market_cap_basic price_earnings_ttm
price_book_fq return_on_equity dividends_yield_current debt_to_equity gross_margin_ttm
total_revenue_yoy_growth_ttm relative_volume_10d_calc current_ratio beta_1_year
earnings_per_share_diluted_ttm price_52_week_high price_52_week_low all_time_high
RSI ADX ATR VWAP MACD.macd MACD.signal MACD.hist Stoch.K Stoch.D BB.upper BB.lower BB.basis
Perf.W Perf.1M Perf.3M Perf.6M Perf.Y Perf.YTD rs_rating rs_raw""".split())
FIELDS |= {f"SMA{n}" for n in SMA} | {f"EMA{n}" for n in EMA} | {f"RSI{n}" for n in RSI}
FIELDS |= {f"average_volume_{n}d_calc" for n in (10, 30, 60, 90)}


SHOULD_MATCH = [
    # ── 基础三段式 ─────────────────────────────────────────
    ("成交量大于1000000", "volume > 1000000"),
    ("成交量大于100万", "volume > 1000000"),
    ("市盈率低于15", "price_earnings_ttm < 15"),
    ("市盈率低于15，市净率小于2", "price_earnings_ttm < 15 AND price_book_fq < 2"),
    ("净资产收益率大于15%", "return_on_equity > 15"),
    ("股价超过20，成交量不低于50万", "close > 20 AND volume >= 500000"),
    ("RSI 小于 30", "RSI < 30"),
    ("市值大于100亿", "market_cap_basic > 10000000000"),
    ("涨跌幅超过4%", "change > 4"),
    ("量比大于2", "relative_volume_10d_calc > 2"),
    ("90日均量大于100万", "average_volume_90d_calc > 1000000"),
    ("股息率大于5%且负债权益比小于1", "dividends_yield_current > 5 AND debt_to_equity < 1"),
    # 2026-09-10 · 「、」不在分句符里时前一个条件被静默丢掉
    ("毛利率大于40%、营收同比大于20%",
     "gross_margin_ttm > 40 AND total_revenue_yoy_growth_ttm > 20"),
    # ── 英文 ──────────────────────────────────────────────
    # 2026-09-10 · "be(low)" 曾命中字段 low
    ("price above 20", "close > 20"),
    ("RSI below 30 and price above 10", "RSI < 30 AND close > 10"),
    # ── 字段对字段 ───────────────────────────────────────
    # 2026-09-10 · 曾产出 SMA20 > 50(把右边的均线当成了阈值)
    ("20日均线大于50日均线", "SMA20 > SMA50"),
    # 2026-09-10 · 用户报:曾被「站上」模板吞掉,产出 close > SMA50
    ("50日均线高于150日均线。", "SMA50 > SMA150"),
    ("收盘价高于50日均线", "close > SMA50"),
    ("股价低于200日均线", "close < SMA200"),
    ("20日均线低于60日均线", "SMA20 < SMA60"),
    ("50日EMA高于200日EMA", "EMA50 > EMA200"),
    # ── 行话 ──────────────────────────────────────────────
    ("收盘价站上50日均线", "close > SMA50"),
    ("股价跌破200日均线", "close < SMA200"),
    ("均线多头排列", "SMA20 > SMA50 and SMA50 > SMA200"),
    # 2026-09-10 · 曾把「52周」的 52 当成百分比
    ("距离52周最高不到10%",
     "(price_52_week_high - close) / price_52_week_high <= 0.1"),
    # 2026-09-11 · 写成 > 永远 0 只
    ("股价突破52周新高", "close >= price_52_week_high"),
    ("市盈率低于15，净资产收益率大于15%，而且股价站上200日均线",
     "price_earnings_ttm < 15 AND return_on_equity > 15 AND close > SMA200"),
    # ── 2026-09-11 · 数字在后的 TA 写法(用户报 EMA20大于EMA50 识别不了)──
    ("EMA20大于EMA50", "EMA20 > EMA50"),
    ("MA20大于MA50", "SMA20 > SMA50"),
    ("SMA50>SMA200", "SMA50 > SMA200"),
    ("ema20 > ema50", "EMA20 > EMA50"),
    ("EMA 20 大于 EMA 50", "EMA20 > EMA50"),
    ("MA(20)大于MA(60)", "SMA20 > SMA60"),
    ("站上MA20", "close > SMA20"),
    ("ma5>ma10且ma10>ma20", "SMA5 > SMA10 AND SMA10 > SMA20"),
    # 曾产出 close > 20 —— MA20 里的 20 被当成了阈值
    ("收盘价大于MA20", "close > SMA20"),
    # ── 2026-09-11 · 全角(中文输入法)──────────────────────
    ("收盘价＞２０", "close > 20"),
    ("ＲＳＩ小于３０", "RSI < 30"),
    ("市盈率＜１５", "price_earnings_ttm < 15"),
    # ── 2026-09-11 · 两条均线的上穿/下穿(曾产出 close > SMA5)────
    ("MA5上穿MA10", "SMA5 > SMA10"),
    ("5日均线上穿20日均线", "SMA5 > SMA20"),
    ("20日均线下穿60日均线", "SMA20 < SMA60"),
    ("5日线金叉10日线", "SMA5 > SMA10"),
    # ── 2026-09-11 · 常用指标名 ───────────────────────────
    ("MACD大于0", "MACD.macd > 0"),
    ("MACD柱大于0", "MACD.hist > 0"),
    ("DIF大于DEA", "MACD.macd > MACD.signal"),
    ("K值小于20", "Stoch.K < 20"),
    ("收盘价大于布林上轨", "close > BB.upper"),
    ("收盘价低于布林下轨", "close < BB.lower"),
    ("收盘价大于VWAP", "close > VWAP"),
    # ── 2026-09-11 · 中文数字 ─────────────────────────────
    ("收盘价站上五日均线", "close > SMA5"),
    ("二十日均线大于六十日均线", "SMA20 > SMA60"),
    # ── 2026-09-11 · 「A比B高」句式 ───────────────────────
    ("收盘价比50日均线高", "close > SMA50"),
    ("20日均线比60日均线低", "SMA20 < SMA60"),
    # ── 2026-09-11 · RS 相对强度评级 ──────────────────────
    ("RS大于80", "rs_rating > 80"),
    ("RS评级不低于90", "rs_rating >= 90"),
    ("相对强度评级大于85", "rs_rating > 85"),
    ("RS大于80，收盘价站上50日均线", "rs_rating > 80 AND close > SMA50"),
    # rs 与 rsi 不能互相吃掉
    ("RSI小于30", "RSI < 30"),
    ("RS大于80且RSI小于70", "rs_rating > 80 AND RSI < 70"),
    # 中文里 RSI 就叫「相对强弱指数」—— 不能被映射成 RS 评级
    ("相对强弱指数小于30", "RSI < 30"),
    ("相对强度指数大于70", "RSI > 70"),
]

SHOULD_REJECT = [
    # 无关 / 看不懂
    "帮我推荐几只好股票",
    "今天天气不错",
    "找那些最近很强势的票",
    "成交量大于100万，并且老板人品好",      # 全中或全不中,不做部分识别
    # 周期不存在
    "37日均线大于50日均线",
    "RSI6小于20",
    "RSI(6) 小于 25",
    # 词表里没有的指标 —— 数字绝不能被当成阈值
    "CCI20大于100",
    # 单位不同(2026-09-11 · 曾产出 volume > SMA50)
    "成交量大于50日均线",
    "市盈率大于20日均线",
    # 本地处理不了的句型 —— 不拦就会**丢掉一半意思**
    "收盘价大于20日均线的1.05倍",        # 曾产出 close > SMA20
    "成交量是30日均量的2倍以上",
    "市盈率在10到20之间",
    "市盈率大于10小于20",               # 曾产出 pe > 10
    # 光秃秃的「相对强度/相对强弱」有歧义(RS 评级 还是 RSI?)—— 不猜
    "相对强度大于80",
    "相对强弱小于30",
]


def _run(sd, kw) -> list[str]:
    has = lambda n: n in FIELDS                          # noqa: E731
    fails: list[str] = []
    for text, want in SHOULD_MATCH:
        try:
            r = kw.translate(text, has, SMA, EMA, RSI)
            got = " AND ".join(m["expr"] for m in r["matched"])
        except sd.ScreenError as e:
            got = f"(拒绝: {e})"
        if got != want:
            fails.append(f"应识别  {text!r}\n        期望 {want}\n        实得 {got}")
    for text in SHOULD_REJECT:
        try:
            r = kw.translate(text, has, SMA, EMA, RSI)
            got = " AND ".join(m["expr"] for m in r["matched"])
            fails.append(f"应拒绝  {text!r}\n        却产出 {got}   ← 静默错误")
        except sd.ScreenError:
            pass
    return fails


def test_screen_kw():
    sd, kw = _load()
    fails = _run(sd, kw)
    assert not fails, "\n" + "\n".join(fails)


if __name__ == "__main__":
    sd, kw = _load()
    fails = _run(sd, kw)
    total = len(SHOULD_MATCH) + len(SHOULD_REJECT)
    print(f"应识别 {len(SHOULD_MATCH)} 条 · 应拒绝 {len(SHOULD_REJECT)} 条 · 共 {total}")
    if fails:
        print(f"FAIL {len(fails)} 条:")
        for f in fails:
            print("  " + f)
        sys.exit(1)
    print("ALL OK")
