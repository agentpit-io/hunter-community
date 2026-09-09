"""F-1 纯日线 7 因子 · 自检脚本(不依赖数据库 · 不依赖 pytest)

用法:  cd apps/api && python scripts/selfcheck_f1_factors.py

数据库层用 stub 顶掉,_fetch_klines_ohlcv 与 _estimate_shares 用合成数据,
验证:量比 / 52 周高点 / 换手 / Amihud / 规模 / 贝塔(=2 与 =0 精确复现)/ 偏度 的数学,
以及 688 成交量单位、三条合理性边界、因子注册完整性。
**独立运行**,不要塞进 pytest 会话 —— 它改了 sys.modules,会影响别的用例。
"""
import sys, types, importlib, math
from datetime import date, timedelta

import os
API_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, API_ROOT)

# stub 掉数据库模块
db_stub = types.ModuleType("app.services.database")
db_stub.get_conn = lambda: (_ for _ in ()).throw(RuntimeError("no db in unit test"))
sys.modules["app.services.database"] = db_stub
fs_stub = types.ModuleType("app.services.quant.financial_store")
fs_stub.read_metric = lambda codes, key, d, lookback_days=400: {}
sys.modules["app.services.quant.financial_store"] = fs_stub

fe = importlib.import_module("app.services.quant.factor_engine")
fd = importlib.import_module("app.services.quant.factor_defs")

T = date(2026, 9, 8)
def days(n):
    return [T - timedelta(days=(n - 1 - i)) for i in range(n)]

# 合成 300 天:指数 m 日收益随机(有方差);股票 A 收益 = 2 × 指数收益(beta=2);股票 B 恒定 +0.05%(beta=0)
import random as _rnd
_rnd.seed(3)
ts = days(300)
m = [100.0]
for _ in ts[1:]:
    m.append(m[-1] * (1 + _rnd.uniform(-0.02, 0.02)))
a = [50.0]
for i in range(1, len(ts)):
    a.append(a[-1] * (1 + 2 * (m[i] / m[i - 1] - 1)))
b = [10.0]
for _ in ts[1:]:
    b.append(b[-1] * 1.0005)
vol_a = [1000.0] * 299 + [3000.0]          # 最后一天放量 3 倍
vol_b = [500.0] * 300

def fake_ohlcv(codes, trade_date, back_days):
    out = {}
    src = {"000300": (m, [0.0] * 300), "A": (a, vol_a), "B": (b, vol_b)}
    for c in codes:
        if c not in src:
            continue
        closes, vols = src[c]
        out[c] = [(ts[i], closes[i] * 1.01, closes[i], vols[i]) for i in range(300)]
    return out

fe._fetch_klines_ohlcv = fake_ohlcv
fe._estimate_shares = lambda codes, d: {"A": 1e8, "B": 2e8}   # 市值 ≈ 数十亿,落在 _MCAP_RANGE 内

codes = ["A", "B"]
ok = True
def check(name, cond, detail=""):
    global ok
    print(("PASS" if cond else "FAIL"), name, detail)
    ok = ok and cond

# 1 量比:A 最后一天 3000 / 均量 1000 = 3
r = fe._compute_vol_ratio_20(codes, T)
check("vol_ratio_20", abs(r["A"] - 3.0) < 1e-9 and abs(r["B"] - 1.0) < 1e-9, r)

# 2 52 周高点:B 单调上涨,今天就是最高价 close/high = 1/1.01;A 随机走势,值应在 (0, 1]
r = fe._compute_high52_prox(codes, T)
check("high52_prox", abs(r["B"] - 1 / 1.01) < 1e-9 and 0 < r["A"] <= 1.0, r)

# 3 换手率:A 均量 (19×1000+3000)/20=1100 手 ×100 / 1e8 股 = 0.0011
r = fe._compute_turnover_20(codes, T)
check("turnover_20", abs(r["A"] - 0.0011) < 1e-12 and abs(r["B"] - 0.00025) < 1e-12, r)

# 3b 单位归一:688 开头按「股」,其他按「手」×100
check("_shares_traded 688=股", fe._shares_traded("688981", 100.0) == 100.0 and fe._shares_traded("600519", 100.0) == 10000.0)
# 3c 合理性边界:换手 > 50% 丢;bps 离谱丢;市值越界丢
fe._estimate_shares = lambda codes, d: {"A": 1_000.0, "B": 2e8}     # A 股本极小 → 换手 110 且市值几万元 → 两处都丢
r = fe._compute_turnover_20(codes, T)
check("turnover 越界丢", "A" not in r and "B" in r, r)
r = fe._compute_size_inv(codes, T)
check("size 市值越界丢", "A" not in r and "B" in r, r)        # A 市值 = close×1000 ≈ 几万元 < 20 亿
fe._estimate_shares = lambda codes, d: {"A": 1e8, "B": 2e8}

# 4 Amihud:B 每日收益 0.0005,成交额 500×100×close;值应为正且 B 的 |ret|/amt 恒定
r = fe._compute_amihud_20(codes, T)
exp_b = sum(0.0005 / (500 * 100 * b[i]) for i in range(280, 300)) / 20 * 1e9
check("amihud_20", r["B"] > 0 and abs(r["B"] - exp_b) / exp_b < 0.02, r)

# 5 规模:−ln(close × shares)
r = fe._compute_size_inv(codes, T)
check("size_inv", abs(r["A"] + math.log(a[-1] * 1e8)) < 1e-9, r)

# 6 贝塔:A=2,B=0
r = fe._compute_beta_60(codes, T)
check("beta_60", abs(r["A"] - 2.0) < 1e-6 and abs(r["B"]) < 1e-6, r)

# 6b 贝塔:指数历史不足 → 整个因子为空
fe._fetch_klines_ohlcv = lambda codes, d, back_days: {k: v[-30:] for k, v in fake_ohlcv(codes, d, back_days).items()}
r = fe._compute_beta_60(codes, T)
check("beta_60 指数不足时返回空", r == {}, r)
fe._fetch_klines_ohlcv = fake_ohlcv

# 7 偏度:B 收益恒定 → std=0 → 不产出;A 收益恒定亦然。构造一个有偏序列
sk = [100.0]
import random
random.seed(7)
for i in range(1, 300):
    rr = 0.02 if random.random() < 0.1 else -0.002   # 少数大涨、多数小跌 → 正偏
    sk.append(sk[-1] * (1 + rr))
fe._fetch_klines_ohlcv = lambda codes, d, back_days: ({"S": [(ts[i], sk[i] * 1.01, sk[i], 100.0) for i in range(300)]} if "S" in codes else {})
r = fe._compute_ret_skew_60(["S"], T)
check("ret_skew_60 正偏", r.get("S", 0) > 0.5, r)
r2 = fe._compute_ret_skew_60(["A"], T)  # A 不在 fake 里 → 空
check("ret_skew_60 无数据返回空", r2 == {}, r2)

# 8 注册完整性:enabled 因子都有 computer 且在 LOCAL_ONLY/AKSHARE_ONLY
orphans = [f.key for f in fd.enabled_factors() if f.key not in fe.COMPUTERS]
covered = set(fe.LOCAL_ONLY) | set(fe.AKSHARE_ONLY)
uncovered = [f.key for f in fd.enabled_factors() if f.key not in covered]
check("每个启用因子都有 computer", orphans == [], orphans)
check("每个启用因子都归了定时任务", uncovered == [], uncovered)
check("CAT_ORDER 覆盖所有 cat", set(f.cat for f in fd.ALL_FACTORS) <= set(fd.CAT_ORDER))
check("参数化因子签名接受 params", all(
    "params" in fe.COMPUTERS[k].__code__.co_varnames for k in fe.FACTOR_PARAMS))
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
