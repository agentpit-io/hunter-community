# -*- coding: utf-8 -*-
"""筛选器 · ThinkScript 时间序列引擎(screen_series)回归用例 —— 不联网,不连库,不依赖 pytest。

    cd apps/api && PYTHONPATH=. python tests/test_screen_series.py

## 为什么要有它

2026-09-15 用户粘了一份标准的 ThinkScript 选股脚本(猎杀 FOMO:连续阳线 + 窗口内阴线数 + 累计涨幅 +
天量),界面报「第 18 行:看不懂的字符 '['」,AI 修错也修不了。根因不是那一句,是**整类写法**没支持:
K 线偏移 x[n]、递归 def、if-then-else、input、对任意表达式做 Sum / Highest。

用户要求:「设计大量测试用例,自动测试,定位解决 bug,确保下次输入 ThinkScript 脚本可以在本地正常识别」。
仓内规矩(CLAUDE.md「改 screen_kw 必须先跑回归用例」那节)同样适用:**按写法类别测,不按报上来的那一句测**;
专门写"应当拒绝"的用例;用例只加不删。

## 分组

  A  语法 · 应通过(编译不报错、且判成时间序列模式);以及横截面老脚本**不能**被误判
  B  应拒绝 · 报错里要点名原因(负偏移 / 自引用 / 动态偏移 / fold / 周线 / 全角字符 …)
  C  求值语义 · 合成数据逐项对答案(偏移 / 窗口 / 递归对暴力算法 / EMA / RSI / 三值逻辑 / 除零 …)
  D  用户那份脚本端到端:命中 / 不满足 / 缺开盘价算不出 三种结果各一只
  E  条件行(decompose)与 bars_matrix
  F  性能:4000 只 × 252 根跑用户脚本

⭐ 标注的是曾经出过错、或者一旦回归就会**静默算错**的用例。
"""
from __future__ import annotations

import importlib.util
import math
import os
import sys
import time
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_API = os.path.dirname(_HERE)

FAILS: list[str] = []
N_OK = 0


def check(name: str, ok, detail: str = ""):
    global N_OK
    ok = bool(ok)
    print(("OK   " if ok else "FAIL ") + name + (("  · " + str(detail)[:200]) if (detail and not ok) else ""))
    if ok:
        N_OK += 1
    else:
        FAILS.append(name)


def _load():
    for n in ("app", "app.services", "app.services.quant"):
        if n not in sys.modules:
            m = types.ModuleType(n)
            m.__path__ = []
            sys.modules[n] = m
    mods = {}
    for name in ("screen_dsl", "screen_series"):
        full = f"app.services.quant.{name}"
        path = os.path.join(_API, "app", "services", "quant", f"{name}.py")
        spec = importlib.util.spec_from_file_location(full, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        spec.loader.exec_module(mod)
        mods[name] = mod
    return mods["screen_dsl"], mods["screen_series"]


sd, ss = _load()
import numpy as np  # noqa: E402

SMA = [2, 3, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 20, 21, 25, 26, 30, 34, 40, 50, 55, 60, 75, 89, 100, 120, 144, 150, 200, 250, 300]
EMA = list(SMA)
RSI = [2, 3, 4, 5, 7, 9, 10, 20, 21, 30]
FIELDS = set("""close open high low volume change market_cap_basic price_earnings_ttm return_on_equity
relative_volume_10d_calc price_52_week_high Perf.Y rs_rating RSI""".split())
FIELDS |= {f"SMA{n}" for n in SMA} | {f"EMA{n}" for n in EMA} | {f"RSI{n}" for n in RSI}
FIELDS |= {f"average_volume_{n}d_calc" for n in (10, 30, 60, 90)}
has = lambda n: n in FIELDS                                   # noqa: E731

NAN = float("nan")


def compile(script: str):
    return sd.compile_script(script, has, SMA, EMA, RSI)


def err_of(script: str) -> str:
    try:
        compile(script)
    except sd.ScreenError as e:
        return str(e)
    return ""


# ─── 合成数据 ──────────────────────────────────────────────────────
def stock(n=80, close=None, open_=None, high=None, low=None, volume=None):
    """一只票的五条序列(list,可含 None)。默认:收 10、开 9.9(阳线)、高 10.1、低 9.8、量 10 万。"""
    def fill(v, d):
        if v is None:
            return [d] * n
        if isinstance(v, (int, float)):
            return [float(v)] * n
        if callable(v):
            return [v(i) for i in range(n)]
        return list(v) + [None] * (n - len(v)) if len(v) < n else list(v)[:n]
    return {"close": fill(close, 10.0), "open": fill(open_, 9.9), "high": fill(high, 10.1),
            "low": fill(low, 9.8), "volume": fill(volume, 1e5)}


def matrix(stocks: list[dict]):
    n = len(stocks[0]["close"])
    out = {}
    for k in ("open", "high", "low", "close", "volume"):
        rows = []
        for s in stocks:
            rows.append([NAN if v is None else float(v) for v in s[k]])
        out[k] = np.array(rows, dtype=float).reshape(len(stocks), n)
    return out


def run(script: str, stocks: list[dict], snap: dict | None = None, keep=None):
    c = compile(script)
    if c.series is None:
        # 这里只测时间序列引擎。脚本里一个序列写法都没有的话会走横截面(那是对的),
        # 但这条用例就没测到东西 —— 直接报出来,别让它变成 AttributeError
        raise AssertionError("测试脚本没有序列写法,走了横截面模式:" + script.replace("\n", " ⏎ ")[:120])
    res = ss.evaluate(c, matrix(stocks), snap or {}, keep=set(keep or []))
    return c, res


def verdicts(script: str, stocks: list[dict], snap=None):
    _, res = run(script, stocks, snap)
    out = []
    for v in res["verdict"]:
        out.append(None if v != v else bool(v))
    return out


def last(script: str, name: str, stocks: list[dict], snap=None):
    _, res = run(script, stocks, snap)
    v = res["last"][name][0]
    return None if v != v else float(v)


def close_to(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol * max(1.0, abs(b))


USER_SCRIPT = """# ============================================
# 猎杀FOMO 只做空 - 选股器
# ============================================

input lookback = 5;
input minBullDays = 3;
input maxBearInWindow = 1;
input minRet = 0.80;      # 小盘股用 1.0，大盘用 0.25
input last2RatioMin = 0.5;
input volMult = 10;
input volMaxMult = 1.5;
input minAmount = 50000000;

def isBull = close > open;
def isBear = close < open;

# 连续阳线
def bullStreak = if isBull then bullStreak[1] + 1 else 0;

# 窗口内阴线数
def bearCount = Sum(isBear, lookback);

# 累计涨幅
def cumRet = close / close[lookback] - 1;

# 最后一日涨幅是否窗口内最大
def ret = close / close[1] - 1;
def maxRet = Highest(ret, lookback);
def isMaxRet = ret >= maxRet;

# 加速
def accel = isMaxRet and ret > ret[1];

# 最后2日占比
def last2Ret = close / close[2] - 1;
def last2Ratio = if cumRet > 0 then last2Ret / cumRet else 0;

# 成交量递增
def volUp3 = volume > volume[1] and volume[1] > volume[2];

# 天量
def volMA20 = Average(volume[1], 20);
def volMax60 = Highest(volume[1], 60);
def volSpike = volume > volMA20 * volMult and volume > volMax60 * volMaxMult;

# 成交额
def amount = close * volume;

plot scan = bullStreak >= minBullDays
    and bearCount <= maxBearInWindow
    and cumRet >= minRet
    and accel
    and last2Ratio >= last2RatioMin
    and volUp3
    and volSpike
    and amount >= minAmount;
"""

# ═══════════════════════════════════════════════════════════════
# A · 语法 · 应通过
# ═══════════════════════════════════════════════════════════════
print("\n── A 语法·应通过(时间序列模式)──")
A_SERIES = [
    ("input 整数", "input n = 5;\nplot scan = close > close[n];"),
    ("input 小数 / 负数 / yes", "input r = 0.8;\ninput k = -2;\ninput on = yes;\nplot scan = on and close / close[1] - 1 > r + k;"),
    ("rec 显式递归", "rec s = if close > close[1] then s[1] + 1 else 0;\nplot scan = s >= 3;"),
    ("declare 整句跳过", "declare lower;\ndeclare once_per_bar;\ndef a = close > open[1];\nplot scan = a;"),
    ("AddLabel / .SetDefaultColor 等画图语句整句跳过",
     "def a = close > open[1];\nAddLabel(yes, \"bull\", Color.RED);\nplot scan = a;\nscan.SetDefaultColor(Color.GREEN);\nscan.SetPaintingStrategy(PaintingStrategy.BOOLEAN_ARROW_UP);\nAssignPriceColor(if a then Color.GREEN else Color.RED);"),
    ("if-then-else 嵌套 else if", "def x = if close > open then 1 else if close < open then -1 else 0;\nplot scan = x == 1;"),
    ("if() 函数写法", "def x = if(close > open, 1, 0);\nplot scan = x == 1;"),
    ("偏移:字面量 / input / 常量表达式", "input n = 3;\nplot scan = close[1] > close[n + 1] and high[2 * n] > 0;"),
    ("括号表达式后接偏移", "plot scan = (close - open)[1] > 0;"),
    ("! && ||", "plot scan = !(close < open[1]) && (volume > 0 || high > low);"),
    ("crosses above / below / 任一", "def s = Average(close, 20);\nplot scan = close crosses above s or low crosses below s or close crosses s;"),
    ("Crosses() 函数 + CrossingDirection", "plot scan = Crosses(close, Average(close, 20), CrossingDirection.ABOVE);"),
    ("within n bars", "def b = close > Highest(high[1], 20);\nplot scan = b within 5 bars;"),
    ("命名参数 / 引号参数名", "def r = RSI(\"length\" = 9);\ndef a = Average(data = close, length = 20);\nplot scan = r > 50 and close > a;"),
    ("Sum 对布尔计数", "def bear = close < open;\nplot scan = Sum(bear, 5) <= 1;"),
    ("Highest 对任意表达式", "def ret = close / close[1] - 1;\nplot scan = ret >= Highest(ret, 5);"),
    ("Average 对偏移后的序列", "plot scan = volume > Average(volume[1], 20) * 10;"),
    ("研究函数 RSI / ATR / MACD / BollingerBands / VolumeAvg",
     "plot scan = RSI() > 50 and ATR(14) > 0 and MACD().Diff > 0 and close > BollingerBands().UpperBand and volume > VolumeAvg(50).VolAvg;"),
    ("MovingAverage(AverageType.X)", "plot scan = MovingAverage(AverageType.EXPONENTIAL, close, 20) > MovingAverage(AverageType.SIMPLE, close, 50) and MovingAverage(AverageType.WILDERS, close, 10) > 0;"),
    ("Double.NaN / IsNaN / CompoundValue / BarNumber / GetValue",
     "def x = if close > open then close else Double.NaN;\nrec c = CompoundValue(1, c[1] + 1, 0);\nplot scan = !IsNaN(x) and BarNumber() > 10 and GetValue(close, 2) > 0 and c > 5;"),
    ("hl2 / hlc3 / ohlc4", "plot scan = close > hl2 and hlc3 > ohlc4[1];"),
    ("数学函数一把抓", "plot scan = Max(close, open) > Min(low, 1) and AbsValue(close - open) < Sqrt(Power(close, 2)) and Round(close / open, 2) > 0 and Between(RSI(), 30, 70) and StDev(close, 20) >= 0 and TotalSum(volume) > 0 and close >= HighestAll(high) * 0.5 and Log(close) > 0 and Exp(0) == 1 and Floor(close) <= Ceil(close) and Sign(close) == 1;"),
    ("快照字段混用", "plot scan = market_cap_basic > 1000000000 and close > close[1];"),
    ("多行 plot", "def a = close > open[1];\ndef b = volume > 0;\nplot scan = a\n    and b;"),
    ("注释里有中文与全角逗号", "input minRet = 0.80;      # 小盘股用 1.0，大盘用 0.25\nplot scan = close / close[5] - 1 >= minRet;"),
    ("多个 plot 取最后一个", "plot a = close > open;\nplot scan = close > close[1];"),
    ("plot 直接是数值(非 0 为真)", "def s = if close > open then s[1] + 1 else 0;\nplot scan = s;"),
    ("大小写不敏感的函数名 / 序列名", "plot scan = CLOSE > average(Close, 20) and SUM(close > Open, 5) > 2;"),
    ("ExpAverage / WildersAverage / SimpleMovingAvg / MovAvgExponential 别名", "plot scan = ExpAverage(close, 10) > WildersAverage(close, 10) and SimpleMovingAvg(close, 10) > 0 and MovAvgExponential(close, 10) > 0;"),
    ("Lowest / LowestAll / IsAscending / IsDescending / RoundUp / RoundDown", "plot scan = close > Lowest(low, 20) and close > LowestAll(low) and IsAscending(close, 3) and !IsDescending(close, 3) and RoundUp(close, 1) >= RoundDown(close, 1);"),
    ("用户脚本(猎杀 FOMO)", USER_SCRIPT),
]
for name, script in A_SERIES:
    e = err_of(script)
    ok = not e
    if ok:
        c = compile(script)
        ok = c.series is not None
        e = "没有判成时间序列模式" if not ok else ""
    check(f"A 通过 · {name}", ok, e)

print("\n── A 横截面老脚本不能被误判 ──")
A_CROSS = [
    ("Average(close,50) → SMA50", "plot scan = Average(close, 50) > close;", ["SMA50", "close"]),
    ("Highest(high,252) → 52 周高", "def sma20 = Average(close, 20);\nplot scan = close > sma20 and Highest(high, 252) * 0.9 < close;", ["SMA20", "close", "price_52_week_high"]),
    ("RSI(14) → RSI", "plot scan = RSI(14) < 30;", ["RSI"]),
    ("纯字段比较", "plot scan = close > 20 and volume > 1000000;", ["close", "volume"]),
    ("均量字段", "plot scan = Average(volume, 30) > 1000000;", ["average_volume_30d_calc"]),
]
for name, script, fields in A_CROSS:
    e = err_of(script)
    c = compile(script) if not e else None
    check(f"A 横截面 · ⭐{name} 仍走老路", (c is not None) and c.series is None and sorted(c.fields) == sorted(fields),
          e or (c and (c.series, c.fields)))
check("A 横截面 · ⭐_needs_series:Average(close,50) 否", not sd._needs_series(sd._Parser("plot scan = Average(close, 50) > 1;").parse()[0]))
check("A 横截面 · _needs_series:Average(volume[1],20) 是", sd._needs_series(sd._Parser("plot scan = Average(volume[1], 20) > 1;").parse()[0]))
check("A 横截面 · _needs_series:Highest(ret, n) 是", sd._needs_series(sd._Parser("def ret = close;\nplot scan = Highest(ret, 5) > 1;").parse()[0]))
check("A 横截面 · _needs_series:Sum() 是", sd._needs_series(sd._Parser("plot scan = Sum(close, 5) > 1;").parse()[0]))

# ═══════════════════════════════════════════════════════════════
# B · 应拒绝 · 报错要点名原因
# ═══════════════════════════════════════════════════════════════
print("\n── B 应拒绝 ──")
B = [
    ("负偏移", "plot scan = close[-1] > 0;", "负数偏移"),
    ("自引用没有偏移", "def s = s + 1;\nplot scan = s > 0;", "直接引用了自己"),
    ("自引用 [0]", "def s = if close > 0 then s[0] + 1 else 0;\nplot scan = s > 0;", "至少 [1]"),
    ("自引用放在窗口函数里", "def s = Sum(s[1], 3) + 1;\nplot scan = s > 0;", "窗口函数"),
    ("先用后定义 / 互递归", "def a = b + 1;\ndef b = close;\nplot scan = a > 0;", "后面才定义"),
    ("动态偏移", "def n = if close > open then 1 else 2;\nplot scan = close[n] > 0;", "常量"),
    ("动态窗口", "def n = if close > open then 5 else 10;\nplot scan = Sum(close, n) > 0;", "常量"),
    ("小数窗口", "plot scan = Sum(close, 2.5) > 0;", "整数"),
    ("fold", "plot scan = fold i = 0 to 5 with s do s + close[i] > 0;", "fold"),
    ("AggregationPeriod 周线", "plot scan = close(period = AggregationPeriod.WEEK) > 0;", "只有日线"),
    ("ADX 未实现要明说", "plot scan = ADX() > 25;", "ADX"),
    ("字符串当值", "plot scan = close > \"20\";", "字符串"),
    ("枚举型 input", "input t = {default A, B};\nplot scan = close > 0;", "花括号"),
    ("全角分号", "plot scan = close > 0；", "全角"),
    ("全角括号", "plot scan = （close > 0）;", "全角"),
    ("? : 三目", "def x = close > open ? 1 : 0;\nplot scan = x == 1;", "三目"),
    ("input 序列型", "input p = close;\nplot scan = p > 0;", "序列型参数"),
    ("def x; 两段写法", "def x;\nx = close > 0;\nplot scan = x;", "两段写法"),
    ("不认识的名字", "plot scan = foo > 1;", "不认识"),
    (".属性用在非研究函数上", "plot scan = Average(close, 5).Value > 0;", "只有 MACD()"),
    ("BollingerBands 没有的属性", "plot scan = BollingerBands().Top > 0;", "没有 .Top"),
    ("MovingAverage 第 1 个参数不是类型", "plot scan = MovingAverage(close[1], 20) > 0;", "AverageType"),
    ("窗口超过能装的根数", "plot scan = Sum(close, 500) > 0;", "超过"),
    ("if 缺 else", "def x = if close > open then 1;\nplot scan = x > 0;", "else"),
    ("Color 常量出现在条件里", "plot scan = close > Color.RED;", "颜色"),
    ("Crosses 方向参数不对", "plot scan = Crosses(close, open, 5);", "CrossingDirection"),
    ("未知函数要列出支持清单", "plot scan = Foo(close, 5) > 0;", "支持"),
    ("命名参数名不存在", "plot scan = Average(close, window = 5) > 0;", "没有叫"),
    ("参数太多", "plot scan = Average(close, 5, 6) > 0;", "最多"),
    ("单引号", "plot scan = close > 'a';", "单引号"),
    ("^ 乘方", "plot scan = close ^ 2 > 0;", "Power"),
    ("% 百分号", "plot scan = close > 5%;", "百分号"),
    ("缺分号", "def a = close > open\nplot scan = a;", "分号"),
    ("没有 plot", "def a = close[1] > 0;", "plot"),
    ("单个 = 当比较", "plot scan = close[1] = 5;", "=="),
]
for name, script, kw in B:
    e = err_of(script)
    check(f"B 拒绝 · {name}", bool(e) and kw in e, e or "(没报错)")

# ═══════════════════════════════════════════════════════════════
# C · 求值语义
# ═══════════════════════════════════════════════════════════════
print("\n── C 求值 ──")
ramp = stock(close=lambda i: float(i + 1))                      # 收盘 1..80
check("C 偏移 · close[1] / close[3]", verdicts("plot scan = close[1] == 79 and close[3] == 77;", [ramp]) == [True])
check("C 偏移 · def 值", close_to(last("def x = close[2];\nplot scan = x > 0;", "x", [ramp]), 78))
check("C 偏移 · 超出历史 → 算不出(不是不满足)", verdicts("plot scan = close[80] > 0;", [ramp]) == [None])
check("C 偏移 · [0] 就是自己", verdicts("plot scan = close[0] == close;", [ramp]) == [True])
check("C 偏移 · input 当偏移 = 字面量", close_to(last("input n = 5;\ndef a = close[n];\ndef b = close[5];\ndef d = a - b;\nplot scan = d == 0;", "d", [ramp]), 0))
check("C 偏移 · GetValue(close, 2) == close[2]", verdicts("plot scan = GetValue(close, 2) == close[2];", [ramp]) == [True])

# 最后 5 根:阴 阳 阴 阳 阳(阴线 = close < open)
pat = stock(open_=lambda i: (10.2 if i in (75, 77) else 9.9))
check("C Sum · 布尔计数 = 2", close_to(last("def bear = close < open;\ndef n = Sum(bear, 5);\nplot scan = n > 0;", "n", [pat]), 2))
check("C Sum · 窗口 3 = 1", close_to(last("def bear = close < open;\ndef n = Sum(bear, 3);\nplot scan = n > 0;", "n", [pat]), 1))
check("C Sum · 窗口大于历史 → 算不出", verdicts("plot scan = Sum(close, 81) > 0;", [ramp]) == [None])

check("C Highest/Lowest · 任意表达式", close_to(last("def ret = close - close[1];\ndef h = Highest(ret, 5);\nplot scan = h > 0;", "h", [ramp]), 1))
zig = stock(close=lambda i: [10, 12, 11, 15, 9][i % 5])
check("C Highest · 窗口 5 取最大", close_to(last("def h = Highest(close, 5);\nplot scan = h > 0;", "h", [zig]), 15))
check("C Lowest · 窗口 5 取最小", close_to(last("def l = Lowest(close, 5);\nplot scan = l > 0;", "l", [zig]), 9))
check("C Highest(high[1], n) · 不含当根", close_to(last("def h = Highest(close[1], 4);\nplot scan = h > 0;", "h", [zig]), 15))

vol = stock(volume=lambda i: 1e5 if i < 75 else [1.2e5, 1.5e5, 2e5, 3e5, 4e6][i - 75])
check("C Average(volume[1], 20) 手算 118500", close_to(last("def m = Average(volume[1], 20);\nplot scan = m > 0;", "m", [vol]), 118500.0))
# 注:Average(close, 5) 这种「裸价格 + 数字」在横截面模式下映射成 SMA5,所以这里用 close[0] 逼它走序列引擎
check("C Average(close[0], 5) 对 1..80 = 78", close_to(last("def m = Average(close[0], 5);\nplot scan = m > 0;", "m", [ramp]), 78))

check("C if · 取值", close_to(last("def x = if close > open then 1 else -1;\nplot scan = x > 0;", "x", [pat]), 1))
check("C if · 条件为 NaN → NaN", last("def x = if open > 0 then 1 else 0;\nplot scan = x > 0;", "x", [stock(open_=[None] * 80)]) is None)
check("C if · 嵌套 else if", close_to(last("def x = if close > 100 then 1 else if close > 50 then 2 else 3;\nplot scan = x > 0;", "x", [ramp]), 2))
check("C if() 函数写法 = if 表达式", verdicts("def a = if close > open then 1 else 0;\ndef b = if(close > open, 1, 0);\nplot scan = a == b;", [pat]) == [True])


def brute_streak(opens, closes):
    s = 0
    for o, c in zip(opens, closes):
        if o is None or c is None or o != o or c != c:
            return None
        s = s + 1 if c > o else 0
    return s


mixed = stock(open_=lambda i: (10.3 if i % 7 == 3 else 9.9))
_exp = brute_streak(mixed["open"], mixed["close"])
check("C 递归 · ⭐连续阳线数 = 暴力算法", close_to(last("def s = if close > open then s[1] + 1 else 0;\nplot scan = s > 0;", "s", [mixed]), _exp), f"期望 {_exp}")
c_, res_ = run("def s = if close > open then s[1] + 1 else 0;\nplot scan = s > 0;", [mixed], keep=["s"])
col = res_["full"]["s"][0]
brute_col = []
s = 0
for o, cc in zip(mixed["open"], mixed["close"]):
    s = s + 1 if cc > o else 0
    brute_col.append(s)
check("C 递归 · 整列逐根 = 暴力算法", all(abs(a - b) < 1e-9 for a, b in zip(col, brute_col)))
# 左边补了 NaN(只有 30 根真实历史)
short = stock(open_=lambda i: (None if i < 50 else (10.3 if i % 7 == 3 else 9.9)),
              close=lambda i: (None if i < 50 else 10.0), high=lambda i: (None if i < 50 else 10.1),
              low=lambda i: (None if i < 50 else 9.8), volume=lambda i: (None if i < 50 else 1e5))
_exp2 = brute_streak(short["open"][50:], short["close"][50:])
check("C 递归 · ⭐历史不够长(左侧补 NaN)时起点按 0 算,不被 NaN 毒掉",
      close_to(last("def s = if close > open then s[1] + 1 else 0;\nplot scan = s > 0;", "s", [short]), _exp2), f"期望 {_exp2}")
check("C 递归 · rec 关键字同义", close_to(last("rec s = if close > open then s[1] + 1 else 0;\nplot scan = s > 0;", "s", [mixed]), _exp))
check("C 递归 · CompoundValue 计数器 = 真实根数 - 1", close_to(last("rec c = CompoundValue(1, c[1] + 1, 0);\nplot scan = c > 0;", "c", [short]), 29))
check("C 递归 · 引用 [2]", close_to(last("def s = if close > open then s[2] + 1 else 0;\nplot scan = s > 0;", "s", [stock()]), 40))
check("C 递归 · 运行中最高价(Max 自引用)", close_to(last("def m = Max(close, m[1]);\nplot scan = m > 0;", "m", [zig]), 15))
# 三值逻辑(与横截面引擎、仓内「空的比假的好」一致):中间缺一根开盘价,那根的 isBull 是「未知」,
# 连续计数从那里起就算不出 —— **不**照 ThinkScript 把 NaN 当 false 归零重数(那会静默给出一个偏小的假计数)。
# 直到出现一根真正的阴线(else 分支)才重置,之后重新数。
check("C 递归 · ⭐中间缺一根开盘价 → 之后一直算不出(不拿 0 顶替)",
      last("def s = if close > open then s[1] + 1 else 0;\nplot scan = s > 0;", "s",
           [stock(open_=lambda i: (None if i == 70 else 9.9))]) is None)
check("C 递归 · 缺开盘价之后出现真阴线 → 重置后重新数",
      close_to(last("def s = if close > open then s[1] + 1 else 0;\nplot scan = s > 0;", "s",
                    [stock(open_=lambda i: (None if i == 70 else (10.3 if i == 72 else 9.9)))]), 7))
check("C BarNumber · 真实根数", close_to(last("def b = BarNumber();\nplot scan = b > 0;", "b", [short]), 30))
check("C BarNumber · 满历史", close_to(last("def b = BarNumber();\nplot scan = b > 0;", "b", [ramp]), 80))


def brute_ema(xs, n):
    a = 2.0 / (n + 1)
    e = None
    for x in xs:
        e = x if e is None else a * x + (1 - a) * e
    return e


ema_in = stock(close=lambda i: 10 + math.sin(i / 3.0))
check("C ExpAverage · = 暴力递推(从第一根起算)", close_to(last("def e = ExpAverage(close[0], 5);\nplot scan = e > 0;", "e", [ema_in]), brute_ema(ema_in["close"], 5), 1e-9))
check("C ExpAverage · 历史不足 周期+30 → NaN", last("def e = ExpAverage(close[0], 5);\nplot scan = e > 0;", "e", [stock(close=lambda i: (None if i < 50 else 10.0))]) is None)
check("C ExpAverage · 历史刚够 周期+30 → 有值", last("def e = ExpAverage(close[0], 5);\nplot scan = e > 0;", "e", [stock(close=lambda i: (None if i < 45 else 10.0))]) is not None)
check("C MovingAverage · SIMPLE == Average", verdicts("plot scan = MovingAverage(AverageType.SIMPLE, close, 20) == Average(close, 20);", [ema_in]) == [True])
check("C MovingAverage · EXPONENTIAL == ExpAverage", verdicts("plot scan = MovingAverage(AverageType.EXPONENTIAL, close, 5) == ExpAverage(close, 5);", [ema_in]) == [True])

print("── C 三值逻辑 / 除零 ──")
noopen = stock(open_=[None] * 80)
check("C Kleene · ⭐假 且 未知 = 假", verdicts("plot scan = close[0] < 0 and open > 0;", [noopen]) == [False])
check("C Kleene · 真 或 未知 = 真", verdicts("plot scan = close[0] > 0 or open > 0;", [noopen]) == [True])
check("C Kleene · 真 且 未知 = 未知", verdicts("plot scan = close[0] > 0 and open > 0;", [noopen]) == [None])
check("C Kleene · 非 未知 = 未知", verdicts("plot scan = !(open[0] > 0);", [noopen]) == [None])
check("C Kleene · 未知与顺序无关", verdicts("plot scan = open > 0 and close[0] < 0;", [noopen]) == [False])
check("C 除零 · 结果算不出", verdicts("def r = close / (close - close[0]);\nplot scan = r > 0;", [ramp]) == [None])
check("C 除零 · 用户脚本里的 if cumRet > 0 分支保护", close_to(last("def cumRet = close / close[5] - 1;\ndef r = if cumRet > 0 then 1 / cumRet else 0;\nplot scan = r >= 0;", "r", [stock()]), 0))
check("C 比较 · NaN 参与比较 = NaN", verdicts("plot scan = open[0] >= 0;", [noopen]) == [None])
check("C 布尔算术 · true + true = 2", close_to(last("def x = (close[0] > 0) + (volume > 0);\nplot scan = x > 0;", "x", [ramp]), 2))

print("── C within / crosses ──")
spike = stock(close=lambda i: (20.0 if i == 77 else 10.0))
check("C within · 3 根前出现过 → within 5 真", verdicts("def b = close > 15;\nplot scan = b within 5 bars;", [spike]) == [True])
check("C within · within 2 假", verdicts("def b = close > 15;\nplot scan = b within 2 bars;", [spike]) == [False])
check("C within · 当根算在内", verdicts("def b = close > 15;\nplot scan = b within 1 bars;", [stock(close=lambda i: (20.0 if i == 79 else 10.0))]) == [True])
crossup = stock(close=lambda i: (9.0 if i < 79 else 12.0))          # 最后一根从均线下方跳到上方
check("C crosses · above 真", verdicts("plot scan = close crosses above Average(close, 5);", [crossup]) == [True])
check("C crosses · below 假", verdicts("plot scan = close crosses below Average(close, 5);", [crossup]) == [False])
check("C crosses · 任一方向 真", verdicts("plot scan = close crosses Average(close, 5);", [crossup]) == [True])
check("C crosses · 一直在上方 → 假(不是每根都算穿越)", verdicts("plot scan = close crosses above 5;", [ramp]) == [False])
check("C Crosses() 函数 = 中缀", verdicts("plot scan = Crosses(close, Average(close, 5), CrossingDirection.ABOVE) == (close crosses above Average(close, 5));", [crossup]) == [True])

print("── C 累计 / 统计 / 数学 ──")
check("C HighestAll · 含左侧 NaN", close_to(last("def h = HighestAll(close);\nplot scan = h > 0;", "h", [short]), 10))
check("C TotalSum · 只加真实根", close_to(last("def t = TotalSum(volume);\nplot scan = t > 0;", "t", [short]), 30 * 1e5))
check("C LowestAll", close_to(last("def l = LowestAll(close);\nplot scan = l > 0;", "l", [zig]), 9))
check("C StDev · 常数 = 0", close_to(last("def s = StDev(close, 20);\nplot scan = s >= 0;", "s", [stock()]), 0))
check("C StDev · 1..5 总体标准差 = sqrt(2)", close_to(last("def s = StDev(close, 5);\nplot scan = s > 0;", "s", [ramp]), math.sqrt(2)))
check("C Max/Min", verdicts("plot scan = Max(close, open) == close and Min(close, open) == open;", [stock()]) == [True])
check("C AbsValue/Sqrt/Power/Round", verdicts("plot scan = AbsValue(open - close) > 0.09 and Sqrt(Power(close, 2)) == close and Round(3.14159, 2) == 3.14 and Round(2.5) == 3;", [stock()]) == [True])
check("C Between 闭区间", verdicts("plot scan = Between(close, 10, 10) and !Between(close, 11, 12);", [stock()]) == [True])
check("C Floor/Ceil/Sign/Log/Exp", verdicts("plot scan = Floor(10.7) == 10 and Ceil(10.2) == 11 and Sign(-3) == -1 and Log(Exp(2)) == 2;", [stock()]) == [True])
check("C Sqrt 负数 → NaN", verdicts("plot scan = Sqrt(-1) > 0;", [stock()]) == [None])
check("C IsAscending / IsDescending", verdicts("plot scan = IsAscending(close, 5) and !IsDescending(close, 5);", [ramp]) == [True])
check("C hl2 / hlc3 / ohlc4", verdicts("plot scan = hl2 == 9.95 and AbsValue(hlc3 - 9.96667) < 0.001 and AbsValue(ohlc4 - 9.95) < 0.001;", [stock()]) == [True])
check("C yes / no 与 input 布尔", verdicts("input on = yes;\ninput off = no;\nplot scan = on and !off and close > 0;", [stock()]) == [True])
check("C 命名参数 = 位置参数", verdicts("plot scan = Average(length = 20, data = close) == Average(close, 20) and RSI(\"length\" = 9) == RSI(9);", [ema_in]) == [True])
check("C 大小写", verdicts("plot scan = AVERAGE(CLOSE[0], 5) == Average(close[0], 5) and SUM(Close > OPEN, 3) == 3;", [ramp]) == [True])
check("C Double.NaN / IsNaN", verdicts("def x = if close > 100 then 1 else Double.NaN;\nplot scan = IsNaN(x) and !IsNaN(close);", [stock()]) == [True])
check("C 多个 plot 取最后一个", verdicts("plot a = close[0] < 0;\nplot scan = close[0] > 0;", [stock()]) == [True])
check("C plot 数值非 0 为真", verdicts("def s = if close > open then s[1] + 1 else 0;\nplot scan = s;", [stock()]) == [True])
check("C plot 数值为 0 为假", verdicts("def s = if close < open then s[1] + 1 else 0;\nplot scan = s;", [stock()]) == [False])

print("── C 研究函数 ──")


def brute_rsi(closes, n):
    gains, losses = [], []
    for a, b in zip(closes[:-1], closes[1:]):
        d = b - a
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    ag = al = None
    for g, l in zip(gains, losses):
        ag = g if ag is None else (ag * (n - 1) + g) / n
        al = l if al is None else (al * (n - 1) + l) / n
    return 100 - 100 / (1 + ag / al)


rsi_in = stock(close=lambda i: 10 + math.sin(i / 2.0) + i * 0.02)
# 注:RSI(14) / RSI() 在横截面模式下映射成扫描源的 RSI 字段;带上 price 参数就走序列引擎
check("C RSI · = 暴力 Wilder 递推", close_to(last("def r = RSI(14, close);\nplot scan = r > 0;", "r", [rsi_in]), brute_rsi(rsi_in["close"], 14), 1e-9))
check("C RSI · 一直涨 = 100", close_to(last("def r = RSI(14, close);\nplot scan = r > 0;", "r", [ramp]), 100))
check("C RSI · 一直平 = 50", close_to(last("def r = RSI(14, close);\nplot scan = r > 0;", "r", [stock()]), 50))
check("C RSI · 默认 14 / 默认 close", verdicts("plot scan = RSI(\"price\" = close) == RSI(14, close) and RSI(length = 14, price = close[0]) == RSI(14, close);", [rsi_in]) == [True])
check("C ATR · 固定振幅、无跳空 = 振幅", close_to(last("def a = ATR(14);\nplot scan = a > 0;", "a", [stock(high=10.5, low=8.5, close=9.5, open_=9.0)]), 2.0))
check("C MACD · 一路上涨 Value > 0", last("def v = MACD().Value;\nplot scan = v > 0;", "v", [stock(n=120, close=lambda i: 10 + i * 0.1)]) > 0)
check("C MACD · Diff = Value - Avg", verdicts("plot scan = AbsValue(MACD().Diff - (MACD().Value - MACD().Avg)) < 0.000001;", [stock(n=120, close=lambda i: 10 + math.sin(i / 4.0))]) == [True])
check("C MACD · 默认参数 = (12,26,9)", verdicts("plot scan = MACD().Diff == MACD(12, 26, 9).Diff;", [stock(n=120, close=lambda i: 10 + math.sin(i / 4.0))]) == [True])
check("C MACD · 不写属性默认 .Diff", verdicts("plot scan = MACD() == MACD().Diff;", [stock(n=120, close=lambda i: 10 + math.sin(i / 4.0))]) == [True])
check("C BollingerBands · 常数序列 上=中=下", verdicts("plot scan = BollingerBands().UpperBand == 10 and BollingerBands().MidLine == 10 and BollingerBands().LowerBand == 10;", [stock()]) == [True])
check("C BollingerBands · 有波动 上>中>下", verdicts("plot scan = BollingerBands().UpperBand > BollingerBands().MidLine and BollingerBands().MidLine > BollingerBands().LowerBand;", [ema_in]) == [True])
check("C BollingerBands · 中轨 = 20 日均线", verdicts("plot scan = BollingerBands().MidLine == Average(close, 20);", [ema_in]) == [True])
check("C VolumeAvg(20).VolAvg == Average(volume, 20)", verdicts("plot scan = VolumeAvg(20).VolAvg == Average(volume, 20);", [vol]) == [True])

print("── C 快照字段混用 ──")
check("C 快照 · 今天的值当常量", verdicts("plot scan = market_cap_basic > 1000000000 and close > close[1];", [ramp], {"market_cap_basic": np.array([5e9])}) == [True])
check("C 快照 · 回溯时没有历史 → 算不出", verdicts("plot scan = market_cap_basic > 1000000000 and close > close[1];", [ramp], {"market_cap_basic": None}) == [None])
check("C 快照 · 缺值 NaN → 算不出", verdicts("plot scan = market_cap_basic > 1000000000 and close > close[1];", [ramp], {"market_cap_basic": np.array([NAN])}) == [None])
check("C 快照 · 假 且 缺值 = 假", verdicts("plot scan = market_cap_basic > 1000000000 and close[0] < 0;", [ramp], {"market_cap_basic": np.array([NAN])}) == [False])

print("── C 求值稳定性 ──")
c1, r1 = run(USER_SCRIPT, [ramp])
c2, r2 = run(USER_SCRIPT, [ramp])
check("C 同一输入两次求值结果一致", all((a == b) or (a != a and b != b) for a, b in zip(r1["last"]["cumRet"], r2["last"]["cumRet"])))

# ═══════════════════════════════════════════════════════════════
# D · 用户脚本端到端
# ═══════════════════════════════════════════════════════════════
print("\n── D 用户脚本(猎杀 FOMO)端到端 ──")
_c5 = [10.5, 11.0, 12.5, 14.5, 18.5]
_o5 = [10.2, 10.7, 12.0, 14.0, 18.0]
_v5 = [1.2e5, 1.5e5, 2e5, 3e5, 4e6]
hit = stock(close=lambda i: 10.0 if i < 75 else _c5[i - 75], open_=lambda i: 9.9 if i < 75 else _o5[i - 75],
            high=lambda i: 10.1 if i < 75 else _c5[i - 75] + 0.2, low=lambda i: 9.8 if i < 75 else _o5[i - 75] - 0.2,
            volume=lambda i: 1e5 if i < 75 else _v5[i - 75])
miss = stock(close=hit["close"], open_=[o if i != 79 else 19.0 for i, o in enumerate(hit["open"])],
             high=hit["high"], low=hit["low"], volume=hit["volume"])
unknown = stock(close=hit["close"], open_=[None] * 80, high=hit["high"], low=hit["low"], volume=hit["volume"])
c, res = run(USER_SCRIPT, [hit, miss, unknown])
vd = [None if v != v else bool(v) for v in res["verdict"]]
check("D ⭐三只票:命中 / 不满足 / 缺开盘价算不出", vd == [True, False, None], vd)
check("D 命中票 bullStreak = 80(整段都是阳线)", close_to(res["last"]["bullStreak"][0], 80))
check("D 命中票 cumRet = 0.85", close_to(res["last"]["cumRet"][0], 0.85))
check("D 命中票 bearCount = 0", close_to(res["last"]["bearCount"][0], 0))
check("D 命中票 last2Ratio ≈ 0.565", close_to(res["last"]["last2Ratio"][0], 0.48 / 0.85, 1e-6))
check("D 命中票 volMA20 = 118500", close_to(res["last"]["volMA20"][0], 118500))
check("D 命中票 volMax60 = 300000", close_to(res["last"]["volMax60"][0], 3e5))
check("D 命中票 amount = 7.4e7", close_to(res["last"]["amount"][0], 7.4e7))
check("D 不满足票 bullStreak = 0(最后一根阴线)", close_to(res["last"]["bullStreak"][1], 0))
check("D 缺开盘价的票 bullStreak 是 NaN(不是 0)", res["last"]["bullStreak"][2] != res["last"]["bullStreak"][2])
check("D 计划:用到 open/close/volume", c.series.needs == {"open", "close", "volume"}, c.series.needs)
check("D 计划:递归定义 bullStreak", c.series.rec == ["bullStreak"])
check("D 计划:深度 60(Highest(volume[1], 60))", c.series.depth == 60, c.series.depth)
check("D 计划:窗口 ≥ 250(有递归)", c.series.window >= 250, c.series.window)
check("D 计划:input 8 个进常量表", all(k in c.series.consts for k in ("lookback", "minBullDays", "minRet", "minAmount")))
check("D 计划:cumRet 不是常量", "cumRet" not in c.series.consts)
d = ss.describe(c.series)
check("D describe 里点名递归与函数", "bullStreak" in d and "sum" in d and "开盘价" in d, d)
# 参数改一下,结果要跟着变(minRet 改 0.9 → cumRet 0.85 不满足)
c3, res3 = run(USER_SCRIPT.replace("input minRet = 0.80;", "input minRet = 0.90;"), [hit])
check("D 改 input 影响结果(minRet 0.9 → 不满足)", [None if v != v else bool(v) for v in res3["verdict"]] == [False])

# ═══════════════════════════════════════════════════════════════
# E · 条件行 / bars_matrix / 前端往返
# ═══════════════════════════════════════════════════════════════
print("\n── E decompose / bars_matrix ──")
dd = sd.decompose(USER_SCRIPT, c, has, SMA, EMA, RSI)
kinds = {x["name"]: x["kind"] for x in dd["conditions"]}
check("E decompose · input 行 kind=input", kinds["lookback"] == "input" and kinds["minRet"] == "input")
check("E decompose · def 行 kind=def", kinds["isBull"] == "def" and kinds["bullStreak"] == "def")
bools = {x["name"]: x["is_bool"] for x in dd["conditions"]}
check("E decompose · 布尔条件识别", bools["accel"] and bools["volSpike"] and bools["volUp3"])
check("E decompose · 数值 / input / 递归 不是布尔", not bools["cumRet"] and not bools["lookback"] and not bools["bullStreak"] and not bools["last2Ratio"])
# ⭐ 2026-09-15 改口径:原来这两条断言是「isBull 是条件」「plot 含比较式 → custom」—— 那正是用户截图里的 bug:
# 界面「同时满足」下面列着 isBull(收盘价>开盘价)和 isBear(收盘价<开盘价)两条互斥条件,真正 plot 里的
# bullStreak >= minBullDays 等 8 项一条没列。现在:被别的 def 引用的布尔 def 是中间定义;plot 按顶层 and 拆成条件行
check("E decompose ⭐ 中间布尔定义(isBull / isBear / isMaxRet)不是条件", not bools["isBull"] and not bools["isBear"] and not bools["isMaxRet"])
check("E decompose ⭐ plot 含比较式 → combine=all 且 plot 原文保留", dd["combine"] == "all" and "bullStreak >= minBullDays" in dd["plot_expr"], dd["combine"])
_rows = [x for x in dd["conditions"] if x["is_bool"]]
_row_names = {x["name"] for x in _rows}
check("E decompose ⭐ 条件行正好是 plot 的 8 项", len(_rows) == 8 and len(dd["plot_order"]) == 8 and set(dd["plot_order"]) == _row_names, dd["plot_order"])
_terms = [x for x in dd["conditions"] if x["kind"] == "term"]
check("E decompose ⭐ 表达式项原文逐字", [x["expr"] for x in _terms] == ["bullStreak >= minBullDays", "bearCount <= maxBearInWindow", "cumRet >= minRet", "last2Ratio >= last2RatioMin", "amount >= minAmount"], [x["expr"] for x in _terms])
check("E decompose · 裸名字项进 plot_refs 且按 plot 顺序", dd["plot_refs"] == ["accel", "volUp3", "volSpike"] and dd["plot_order"][3] == "accel", dd["plot_order"])
check("E decompose · 表达式项名字带 #(不会与 def 撞名)", all("#" in x["name"] for x in _terms) and _terms[0]["title"] == "bullStreak")
_ttoks = {x["expr"]: "".join(t["t"] for t in x["tokens"]) for x in _terms}
check("E decompose · 参数引用带当前值", "minBullDays(3)" in _ttoks["bullStreak >= minBullDays"] and "minRet(0.80)" in _ttoks["cumRet >= minRet"], _ttoks)
_dd2 = sd.decompose("def a = close > open[1];\ndef b = volume > 0;\nplot scan = a and b;",
                    compile("def a = close > open[1];\ndef b = volume > 0;\nplot scan = a and b;"), has, SMA, EMA, RSI)
check("E decompose · 纯名字 and 链 → combine=all", _dd2["combine"] == "all" and _dd2["plot_refs"] == ["a", "b"], _dd2["plot_refs"])
toks = {x["name"]: "".join(t["t"] for t in x["tokens"]) for x in dd["conditions"]}
check("E decompose · if 显示成 如果/则/否则", "如果" in toks["bullStreak"] and "否则" in toks["bullStreak"], toks["bullStreak"])
check("E decompose · 偏移显示 [1]", "[1]" in toks["bullStreak"])
check("E decompose · 函数原样显示", toks["bearCount"].startswith("Sum("), toks["bearCount"])
check("E decompose · 序列中文名", "收盘价" in toks["isBull"] and "开盘价" in toks["isBull"], toks["isBull"])
check("E decompose · 数字 token 带位置(可内联编辑)", any(t["k"] == "num" and "s" in t for t in next(x for x in dd["conditions"] if x["name"] == "volMA20")["tokens"]))
check("E decompose · rec 行 kind=rec", {x["name"]: x["kind"] for x in sd.decompose("rec s = if close > open then s[1] + 1 else 0;\nplot scan = s > 2;", compile("rec s = if close > open then s[1] + 1 else 0;\nplot scan = s > 2;"), has, SMA, EMA, RSI)["conditions"]}["s"] == "rec")
check("E decompose · 横截面脚本 kind=def 且行为不变", all(x["kind"] == "def" for x in sd.decompose("def a = close > 20;\nplot scan = a;", compile("def a = close > 20;\nplot scan = a;"), has, SMA, EMA, RSI)["conditions"]))
check("E decompose ⭐ 引用显示名字,不内联成没括号的一串", toks["isMaxRet"] == "ret大于等于maxRet" and "Highest收盘价" not in toks["accel"], (toks["isMaxRet"], toks["accel"]))
_vol = next(x for x in dd["conditions"] if x["name"] == "volUp3")
check("E decompose ⭐ K 线偏移不是可编辑数字", not any(t["k"] == "num" for t in _vol["tokens"]) and "[1]" in toks["volUp3"], _vol["tokens"])
check("E decompose · 窗口长度仍可内联编辑", any(t["k"] == "num" and t["t"] == "20" for t in next(x for x in dd["conditions"] if x["name"] == "volMA20")["tokens"]))


def _dec(s):
    return sd.decompose(s, compile(s), has, SMA, EMA, RSI)


# 按写法类别:plot 的各种形状
_d = _dec("def a = close > close[1];\nplot scan = a or volume > volume[1];")
_t = [x for x in _d["conditions"] if x["kind"] == "term"]
check("E plot 形状 · 单个 or 表达式 → 一条 term,回写要补括号", _d["combine"] == "all" and len(_t) == 1 and _t[0]["paren"] and not next(x for x in _d["conditions"] if x["name"] == "a")["is_bool"])
_d = _dec("def a = close > close[1];\nplot scan = (a or volume > volume[1]) and close > 5;")
_t = [x for x in _d["conditions"] if x["kind"] == "term"]
check("E plot 形状 · 已带括号的 or 项不再补括号(往返不叠括号)", len(_t) == 2 and _t[0]["expr"] == "(a or volume > volume[1])" and not _t[0]["paren"], [(x["expr"], x["paren"]) for x in _t])
_d = _dec("def a = close > close[1];\nplot scan = a within 3 bars;")
check("E plot 形状 · within 单项可拆", _d["combine"] == "all" and len([x for x in _d["conditions"] if x["kind"] == "term"]) == 1)
_d = _dec("plot scan = if close > open and volume > 0 then close > close[1] else no;")
check("E plot 形状 · 顶层 if(里面有 and)拆不开 → custom", _d["combine"] == "custom" and not any(x["kind"] == "term" for x in _d["conditions"]), _d["combine"])
_d = _dec("def a = close > open;\ndef b = Sum(a, 5) >= 3;\ndef c = volume > volume[1];\nplot scan = b;")
_b = {x["name"]: x["is_bool"] for x in _d["conditions"]}
check("E plot 形状 · 没进 plot 也没人引用的布尔 def 仍是(停用的)条件", _b == {"a": False, "b": True, "c": True} and _d["plot_refs"] == ["b"], _b)
_d = _dec("def a = close > close[1];\nplot scan = a and a[1];")
check("E plot 形状 · 同一 def 既是裸名字项又被别的项引用 → 仍是条件", next(x for x in _d["conditions"] if x["name"] == "a")["is_bool"] and _d["plot_refs"] == ["a"])
_d = _dec("def a = close > 20;\ndef b = volume > 1000;\nplot scan = a and b;")
check("E plot 形状 · 横截面纯名字链:和以前完全一样(没有 term)", _d["combine"] == "all" and _d["plot_refs"] == ["a", "b"] and not any(x["kind"] == "term" for x in _d["conditions"]))
_d = _dec("def a = close > 20;\nplot scan = a and volume > 1000;")
check("E plot 形状 · 横截面混写:名字项 + 表达式项", _d["plot_order"] == ["a", "scan#1"] and next(x for x in _d["conditions"] if x["kind"] == "term")["expr"] == "volume > 1000", _d["plot_order"])
_d = _dec("def a = close > close[1];\nplot scan = a # 注释\n  and volume > volume[1];")
check("E plot 形状 · plot 跨行带注释,原文不含注释", [x["expr"] for x in _d["conditions"] if x["kind"] == "term"] == ["volume > volume[1]"])
_d = _dec("def a = close > close[1];\nplot scan = a && Highest(volume[1], 3) > 0;")
check("E plot 形状 · && 也拆,函数括号里的逗号不影响", [x["expr"] for x in _d["conditions"] if x["kind"] == "term"] == ["Highest(volume[1], 3) > 0"])
_t = next(x for x in _d["conditions"] if x["kind"] == "term")
check("E plot 形状 · term 里的数字位置相对 term 原文", all(_t["expr"][t["s"]:t["e"]] == t["t"] for t in _t["tokens"] if t["k"] == "num"))

# 往返:build_script(条件行) 再编译,结果与原脚本逐只一致;停用一项 = plot 里去掉那一项
_rt = sd.build_script(dd["conditions"], dd["plot_name"])
check("E 往返 · build_script 含 input 关键字与全部 term", "input lookback = 5;" in _rt and "bullStreak >= minBullDays and bearCount <= maxBearInWindow" in _rt, _rt[-300:])
check("E 往返 · 回写脚本再拆条件行不变", [x["name"] for x in _dec(_rt)["conditions"] if x["is_bool"]] == [x["name"] for x in dd["conditions"] if x["is_bool"]])
_or = _dec("def a = close > close[1];\nplot scan = a or volume > volume[1];")
check("E 往返 · or 项回写补括号后仍能编译", "(a or volume > volume[1])" in sd.build_script(_or["conditions"] + [{"name": "z", "expr": "close > 1", "is_bool": True}], "scan") and compile(sd.build_script(_or["conditions"] + [{"name": "z", "expr": "close > 1", "is_bool": True}], "scan")) is not None)
check("E 跳过的语句进 notes",any("AddLabel" in n for n in compile("def a = close > open;\nAddLabel(yes, \"x\");\nplot scan = a;").notes))
check("E 多个 plot 进 notes", any("plot" in n for n in compile("plot a = close > open;\nplot scan = close > close[1];").notes))

# bars_matrix:模拟 screen_asof 的缓存结构
from datetime import date, timedelta  # noqa: E402
D = [date(2026, 1, 1) + timedelta(days=i) for i in range(10)]
old4 = np.array([[10 + i, 11 + i, 9 + i, 1000.0] for i in range(10)], dtype=float)         # 老格式 4 列(没有 open)
new5 = np.array([[10 + i, 11 + i, 9 + i, 1000.0, 9.5 + i] for i in range(10)], dtype=float)
# BBB:只有最后 6 天(次新股);DDD:最后一天没有收盘(停牌)
store = {"codes": {"AAA": (D, old4), "BBB": (D[4:], new5[4:]), "CCC": (D, new5), "DDD": (D[:9], new5[:9])}}
codes, mats, short_ = ss.bars_matrix(store, D[-1], 8)
check("E bars_matrix · 只收当天有收盘的票(DDD 最后一天没有)", sorted(codes) == ["AAA", "BBB", "CCC"], codes)
i_a, i_b, i_c = codes.index("AAA"), codes.index("BBB"), codes.index("CCC")
check("E bars_matrix · 老格式没有 open → NaN", np.isnan(mats["open"][i_a]).all())
check("E bars_matrix · 新格式 open 对上", close_to(mats["open"][i_c, -1], 18.5))
check("E bars_matrix · 右对齐,最后一列 = 最新收盘", close_to(mats["close"][i_a, -1], 19) and close_to(mats["close"][i_b, -1], 19))
check("E bars_matrix · 历史不足左侧补 NaN", np.isnan(mats["close"][i_b, :2]).all() and not np.isnan(mats["close"][i_b, 2:]).any())
check("E bars_matrix · short 记录真实根数", short_[i_b] == 6 and short_[i_a] == 8)

# ═══════════════════════════════════════════════════════════════
# F · 性能
# ═══════════════════════════════════════════════════════════════
print("\n── F 性能 ──")
rng = np.random.RandomState(7)
N, W = 4000, 252
closeM = 10 + np.cumsum(rng.normal(0, 0.1, size=(N, W)), axis=1)
openM = closeM + rng.normal(0, 0.05, size=(N, W))
big = {"close": closeM, "open": openM, "high": np.maximum(closeM, openM) + 0.1,
       "low": np.minimum(closeM, openM) - 0.1, "volume": rng.uniform(5e4, 5e5, size=(N, W))}
cc = compile(USER_SCRIPT)
t0 = time.time()
rr = ss.evaluate(cc, big, {})
dt = time.time() - t0
check(f"F 4000 只 × 252 根 用户脚本 {dt:.2f}s(< 8s)", dt < 8.0)
check("F 结果形状", rr["verdict"].shape == (N,))
t0 = time.time()
rr2 = ss.evaluate(compile("plot scan = close > Average(close, 20) and RSI() < 30 and volume > Highest(volume[1], 60) * 1.5 and MACD().Diff > 0 and close > BollingerBands().UpperBand;"), big, {})
dt2 = time.time() - t0
check(f"F 4000 只 × 252 根 研究函数组合 {dt2:.2f}s(< 8s)", dt2 < 8.0)


# ═══════════════════════════════════════════════════════════════
# G · 条件宿主:plot 只写一个名字、条件都在那条 def 里(2026-09-15 用户第二份猎杀 FOMO)
# ═══════════════════════════════════════════════════════════════
# 用户截图:界面「同时满足 1 个条件」一整行 FOMO_Setup,脚本里写明的是 7 个条件。引擎算得没错(下面 G 值 · 对暴力),
# 错在条件行:decompose 只把 plot 的顶层 and 项当条件,plot 是一个裸名字就只有一项。
print("\n── G 条件宿主(猎杀 FOMO 第二份)──")
USER_SCRIPT2 = """# 猎杀FOMO策略 Setup 扫描器
# 用途：扫描连续强势上涨 + 加速 + 天量高潮的潜在做空标的

# ==================== 可调参数 ====================
input minStreak = 3;                  # 最少连续阳线天数
input minTotalReturn = 0.80;          # 最小总涨幅（小市值建议1.0，中大市值0.6~0.8）
input volMultiplier = 5.0;            # 高潮日成交量 ≥ 20日均量的倍数
input maxRedRatio = 0.20;             # 波段内允许的最大阴线比例
input lookbackVol = 20;               # 成交量均线周期
input accelerateRatio = 1.2;          # 加速确认：最后一日涨幅 ≥ 前一日 × 此倍数

# ==================== 基础计算 ====================
def isGreen = close > close[1];
def isRed   = close < close[1];
def dailyReturn = (close - close[1]) / close[1];

# 连续阳线长度
def greenStreak = if isGreen then greenStreak[1] + 1 else 0;

# 波段起点价格（连续阳线开始前一日的收盘价）
def streakStartPrice = if greenStreak == 1 then close[1] else streakStartPrice[1];

# 波段总涨幅
def totalReturn = if greenStreak >= 1 then (close / streakStartPrice) - 1 else 0;

# 波段内阴线数量（用于干净度）
def redCountInStreak = if greenStreak == 1 then 0
                       else if isRed then redCountInStreak[1] + 1
                       else redCountInStreak[1];

def redRatio = if greenStreak > 0 then redCountInStreak / greenStreak else 1;

# 加速特征
def isAccelerating = dailyReturn >= dailyReturn[1] * accelerateRatio and dailyReturn > 0;

# 最后一日是否接近波段最大涨幅（简化加速确认）
def maxReturnInStreak = if greenStreak == 1 then dailyReturn
                        else Max(dailyReturn, maxReturnInStreak[1]);
def isLastMax = dailyReturn >= maxReturnInStreak * 0.95;

# 成交量条件
def volMA = Average(volume, lookbackVol);
def climaxVolRatio = volume / volMA;
def volIncreasing = volume > volume[1] and volume[1] > volume[2];

# ==================== 最终Setup条件 ====================
def FOMO_Setup =
    greenStreak >= minStreak and
    totalReturn >= minTotalReturn and
    (isAccelerating or isLastMax) and
    redRatio <= maxRedRatio and
    climaxVolRatio >= volMultiplier and
    volIncreasing and
    isGreen;                    # 当天必须是阳线

# 扫描条件（必须放在最后）
plot scan = FOMO_Setup;
"""
c2 = compile(USER_SCRIPT2)
check("G 编译 · 时间序列模式", c2.series is not None)
check("G 编译 · 递归定义 4 个", c2.series.rec == ["greenStreak", "streakStartPrice", "redCountInStreak", "maxReturnInStreak"], c2.series.rec)
d2 = sd.decompose(USER_SCRIPT2, c2, has, SMA, EMA, RSI)
rows2 = [x for x in d2["conditions"] if x["is_bool"]]
names2 = [x["name"] for x in d2["conditions"]]
check("G ⭐ term_host = FOMO_Setup", d2["term_host"] == "FOMO_Setup", d2.get("term_host"))
check("G ⭐ 条件行 = FOMO_Setup 的 7 项(不是 1 行)", len(rows2) == 7 and len(d2["plot_order"]) == 7, [x["name"] for x in rows2])
check("G 宿主本身不在条件列表里", "FOMO_Setup" not in names2)
_terms2 = [x for x in d2["conditions"] if x["kind"] == "term"]
check("G ⭐ 5 个表达式项原文与脚本一致", [x["expr"] for x in _terms2] == ["greenStreak >= minStreak", "totalReturn >= minTotalReturn",
      "(isAccelerating or isLastMax)", "redRatio <= maxRedRatio", "climaxVolRatio >= volMultiplier"], [x["expr"] for x in _terms2])
check("G 表达式项名字是 FOMO_Setup#k", [x["name"] for x in _terms2] == [f"FOMO_Setup#{k}" for k in range(1, 6)])
check("G 裸名字项 volIncreasing / isGreen 是 def 条件行且启用", d2["plot_refs"] == ["volIncreasing", "isGreen"]
      and all(x["is_bool"] for x in d2["conditions"] if x["name"] in ("volIncreasing", "isGreen")))
check("G plot_order 与脚本里的先后一致", d2["plot_order"] == ["FOMO_Setup#1", "FOMO_Setup#2", "FOMO_Setup#3", "FOMO_Setup#4",
      "FOMO_Setup#5", "volIncreasing", "isGreen"], d2["plot_order"])
_b2 = {x["name"]: x["is_bool"] for x in d2["conditions"]}
check("G 中间定义不是条件(isAccelerating / isLastMax / isRed / dailyReturn / volMA)",
      not any(_b2[n] for n in ("isAccelerating", "isLastMax", "isRed", "dailyReturn", "volMA", "climaxVolRatio")))
check("G 已带括号的 or 项不再要求补括号", [x["paren"] for x in _terms2] == [False] * 5)
check("G input 6 个 kind=input", sum(1 for x in d2["conditions"] if x["kind"] == "input") == 6)


def _rebuild(dd):
    """照前端 buildScript 的规则回写(input/rec 关键字、宿主按原结构)。"""
    lines, on = [], []
    byname = {x["name"]: x for x in dd["conditions"]}
    for x in dd["conditions"]:
        if x["kind"] == "term":
            continue
        kw = x["kind"] if x["kind"] in ("input", "rec") else "def"
        lines.append(f"{kw} {x['name']} = {x['expr']};")
    for n in dd["plot_order"]:
        x = byname[n]
        on.append((f"({x['expr']})" if x.get("paren") else x["expr"]) if x["kind"] == "term" else n)
    if dd.get("term_host"):
        lines.append(f"def {dd['term_host']} = " + " and ".join(on) + ";")
        lines.append(f"plot {dd['plot_name']} = {dd['term_host']};")
    else:
        lines.append(f"plot {dd['plot_name']} = " + " and ".join(on) + ";")
    return "\n".join(lines)


_rs2 = _rebuild(d2)
check("G 回写:def FOMO_Setup = 7 项;plot scan = FOMO_Setup", "def FOMO_Setup = greenStreak >= minStreak and totalReturn >= minTotalReturn and "
      "(isAccelerating or isLastMax) and redRatio <= maxRedRatio and climaxVolRatio >= volMultiplier and volIncreasing and isGreen;" in _rs2
      and _rs2.rstrip().endswith("plot scan = FOMO_Setup;"), _rs2[-300:])
_c2b = compile(_rs2)
_d2b = sd.decompose(_rs2, _c2b, has, SMA, EMA, RSI)
check("G ⭐ 往返:再解析一次条件行一模一样", [x["expr"] for x in _d2b["conditions"]] == [x["expr"] for x in d2["conditions"]]
      and _d2b["term_host"] == "FOMO_Setup" and _d2b["plot_order"] == d2["plot_order"])
check("G 往返两次不漂移", _rebuild(_d2b) == _rs2)
check("G 后端 build_script 不传 plot_order 时老行为不变(按列表顺序)",
      sd.build_script([{"name": "b", "expr": "close > 1", "is_bool": True}, {"name": "a", "expr": "close > 2", "is_bool": True}]).endswith("plot scan = b and a;"))
_bs = sd.build_script([dict(x, enabled=True) for x in d2["conditions"]], "scan", None, d2["term_host"], d2["plot_order"])
check("G 后端 build_script 与前端同规则(宿主按原结构)", "def FOMO_Setup = greenStreak >= minStreak and" in _bs and _bs.endswith("plot scan = FOMO_Setup;"), _bs[-200:])


# 值 · 对暴力(ThinkScript 语义逐根)
def _brute2(cl, vo):
    g = sp = rc = mx = 0.0
    res = None
    for i in range(1, len(cl)):
        isG, isR = cl[i] > cl[i - 1], cl[i] < cl[i - 1]
        dr = (cl[i] - cl[i - 1]) / cl[i - 1]
        g = g + 1 if isG else 0
        sp = cl[i - 1] if g == 1 else sp
        rc = 0 if g == 1 else (rc + 1 if isR else rc)
        mx = dr if g == 1 else max(dr, mx)
        if i == len(cl) - 1 and i >= 20:
            tr = (cl[i] / sp) - 1 if g >= 1 else 0
            rr = rc / g if g > 0 else 1
            dr1 = (cl[i - 1] - cl[i - 2]) / cl[i - 2]
            acc = dr >= dr1 * 1.2 and dr > 0
            cvr = vo[i] / (sum(vo[i - 19:i + 1]) / 20)
            vinc = vo[i] > vo[i - 1] > vo[i - 2]
            res = {"greenStreak": g, "totalReturn": tr, "redRatio": rr, "maxReturnInStreak": mx, "climaxVolRatio": cvr,
                   "FOMO_Setup": float(g >= 3 and tr >= 0.8 and (acc or dr >= mx * 0.95) and rr <= 0.2 and cvr >= 5.0 and vinc and isG)}
    return res


import random as _rnd  # noqa: E402
_rnd.seed(11)
_W2 = c2.series.window
_sts = []
for k in range(90):
    n = _rnd.choice([_W2, _W2, 120, 60])
    px, cl, vo = 10.0, [], []
    for i in range(n):
        if k % 3 == 0 and i >= n - 5:
            px *= 1 + 0.08 * (i - (n - 6)); vo.append(1e5 * 3 ** (i - (n - 6)))
        else:
            px *= 1 + _rnd.uniform(-0.04, 0.045); vo.append(_rnd.uniform(5e4, 2e5))
        cl.append(px)
    _sts.append((cl, vo))
_cm = np.full((len(_sts), _W2), np.nan); _vm = np.full((len(_sts), _W2), np.nan)
for r_, (cl, vo) in enumerate(_sts):
    _cm[r_, _W2 - len(cl):] = cl; _vm[r_, _W2 - len(vo):] = vo
_bars2 = {"close": _cm, "volume": _vm, "high": _cm * 1.01, "low": _cm * 0.99, "open": _cm}
_e2 = ss.evaluate(c2, {k: v.copy() for k, v in _bars2.items()}, {})
_bad = []
_hits = 0
for r_, (cl, vo) in enumerate(_sts):
    b = _brute2(cl, vo)
    _hits += int(b["FOMO_Setup"])
    for nm, bv in b.items():
        ev = _e2["last"][nm][r_] if nm != "FOMO_Setup" else _e2["verdict"][r_]
        if ev != ev or abs(ev - bv) > 1e-9 * max(1.0, abs(bv)):
            _bad.append((r_, nm, ev, bv))
check("G ⭐ 值 · 90 只合成票逐项对暴力(连续阳线 / 波段起点 / 总涨幅 / 阴线比例 / 波段最大涨幅 / 量比 / 最终结果)", not _bad, _bad[:3])
check("G 值 · 合成数据里命中与不命中都有(用例确实测到东西)", 0 < _hits < len(_sts), _hits)
_e2b = ss.evaluate(_c2b, {k: v.copy() for k, v in _bars2.items()}, {})
check("G 值 · 往返后的脚本结果完全一致", np.array_equal(np.nan_to_num(_e2["verdict"], nan=-1), np.nan_to_num(_e2b["verdict"], nan=-1)))

# 应当不展开的写法(反例,只加不删)
_hs = "def a = close > close[1];\ndef b = close[1] > close[2];\ndef both = a and b;\ndef x = if both then 1 else 0;\nplot scan = both;"
_dh = sd.decompose(_hs, compile(_hs), has, SMA, EMA, RSI)
check("G 反例 · 宿主被别的定义引用 → 不展开(它是中间量)", _dh["term_host"] == "" and [x["name"] for x in _dh["conditions"] if x["is_bool"]] == ["both"], _dh["plot_order"])
_hs = "def up = close > close[1];\nplot scan = up;"
_dh = sd.decompose(_hs, compile(_hs), has, SMA, EMA, RSI)
check("G 反例 · 宿主只有一项 → 不展开", _dh["term_host"] == "" and _dh["plot_order"] == ["up"])
_hs = "def s = close > close[1] or close[1] > close[2];\nplot scan = s;"
_dh = sd.decompose(_hs, compile(_hs), has, SMA, EMA, RSI)
check("G 反例 · 宿主顶层是 or → 不展开", _dh["term_host"] == "" and _dh["plot_order"] == ["s"])
_hs = "def s = close > close[1] and close[1] > close[2];\nplot scan = s and volume > 100000;"
_dh = sd.decompose(_hs, compile(_hs), has, SMA, EMA, RSI)
check("G 反例 · plot 自己有多项 → 按 plot 拆,不找宿主", _dh["term_host"] == "" and len(_dh["plot_order"]) == 2)
_hs = "def s = if close > close[1] then close[1] > close[2] and close > 5 else close > 1;\nplot scan = s;"
_dh = sd.decompose(_hs, compile(_hs), has, SMA, EMA, RSI)
check("G 反例 · 宿主顶层是 if(里面的 and 不能拆)→ 不展开", _dh["term_host"] == "")
_hs = "def c1 = close > 10;\ndef c2 = volume > 100000;\ndef all_ = c1 and c2;\nplot scan = all_;"
_ch = compile(_hs)
_dh = sd.decompose(_hs, _ch, has, SMA, EMA, RSI)
check("G 横截面脚本同样展开:all_ = c1 and c2 → 条件行 c1 / c2", _ch.series is None and _dh["term_host"] == "all_"
      and _dh["plot_refs"] == ["c1", "c2"] and "all_" not in [x["name"] for x in _dh["conditions"]], _dh)


# ═══════════════════════════════════════════════════════════════
# H · 审计补测(2026-09-15):常量 plot / 全停用宿主 / 嵌套括号 / 多个 plot / 重名 / 宿主不在最后 / 注释与 emoji
# ═══════════════════════════════════════════════════════════════
print("\n── H 审计补测 ──")


def _dd(src):
    cc = compile(src)
    return cc, sd.decompose(src, cc, has, SMA, EMA, RSI)


_c, _d = _dd("def a = close > close[1];\ndef b = volume > volume[1];\nplot scan = false;")
check("H ⭐ plot scan = false:不出条件行(原来多一条启用的「false」)", not any(x["kind"] == "term" for x in _d["conditions"]) and _d["plot_order"] == [], _d["plot_order"])
check("H plot scan = false:combine=all,a / b 是停用的条件", _d["combine"] == "all" and _d["plot_refs"] == []
      and all(x["is_bool"] for x in _d["conditions"] if x["name"] in ("a", "b")))
_c, _d = _dd("def a = close > close[1];\nplot scan = no;")
check("H plot scan = no 与 false 同样处理", _d["plot_order"] == [] and _d["combine"] == "all")
_c, _d = _dd("def a = close > close[1];\nplot scan = yes;")
check("H plot scan = yes(全部放行)按自定义组合原样保留", _d["combine"] == "custom" and _d["plot_expr"] == "yes" and _d["plot_order"] == [])
_c, _d = _dd("def a = close > close[1];\ndef b = volume > volume[1];\ndef H1 = false;\nplot scan = H1;")
check("H ⭐ 宿主全停用 def H1 = false:仍是宿主、没有条件行、H1 不单列", _d["term_host"] == "H1" and _d["plot_order"] == []
      and "H1" not in [x["name"] for x in _d["conditions"]] and _d["combine"] == "all", _d)
check("H 宿主全停用:a / b 是停用的条件(能重新打开)", all(x["is_bool"] for x in _d["conditions"] if x["name"] in ("a", "b")) and _d["plot_refs"] == [])
_c, _d = _dd("def H1 = false;\ndef x = if H1 then 1 else 0;\nplot scan = H1;")
check("H 反例 · 值为 false 的定义被别处引用 → 不当宿主", _d["term_host"] == "")

_c, _d = _dd("def a = close > close[1];\ndef b = volume > volume[1];\nplot scan = a and (b and (close > 5 or close < 1));")
check("H ⭐ 嵌套括号 a and (b and (c or d)) → 3 条可开关的条件(原来整体退成自定义组合)", _d["combine"] == "all" and len(_d["plot_order"]) == 3, (_d["combine"], _d["plot_order"]))
check("H 嵌套括号:or 项原文带括号、不再补括号", [x["expr"] for x in _d["conditions"] if x["kind"] == "term"] == ["(close > 5 or close < 1)"]
      and not [x for x in _d["conditions"] if x["kind"] == "term"][0]["paren"])
_c, _d = _dd("def a = close > close[1];\ndef b = volume > volume[1];\nplot scan = ((a and b)) and close > 1;")
check("H 双层括号 ((a and b)) and c → 3 条", _d["combine"] == "all" and len(_d["plot_order"]) == 3, _d["plot_order"])
_c, _d = _dd("def a = close > close[1];\nplot scan = (if a then close > 1 and close < 9 else close > 2) and volume > 1;")
check("H 括号里是 if(里面的 and 不能拆)→ 这一段整体一条", _d["combine"] == "all" and len(_d["plot_order"]) == 2, _d["plot_order"])
_c, _d = _dd("def a = close > close[1];\ndef b = volume > volume[1];\nplot scan = not (a and b) and close > 1;")
check("H not (a and b) 不拆", _d["combine"] == "all" and len(_d["plot_order"]) == 2)
_c, _d = _dd("def a = close > close[1];\ndef b = volume > volume[1];\ndef H2 = a and (b and close > 3);\nplot scan = H2;")
check("H 宿主里的嵌套括号也拆", _d["term_host"] == "H2" and len(_d["plot_order"]) == 3, _d["plot_order"])
_c, _d = _dd("def a = close > close[1];\ndef b = volume > volume[1];\nplot scan = a && (b && close > 3);")
check("H && 写法的嵌套括号也拆", _d["combine"] == "all" and len(_d["plot_order"]) == 3)

_c, _d = _dd("def a = close > close[1];\ndef b = volume > volume[1];\nplot p1 = b;\nplot scan = a;")
check("H ⭐ 两个 plot:前一个原样进 extra_plots", _d["extra_plots"] == [{"name": "p1", "expr": "b"}], _d.get("extra_plots"))
check("H ⭐ 两个 plot:前一个用到的 b 是中间定义,不是「停用条件」", not [x for x in _d["conditions"] if x["name"] == "b"][0]["is_bool"] and _d["plot_refs"] == ["a"])
_c, _d = _dd("def a = close > close[1];\nplot scan = a;")
check("H 只有一个 plot 时 extra_plots 为空", _d["extra_plots"] == [])

check("H 重名定义被拒并点名", "定义了两次" in err_of("def a = close > 1;\ndef a = close > 2;\nplot scan = a;"))
check("H input 与 def 重名也被拒", "定义了两次" in err_of("input a = 3;\ndef a = close > a;\nplot scan = a;"))
_c, _d = _dd("def a = close > close[1];\ndef b = volume > volume[1];\ndef H3 = a and b and close > 2;\ndef z = close > 1;\nplot scan = H3;")
check("H 宿主不是最后一句:照样展开,后面的 z 是停用条件", _d["term_host"] == "H3" and len(_d["plot_order"]) == 3
      and [x for x in _d["conditions"] if x["name"] == "z"][0]["is_bool"] and "z" not in _d["plot_refs"])

_src = "def v = volume > Average(volume, 20) # 量能😀 放大\n  * 7;\nplot scan = v;"
_c, _d = _dd(_src)
_v = [x for x in _d["conditions"] if x["name"] == "v"][0]
_num7 = [t for t in _v["tokens"] if t["k"] == "num" and t["t"] == "7"]
check("H ⭐ 注释里有 emoji:数字 token 的 s/e 是**码点**偏移(前端要换算成 UTF-16 再切)", _num7 and _v["expr"][_num7[0]["s"]:_num7[0]["e"]] == "7", _num7)
_c, _d = _dd("def a = close > close[1];\ndef b = volume > volume[1];\ndef H4 = a and  # 第一条\n  b and   # 第二条\n  close > 3;\nplot scan = H4;")
check("H 宿主里每项后面跟注释:条件原文不含注释", [x["expr"] for x in _d["conditions"] if x["kind"] == "term"] == ["close > 3"]
      and _d["plot_refs"] == ["a", "b"], _d["plot_order"])

print("\n── I 名字不分大小写(2026-09-19 编辑框 sma10 > sma20 报不认识)──")
# ThinkScript 不分大小写。按类别:扫描源字段(全小写 / 全大写 / 混写 / 带点 / 带下划线)、自己的 def 名、
# 与字段同名的 def、函数与关键字(本来就认,不能被改坏)、拿不准的不猜、位置不变。


def fixed(src, extra=""):
    return sd.fix_case(src, FIELDS, extra)


def compiles(src):
    try:
        compile(fixed(src)[0])
        return True
    except sd.ScreenError:
        return False


check("I ⭐ 用户原样:sma10 > sma20 → SMA10 > SMA20", fixed("plot scan = sma10 > sma20;")[0] == "plot scan = SMA10 > SMA20;")
check("I 改前确实不认识(不是本来就认)", "不认识" in err_of("plot scan = sma10 > sma20;"))
check("I 改后编译得过", compiles("plot scan = sma10 > sma20;"))
check("I 混写 Sma20 / eMa50", fixed("plot scan = Sma20 > eMa50;")[0] == "plot scan = SMA20 > EMA50;")
check("I 大写下划线字段 MARKET_CAP_BASIC", fixed("plot scan = MARKET_CAP_BASIC > 1e9;")[0] == "plot scan = market_cap_basic > 1e9;")
check("I 带点字段 perf.y", fixed("plot scan = perf.y > 0;")[0] == "plot scan = Perf.Y > 0;")
check("I rsi → RSI、Rs_Rating → rs_rating", fixed("plot scan = rsi > 50 and Rs_Rating >= 80;")[0]
      == "plot scan = RSI > 50 and rs_rating >= 80;")
check("I 改写清单逐项列出", fixed("plot scan = sma10 > sma20;")[1] == ["sma10 → SMA10", "sma20 → SMA20"])
check("I 已经是原名的不列、不改", fixed("plot scan = SMA10 > SMA20;") == ("plot scan = SMA10 > SMA20;", []))
check("I ⭐ def 名换大小写引用:def Up … plot scan = up", fixed("def Up = close > 20; plot scan = up;")[0]
      == "def Up = close > 20; plot scan = Up;")
check("I def 名换大小写后编译得过", compiles("def Up = close > 20; plot scan = UP;"))
# 2026-09-22 改口径:原来这条断言「引用归 def」,线上当天就出事(见下一条)。逐字是字段名的一律按字段
check("I ⭐ 逐字写对的字段名不被同名(不同大小写)的 def 抢走",
      fixed("def sma20 = Average(close, 20); plot scan = close > SMA20;")[0]
      == "def sma20 = Average(close, 20); plot scan = close > SMA20;")
check("I ⭐ 09-22 事故:上下文有 def sma50,追加的脚本里 SMA50 不动",
      fixed("def c_trend = close > SMA50 and SMA50 > SMA150; plot scan = c_trend;",
            "def sma50 = Average(close, 50); def cond_price_sma50 = close > sma50; plot scan = cond_price_sma50;")[0]
      == "def c_trend = close > SMA50 and SMA50 > SMA150; plot scan = c_trend;")
check("I 事故那份追加后编译得过", compiles("def sma50 = Average(close, 50); def c1 = close > sma50;"
                                     " def c_trend = close > SMA50 and SMA50 > SMA150; plot scan = c1 and c_trend;"))
check("I 不是字段的名字仍按 def 改(def Foo50 … foo50)",
      fixed("def Foo50 = close > 1; plot scan = foo50;")[0] == "def Foo50 = close > 1; plot scan = Foo50;")
check("I 追加模式:片段引用上下文里的 def,按上下文的写法改",
      fixed("plot scan = MYCOND;", "def myCond = close > 20;")[0] == "plot scan = myCond;")
check("I input 名换大小写", fixed("input N = 20; plot scan = close > n;")[0] == "input N = 20; plot scan = close > N;")
check("I 函数调用不动(本来就不分)", fixed("plot scan = close > average(close, 50);")[0] == "plot scan = close > average(close, 50);")
check("I 价格名不动(本来就不分)", fixed("plot scan = CLOSE > Close[1];")[0] == "plot scan = CLOSE > Close[1];")
check("I 关键字不动", fixed("plot scan = close > 20 AND volume > 1;")[0] == "plot scan = close > 20 AND volume > 1;")
check("I ⭐ 不认识的名字不乱改,照旧报不认识", "不认识" in err_of(fixed("plot scan = sma7x > 1;")[0]))
AMB = FIELDS | {"Foo", "FOO"}
check("I ⭐ 不分大小写对上两个字段时不猜", sd.fix_case("plot scan = foo > 1;", AMB)[0] == "plot scan = foo > 1;")
check("I 注释里的字不动", fixed("plot scan = sma10 > 1;  # sma10 是十日线")[0] == "plot scan = SMA10 > 1;  # sma10 是十日线")
src = "def c1 = sma10 > sma20;   # 注释\nplot scan = c1 and rsi > 50;"
check("I ⭐ 只改大小写,长度不变(decompose / 编辑框用的位置照旧)", len(fixed(src)[0]) == len(src))
check("I 大白话原样返回(词法不过)", fixed("股价站上50日均线") == ("股价站上50日均线", []))
check("I 时间序列脚本里的字段也改", fixed("plot scan = sma20 > sma20[1];")[0] == "plot scan = SMA20 > SMA20[1];")

print(f"\n{'ALL OK' if not FAILS else 'SOME FAILED'} · 通过 {N_OK} · 失败 {len(FAILS)}")
if FAILS:
    print("失败清单:")
    for f in FAILS:
        print("  -", f)
sys.exit(1 if FAILS else 0)
