"""因子参数 · 自检脚本(不依赖数据库 · 不依赖 pytest)

用法:  cd apps/api && python scripts/selfcheck_factor_params.py

这个脚本回答的是一个问题:**用户在工作台调了参数,选股真的会变吗?**

光验证"接口收下了参数"没有意义 —— 打分链路最后要过 `_winsorize_zscore`,
而 z-score 会把任何线性变换重新归一成同一个分布,截面排名一个位置都不变。
所以每个参数都要断言**排序变了**,而不只是数值变了。
(RSI 超买卖线在 2026-09-09 之前就是这样一个假参数,见下面第 4 组。)

同时验证:默认值仍然复现老口径(库里的历史值才对得上)、
select 型参数的非法值回落、注册表自检 `_check_params()` 干净。

**独立运行**,不要塞进 pytest 会话 —— 它改了 sys.modules,会影响别的用例。
"""
import sys, types, importlib, os
from datetime import date, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Windows 控制台是 GBK,别让 print 崩

API_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, API_ROOT)

# stub 掉数据库模块(本机没有 psycopg2 也能跑)
db_stub = types.ModuleType("app.services.database")
db_stub.get_conn = lambda: (_ for _ in ()).throw(RuntimeError("no db in unit test"))
sys.modules["app.services.database"] = db_stub
fs_stub = types.ModuleType("app.services.quant.financial_store")
fs_stub.read_metric = lambda codes, key, d, lookback_days=400: {}
sys.modules["app.services.quant.financial_store"] = fs_stub

fe = importlib.import_module("app.services.quant.factor_engine")

T = date(2026, 9, 8)
ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS" if cond else "FAIL"), name, detail)
    ok = ok and cond


def ranking(vals: dict) -> list:
    """按值降序的代码序列 · 同分按代码名排(稳定)· 用来比"排序有没有变" """
    return [c for c, _ in sorted(vals.items(), key=lambda kv: (-kv[1], kv[0]))]


def rank_groups(vals: dict) -> dict:
    """每只票落在第几档 —— **同分归同档**。

    比"排序变没变"不能直接比排好的列表:同分时列表要靠别的规则打破平局,
    看起来顺序没动,而实际上"并列"这件事本身就是排名结构的变化
    (阈值型参数干的正是这个:把远离高点的一批票压成同一档)。
    """
    z, _rank = fe._winsorize_zscore(vals)
    tiers = sorted({round(v, 9) for v in z.values()}, reverse=True)
    return {c: tiers.index(round(v, 9)) for c, v in z.items()}


def days(n):
    return [T - timedelta(days=(n - 1 - i)) for i in range(n)]


# ═══════════════════════════════════════════════════════════════
# 0 · 注册表自检
# ═══════════════════════════════════════════════════════════════
check("_check_params() 无问题", fe._check_params() == [], fe._check_params())
check("每个参数化因子都在 LOCAL_ONLY",
      all(k in fe.LOCAL_ONLY for k in fe.FACTOR_PARAMS),
      [k for k in fe.FACTOR_PARAMS if k not in fe.LOCAL_ONLY])

# ═══════════════════════════════════════════════════════════════
# 1 · params_of · 夹取 / 回落 / 野字段
# ═══════════════════════════════════════════════════════════════
p = fe.params_of("high52_prox", {"window": 9999, "near_pct": 7.5, "outside": "drop"})
check("越界夹到 max", p["window"] == 500, p)
check("float 参数按 float 收", abs(p["near_pct"] - 7.5) < 1e-12, p)
check("select 合法值生效", p["outside"] == "drop", p)
p = fe.params_of("high52_prox", {"near_pct": -3, "outside": "rm -rf", "野字段": 1})
check("越界夹到 min", abs(p["near_pct"] - 1.0) < 1e-12, p)
check("select 非法值回落默认(不猜)", p["outside"] == "floor", p)
check("没登记的键被忽略", "野字段" not in p, p)
p = fe.params_of("rsi", {"period": "14.9"})
check("字符串数字能收 · int 参数取整", p["period"] == 14, p)

# ═══════════════════════════════════════════════════════════════
# 2 · 52 周高点距离 · 「距离百分比」阈值(用户点名的那个)
# ═══════════════════════════════════════════════════════════════
#
# 造 6 只票:收盘价都是 100,但窗口内最高价不同 → 距高点 0% / 2% / 5% / 10% / 20% / 40%。
# 最高点放在第 100 根(落在 250 日窗口里)。
GAPS = {"G00": 0.00, "G02": 0.02, "G05": 0.05, "G10": 0.10, "G20": 0.20, "G40": 0.40}
_ts = days(300)


def fake_ohlcv_gap(codes, trade_date, back_days):
    out = {}
    for c in codes:
        if c not in GAPS:
            continue
        g = GAPS[c]
        peak = 100.0 / (1.0 - g) if g else 100.0
        out[c] = [(_ts[i], peak if i == 100 else 100.0, 100.0, 1000.0) for i in range(300)]
    return out


fe._fetch_klines_ohlcv = fake_ohlcv_gap
CODES = list(GAPS)

base = fe._compute_high52_prox(CODES, T)
check("默认口径 = 收盘/最高(与 2026-09-09 之前逐位一致)",
      all(abs(base[c] - (1.0 - GAPS[c])) < 1e-12 for c in CODES), base)

near5 = fe._compute_high52_prox(CODES, T, {"near_pct": 5.0})
check("阈值 5% · 5% 以内按距离给分",
      abs(near5["G00"] - 1.0) < 1e-12 and abs(near5["G02"] - 0.6) < 1e-12
      and abs(near5["G05"] - 0.0) < 1e-12, near5)
check("阈值 5% · 超出的并到 0 分(floor)",
      near5["G10"] == 0.0 and near5["G20"] == 0.0 and near5["G40"] == 0.0, near5)
check("阈值改了 · 过完 z-score 排名结构真的变了(不是假参数)",
      rank_groups(base) != rank_groups(near5),
      f"默认 {rank_groups(base)} → 5% {rank_groups(near5)}")

drop5 = fe._compute_high52_prox(CODES, T, {"near_pct": 5.0, "outside": "drop"})
check("阈值 5% + 不打分 · 超出的整只票不产出(当筛选条件用)",
      set(drop5) == {"G00", "G02", "G05"}, sorted(drop5))

# 窗口参数:把窗口收到 120 日,第 100 根的高点就出了窗口 → 所有票距高点都是 0
win120 = fe._compute_high52_prox(CODES, T, {"window": 120})
check("回看窗口变短 · 高点出了窗口,值随之变",
      all(abs(v - 1.0) < 1e-12 for v in win120.values()), win120)

# ═══════════════════════════════════════════════════════════════
# 3 · compute_z_live · 默认走查表 / 改了才现算
# ═══════════════════════════════════════════════════════════════
check("参数为空 → 不现算(让调用方查表)",
      fe.compute_z_live("high52_prox", CODES, T, None) == {})
check("参数调回默认 → 不现算",
      fe.compute_z_live("high52_prox", CODES, T, {"near_pct": 100.0}) == {})
live = fe.compute_z_live("high52_prox", CODES, T, {"near_pct": 5.0})
check("参数改了 → 现算并返回 z_score", len(live) == len(CODES), sorted(live))
check("现算结果不落库(_LIVE_CACHE 命中同一份)",
      fe.compute_z_live("high52_prox", CODES, T, {"near_pct": 5.0}) is live)

# ═══════════════════════════════════════════════════════════════
# 4 · RSI 超买卖线 · 修掉「线性变换被 z-score 吃掉」的假参数
# ═══════════════════════════════════════════════════════════════
#
# 造 7 只票,RSI 分别 ≈ 15/25/35/50/65/75/85。
# 7 个涨日 +u、7 个跌日 -1 → RSI = 100 - 100/(1+u),取 u = t/(100-t)。
RSIS = {f"R{t}": t for t in (15, 25, 35, 50, 65, 75, 85)}


def fake_close_rsi(codes, trade_date, back_days):
    out = {}
    for c in codes:
        if c not in RSIS:
            continue
        t = RSIS[c]
        u = t / (100.0 - t)
        diffs = []
        for _ in range(7):
            diffs += [u, -1.0]
        closes = [100.0] * 6
        for d in diffs:
            closes.append(closes[-1] + d)
        out[c] = [(_ts[i], closes[i]) for i in range(len(closes))]
    return out


fe._fetch_klines_close = fake_close_rsi
RCODES = list(RSIS)

wide = fe._compute_rsi(RCODES, T)                                    # 默认 30/70
narrow = fe._compute_rsi(RCODES, T, {"oversold": 45, "overbought": 55})
check("超卖线以下封顶并列 +1", abs(wide["R15"] - 1.0) < 1e-9 and abs(wide["R25"] - 1.0) < 1e-9, wide)
check("超买线以上封顶并列 -1", abs(wide["R85"] + 1.0) < 1e-9 and abs(wide["R75"] + 1.0) < 1e-9, wide)
check("收窄超买卖区间 · 过完 z-score 排名结构真的变了",
      rank_groups(wide) != rank_groups(narrow),
      f"30/70 {rank_groups(wide)} → 45/55 {rank_groups(narrow)}")

# 反证:不截断的老实现下,两组参数只差一个正比例系数 → 排序完全相同(这就是原来的假参数)
mid_w, half_w = 50.0, 20.0
mid_n, half_n = 50.0, 5.0
old_wide = {c: (mid_w - t) / half_w for c, t in RSIS.items()}
old_narrow = {c: (mid_n - t) / half_n for c, t in RSIS.items()}
check("反证:不截断时两组参数排名一模一样(修之前的行为)",
      rank_groups(old_wide) == rank_groups(old_narrow), rank_groups(old_wide))

# 周期参数仍然生效(改的是取哪几天,不是刻度)。
# 上面那组 7 涨 7 跌的合成序列**测不出周期** —— 它的涨跌幅比例在任何长度的
# 窗口里都一样,period 14 和 6 会算出同一个 RSI。换一条涨跌幅逐步放大的序列。
def fake_close_ramp(codes, trade_date, back_days):
    closes = [100.0]
    for i in range(20):
        closes.append(closes[-1] + (1.0 + i * 0.3) * (1 if i % 2 == 0 else -0.5))
    return {"P": [(_ts[i], closes[i]) for i in range(len(closes))]} if "P" in codes else {}

fe._fetch_klines_close = fake_close_ramp
p14 = fe._compute_rsi(["P"], T)["P"]
p6 = fe._compute_rsi(["P"], T, {"period": 6})["P"]
check("RSI 周期改了 · 数值变", abs(p14 - p6) > 1e-9, (round(p14, 4), round(p6, 4)))

# ═══════════════════════════════════════════════════════════════
# 5 · 动量 · 回看窗口 / 剔除近期
# ═══════════════════════════════════════════════════════════════
#
# 造一只票:前 250 根匀速涨,最后 21 根急跌 —— 「剔除近期」剔掉那段的话
# 动量应该是正的,不剔就是负的。这也验证了原来 min(-22, -(len//3)) 的 bug:
# 那个式子下 skip 参数几乎永远不生效。
mom = [100.0]
for i in range(1, 279):
    mom.append(mom[-1] * 1.002)
for i in range(21):
    mom.append(mom[-1] * 0.97)


def fake_close_mom(codes, trade_date, back_days):
    return {"M": [(_ts[i], mom[i]) for i in range(300)]} if "M" in codes else {}


fe._fetch_klines_close = fake_close_mom
skip21 = fe._compute_momentum_12m_1m(["M"], T)["M"]
skip0 = fe._compute_momentum_12m_1m(["M"], T, {"skip": 0})["M"]
check("默认剔除近 21 日 → 剔掉急跌段,动量为正", skip21 > 0, skip21)
check("剔除天数设 0 → 含急跌段,动量为负", skip0 < 0, skip0)
check("「剔除近期」参数真的生效", abs(skip21 - skip0) > 0.1, (skip21, skip0))

w120 = fe._compute_momentum_12m_1m(["M"], T, {"window": 120})["M"]
check("回看窗口参数真的生效", abs(w120 - skip21) > 1e-6, (w120, skip21))

m1_21 = fe._compute_momentum_1m(["M"], T)["M"]
m1_5 = fe._compute_momentum_1m(["M"], T, {"window": 5})["M"]
check("1 月动量 · 窗口参数生效", abs(m1_21 - m1_5) > 1e-6, (m1_21, m1_5))
check("1 月动量默认 21 日 = 老口径 closes[-22]",
      abs(m1_21 - (mom[-1] / mom[-22] - 1)) < 1e-12, m1_21)

m6 = fe._compute_momentum_6m(["M"], T)["M"]
check("6 月动量默认 120 日 = 老口径", abs(m6 - (mom[-1] / mom[-121] - 1)) < 1e-12, m6)

# ═══════════════════════════════════════════════════════════════
# 6 · 规模 / 换手 · 门槛型参数(改的是"谁进截面",不是刻度)
# ═══════════════════════════════════════════════════════════════
fe._fetch_klines_ohlcv = lambda codes, d, back_days: {
    c: [(_ts[i], 101.0, 100.0, 1000.0) for i in range(300)] for c in codes if c in ("BIG", "SMALL")
}
# BIG 市值 = 100 × 1e9 = 1000 亿 · SMALL = 100 × 1e7 = 10 亿(低于默认下限 20 亿)
fe._estimate_shares = lambda codes, d: {"BIG": 1e9, "SMALL": 1e7}
r = fe._compute_size_inv(["BIG", "SMALL"], T)
check("市值下限默认 20 亿 · 10 亿的那只不打分", set(r) == {"BIG"}, sorted(r))
r = fe._compute_size_inv(["BIG", "SMALL"], T, {"mcap_min_yi": 1, "mcap_max_yi": 500})
check("下限调到 1 亿 / 上限 500 亿 · 换成小的那只进、大的出", set(r) == {"SMALL"}, sorted(r))

# 换手率:1000 手 × 100 = 10 万股。SMALL 股本收到 5e6 股 → 日均换手 2%,
# BIG 1e9 股 → 0.01%。上限收到 1%(注册表 min,再小夹不下去)时 SMALL 该被丢掉。
fe._estimate_shares = lambda codes, d: {"BIG": 1e9, "SMALL": 5e6}
r = fe._compute_turnover_20(["BIG", "SMALL"], T)
check("换手率默认上限 20% · 两只都在", set(r) == {"BIG", "SMALL"}, r)
r = fe._compute_turnover_20(["BIG", "SMALL"], T, {"max_turnover_pct": 1.0})
check("上限收到 1% · 换手 2% 的那只被丢掉", set(r) == {"BIG"}, r)

# ═══════════════════════════════════════════════════════════════
# 7 · 近 N 日 K 线 · 统计窗口
# ═══════════════════════════════════════════════════════════════
# _compute_candle_5d 直接查库,这里只验参数注册与夹取(计算逻辑同 F-1 自检)
check("candle_5d 注册了统计窗口", fe.params_of("candle_5d")["window"] == 5)
check("candle_5d 窗口可调", fe.params_of("candle_5d", {"window": 30})["window"] == 30)

print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
