# -*- coding: utf-8 -*-
"""VCP 字段回归用例 —— 纯计算,不联网不连库。

    cd apps/api && PYTHONPATH=. python tests/test_vcp.py

最危险的失败还是**静默理解错**:把阶梯上涨数成「收缩三次」、把波动放大当成收紧,
用户照着筛,完全看不出来。所以一半用例是「不该算成 VCP 的一定不能算成」。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import date, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "vcp", os.path.join(os.path.dirname(_HERE), "app", "services", "quant", "vcp.py"))
vcp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vcp)

fails: list[str] = []
passed = 0


def check(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
    else:
        fails.append(f"{name}  {extra}")


def path(legs, start=50.0, vol=1000.0, noise=0.0):
    """legs = [(根数, 目标价, 这一段的日均量)] → 按日期升序的 [(日期, 收, 高, 低, 量)]。
    价格在段内线性走;最高/最低 = 收盘 ±0.5%。noise>0 时叠加一个交替的小抖动。"""
    out, p, d, k = [], start, date(2026, 1, 2), 0
    for n, target, v in legs:
        step = (target - p) / n
        for _ in range(n):
            p += step
            c = p * (1 + (noise if k % 2 else -noise))
            out.append((d, c, c * 1.005, c * 0.995, v))
            d += timedelta(days=1)
            k += 1
    return out


UP = (40, 100.0, 1000.0)                     # 先涨上来(第二阶段)
TEXTBOOK = [UP,
            (15, 75.0, 1000.0), (15, 99.0, 800.0),     # 第 1 次:约 25%
            (10, 87.0, 700.0), (10, 98.0, 700.0),      # 第 2 次:约 12%
            (6, 93.0, 400.0), (6, 97.0, 500.0)]        # 第 3 次:约 5%,量最小

s = vcp.vcp_stats(path(TEXTBOOK))
check("教科书 · 数出 3 次收缩", s and s["contractions"] == 3, str(s))
check("教科书 · 深度逐次变浅", s and s["first_depth"] > 20 and 4 < s["last_depth"] < 7, str(s))
check("教科书 · 深度串给人看", s and s["depths"].count("→") == 2, str(s and s["depths"]))
check("教科书 · 量能逐次递减 = 1", s and s["vol_declining"] == 1, str(s))
check("教科书 · 最后一次缩量(量比 < 1)", s and s["last_vol_ratio"] is not None and s["last_vol_ratio"] < 1, str(s))
check("教科书 · 距枢轴约 1.5%(还没突破)", s and 0.5 < s["pivot_dist"] < 3, str(s))
check("教科书 · 底部天数从第一次收缩的高点算起", s and 60 <= s["base_days"] <= 70, str(s))

s = vcp.vcp_stats(path(TEXTBOOK, noise=0.004))
check("带噪音 · ⭐每天 ±0.4% 的抖动不会多数出收缩", s and s["contractions"] == 3, str(s))

# 波动在**放大**:5% → 12% → 25%。最近一次最深,往前数第一步就断
s = vcp.vcp_stats(path([UP, (6, 95.0, 400.0), (6, 99.0, 500.0), (10, 87.0, 700.0),
                        (10, 98.0, 700.0), (15, 74.0, 1000.0), (15, 90.0, 800.0)]))
check("放大 · ⭐波动越来越大不能算收缩(只剩最近 1 次)", s and s["contractions"] == 1, str(s))
check("放大 · 只有 1 次时量能递减无从比较 → 空", s and s["vol_declining"] is None, str(s))

# 阶梯上涨:回撤 10% → 8% → 6% 在收紧,但每个高点都比前一个高 10%
s = vcp.vcp_stats(path([UP, (8, 90.0, 900), (8, 110.0, 900), (8, 101.2, 800), (8, 121.0, 800),
                        (8, 113.7, 700), (8, 125.0, 700)]))
check("阶梯 · ⭐高点一路抬高是上涨不是底部,不能算成收缩 3 次", s and s["contractions"] == 1, str(s))

# 下跌中的反弹(2026-09-11 TSLA 实盘形态):跌 30% → 反弹到 85 → 又回落 6%。
# 深度确实在变浅,但 85 离左边的 100 还差 15%,阻力在 100 不在 85
s = vcp.vcp_stats(path([UP, (15, 70.0, 1000.0), (15, 85.0, 800.0), (6, 80.0, 500.0)]))
check("反弹 · ⭐高点比左侧低 15% 是下跌中的反弹,不能算成收缩 2 次", s and s["contractions"] == 1, str(s))

# 高点一路降低,每一步都不到 10%(100 → 93 → 86),累计 14%:按累计量断,不按相邻两次
s = vcp.vcp_stats(path([UP, (12, 80.0, 1000.0), (12, 93.0, 900.0), (8, 83.7, 700.0),
                        (8, 86.0, 600.0), (5, 81.7, 400.0)]))
check("反弹 · ⭐每步都不到 10% 但累计跌了 14%,只认最近 2 次", s and s["contractions"] == 2, str(s))

# 量能没递减
s = vcp.vcp_stats(path([UP, (15, 75.0, 500.0), (15, 99.0, 800.0), (10, 87.0, 700.0),
                        (10, 98.0, 700.0), (6, 93.0, 900.0), (6, 97.0, 500.0)]))
check("量能 · 收缩在变浅但量在放大 → 0", s and s["contractions"] == 3 and s["vol_declining"] == 0, str(s))

# 一路新高、没有回调
s = vcp.vcp_stats(path([(80, 150.0, 1000.0)]))
check("新高 · 没有收缩 → 0 次(不是空)", s and s["contractions"] == 0 and s["depths"] == "", str(s))
check("新高 · 没有枢轴 → 距枢轴为空", s and s["pivot_dist"] is None, str(s))

# 进行中的收缩:第三次还在往下走
s = vcp.vcp_stats(path([UP, (15, 75.0, 1000.0), (15, 99.0, 800.0), (10, 87.0, 700.0),
                        (10, 98.0, 700.0), (6, 93.0, 400.0)]))
check("进行中 · 最后一次没走完也算进来", s and s["contractions"] == 3, str(s))
check("进行中 · 距枢轴 = 从高点跌下来的幅度", s and 4 < s["pivot_dist"] < 7, str(s))

# 已突破:第三次收缩之后冲过枢轴
s = vcp.vcp_stats(path(TEXTBOOK[:-1] + [(4, 102.0, 900.0)]))
check("突破 · 距枢轴为负", s and s["pivot_dist"] is not None and s["pivot_dist"] < 0, str(s))

# 收缩都发生在半年以前,之后一路涨
s = vcp.vcp_stats(path(TEXTBOOK + [(140, 200.0, 1000.0)]))
check("太久 · 半年前的收缩不算", s and s["contractions"] == 0, str(s))

# ─── 2026-09-11 第二批:用户完整规则(首次 ≤50% / 至少 3 次 / 末次 ≤10% 且低点缩量 / 底部 3~12 个月)

# 第一次大回调中途有一次反抽(100 → 80 → 反弹 88 → 62):是同一次 38% 的下跌,不是两次
s = vcp.vcp_stats(path([UP, (12, 80.0, 1000.0), (6, 88.0, 900.0), (12, 62.0, 1000.0),
                        (20, 97.0, 800.0), (12, 80.0, 700.0), (12, 96.0, 700.0),
                        (8, 89.0, 400.0), (8, 95.0, 500.0)]))
check("反抽 · ⭐中途反抽没过前高又跌破前低,并成同一次(38% 而不是 20%+30%)",
      s and s["contractions"] == 3 and 36 < s["first_depth"] < 40, str(s))

# 9 个月的长底部:第一次收缩在 160 个交易日前开始,旧的 6 个月窗口会漏掉它
s = vcp.vcp_stats(path([UP, (40, 70.0, 1000.0), (40, 98.0, 800.0), (25, 85.0, 700.0),
                        (25, 97.0, 700.0), (15, 92.0, 400.0), (15, 96.0, 500.0)]))
check("长底部 · ⭐9 个月的底部照样数出 3 次", s and s["contractions"] == 3, str(s))
check("长底部 · 底部天数超过 6 个月", s and 150 <= s["base_days"] <= 165, str(s))

# 突破后已经冲过枢轴 15%:底部走完了,现在不在底部里
s = vcp.vcp_stats(path(TEXTBOOK[:-1] + [(4, 112.0, 1500.0)]))
check("走完 · ⭐冲过枢轴 10% 以上 → 0 次(不能带着「收缩 3 次」通过筛选)",
      s and s["contractions"] == 0 and s["base_days"] is None, str(s))

# 最低点量比:教科书最后一次收缩 400 量,前 50 天日均约 820
s = vcp.vcp_stats(path(TEXTBOOK))
check("低点量 · 最低点量比约 0.49", s and s["low_vol_ratio"] is not None and 0.4 < s["low_vol_ratio"] < 0.6, str(s))
check("低点量 · 越接近低点量越小(最低点量比 < 最后一次收缩量比)",
      s and s["low_vol_ratio"] < s["last_vol_ratio"], str(s))
s = vcp.vcp_stats(path(TEXTBOOK[:-2] + [(4, 94.0, 400.0), (2, 93.0, 1500.0), (6, 97.0, 500.0)]))
check("低点量 · 低点那几天放量 → 最低点量比 > 1", s and s["low_vol_ratio"] is not None and s["low_vol_ratio"] > 1, str(s))

# 近 20 日涨跌天数与涨跌日均量比
d0 = date(2026, 8, 1)
seq_c = [100.0]
seq_v = [1000.0]
for i in range(20):                       # 12 天涨(量 2000)· 8 天跌(量 1000)
    up = i % 5 != 4 and i < 15 or i >= 18
    seq_c.append(seq_c[-1] * (1.01 if up else 0.99))
    seq_v.append(2000.0 if up else 1000.0)
pv_bars = [(d0 + timedelta(days=i), c, c * 1.005, c * 0.995, v) for i, (c, v) in enumerate(zip(seq_c, seq_v))]
p = vcp.pv_stats(pv_bars)
n_up = sum(1 for a, b in zip(seq_c, seq_c[1:]) if b > a)
check("涨跌 · 上涨天数 / 下跌天数按收盘比前一天", p["up_days"] == n_up and p["down_days"] == 20 - n_up, str(p))
check("涨跌 · 涨跌日均量比 = 2000 ÷ 1000", p["ud_vol_ratio"] == 2.0, str(p))
p = vcp.pv_stats([(d, c, None, None, None) for d, c, _h, _l, _v in pv_bars])
check("涨跌 · ⭐老数据只有收盘:天数照算,量比为空(不补 0)",
      p["up_days"] == n_up and p["ud_vol_ratio"] is None, str(p))
p = vcp.pv_stats([(d0 + timedelta(days=i), 100.0 + i, None, None, 1000.0) for i in range(21)])
check("涨跌 · 20 天没有下跌日 → 量比为空(除不了)", p["up_days"] == 20 and p["ud_vol_ratio"] is None, str(p))
check("涨跌 · 不足 21 根 → 全空", vcp.pv_stats(pv_bars[:15])["up_days"] is None)

# 覆盖数按条件里用到的字段数:只用涨跌天数时,老数据也算得出
hist2 = {"AAA": {"as_of": date(2026, 9, 10), "up_days_20d": 12, "vcp_contractions": None}}
rows2 = [{"_code": "AAA"}]
check("补字段 · 只用涨跌天数时按它数覆盖", vcp.inject(rows2, hist2, False, ["up_days_20d"]) == 1)
check("补字段 · 用了收缩次数就按收缩次数数", vcp.inject(rows2, hist2, False, ["up_days_20d", "vcp_contractions"]) == 0)

# ─── 第三批:低点抬高 / 精确交易日窗口

# 低点抬高由收缩次数保证(vcp.py 口径第 10 条):后一次低点更低的,要么并成同一次,要么序列断开
# 100→92 (8%),反弹 91(没过前高)再跌到 86.5(破前低):是同一次 14% 的下跌,不能数成 8%→5% 两次
s = vcp.vcp_stats(path([UP, (8, 92.0, 900.0), (8, 91.0, 800.0), (6, 86.5, 500.0), (6, 88.0, 500.0)]))
check("低点抬高 · ⭐低点更低、高点也更低 → 并成一次,不会数成「逐次变浅 2 次」",
      s and s["contractions"] == 1 and 13 < s["first_depth"] < 16, str(s))
# 后一次高点更高、低点更低 → 后一次更深,序列断开
s = vcp.vcp_stats(path([UP, (8, 92.0, 900.0), (8, 103.0, 800.0), (8, 90.0, 700.0), (6, 100.0, 600.0)]))
check("低点抬高 · ⭐高点更高、低点更低 → 后一次更深,只剩 1 次", s and s["contractions"] == 1, str(s))

wb = [(date(2026, 8, 1) + timedelta(days=i), 100.0 + i, 101.0 + i, 99.0 + i, 1000.0) for i in range(70)]
w = vcp.window_stats(wb)
check("窗口 · 最近 5 根的最高 / 最低", w["high_5d"] == 170.0 and w["low_5d"] == 164.0, str(w))
check("窗口 · 最近 63 根(不是 61 根)", w["low_63d"] == 106.0 and w["high_63d"] == 170.0, str(w))
check("窗口 · 根数不够 → 空", vcp.window_stats(wb[:40])["low_63d"] is None
      and vcp.window_stats(wb[:40])["low_21d"] == 118.0)
check("窗口 · ⭐老数据没有最高最低 → 空,不拿收盘顶替",
      vcp.window_stats([(d, c, None, None, None) for d, c, _h, _l, _v in wb])["high_5d"] is None)

# 算不出就是空
check("数据 · 不足 60 根 → 空", vcp.vcp_stats(path([(50, 80.0, 1000.0)])) is None)
old = [(d, c, None, None, None) for d, c, _h, _l, _v in path(TEXTBOOK)]
check("数据 · ⭐老数据只有收盘价 → 整组为空,不拿收盘价顶替最高最低", vcp.vcp_stats(old) is None)
novol = [(d, c, h, l, None) for d, c, h, l, _v in path(TEXTBOOK)]
s = vcp.vcp_stats(novol)
check("数据 · 成交量缺失 → 收缩照算,量能两项为空",
      s and s["contractions"] == 3 and s["vol_declining"] is None and s["last_vol_ratio"] is None, str(s))

# 扫描时补字段:只认全市场最新那一天,过期就全空
D1, D0 = date(2026, 9, 10), date(2026, 9, 9)
hist = {"AAA": {"as_of": D1, "vcp_contractions": 3, "vcp_depths": "25→12→5", "vcp_last_depth": 5.0},
        "BBB": {"as_of": D0, "vcp_contractions": 2}}
rows = [{"_code": "AAA"}, {"_code": "BBB"}, {"_code": "CCC"}]
n = vcp.inject(rows, hist, stale=False)
check("补字段 · 最新那天的补上", rows[0]["vcp_contractions"] == 3 and rows[0]["vcp_depths"] == "25→12→5")
check("补字段 · 停牌/没拉到(as_of 落后)的不补", rows[1]["vcp_contractions"] is None)
check("补字段 · 没有统计的是 None 不是 0", rows[2]["vcp_contractions"] is None and n == 1)
rows = [{"_code": "AAA"}]
vcp.inject(rows, hist, stale=True)
check("补字段 · ⭐日线过期 → 全空(用旧形态判断今天会给错答案)", rows[0]["vcp_contractions"] is None)

# 拆股修正同步到高/低/量(rs_history.adjust_bars)——入库那一步最容易算错的地方
_spec2 = importlib.util.spec_from_file_location(
    "rs_history", os.path.join(os.path.dirname(_HERE), "app", "services", "quant", "rs_history.py"))
rh = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(rh)
D = [date(2026, 3, i) for i in range(2, 6)]
# 腾讯没复权的 1 拆 2:拆股前收 200、后收 100;repair_splits 把拆股前的收盘乘了 0.5
raw = {D[0]: (200.0, 202.0, 198.0, 1000.0), D[1]: (200.0, 204.0, 199.0, 1100.0),
       D[2]: (100.0, 101.0, 99.0, 2000.0), D[3]: (100.0, 102.0, 99.0, None)}
fixed = [(D[0], 100.0), (D[1], 100.0), (D[2], 100.0), (D[3], 100.0)]
b = rh.adjust_bars(raw, fixed)
check("拆股 · 拆股前的最高最低跟收盘同一个系数(×0.5)", b[0][2] == 101.0 and b[0][3] == 99.0, str(b[0]))
check("拆股 · 拆股前的成交量反向调整(÷0.5)", b[0][4] == 2000.0 and b[1][4] == 2200.0, str(b[:2]))
check("拆股 · 拆股后的原样不动", b[2] == (D[2], 100.0, 101.0, 99.0, 2000.0), str(b[2]))
check("拆股 · 缺的量仍是空,不补 0", b[3][4] is None, str(b[3]))
hi, lo = max(x[2] for x in b), min(x[3] for x in b)
check("拆股 · ⭐不会凭空多出一次 50% 的「收缩」", (hi - lo) / hi < 0.05, f"{hi} {lo}")
cut = rh.adjust_bars(raw, fixed[2:])
check("拆股 · repair_splits 截掉开头时只按留下的日期出", [x[0] for x in cut] == D[2:])

check("窗口 · rs_history 读库用的列表与 vcp.WINDOW_FIELDS 一致", tuple(rh._WIN) == vcp.WINDOW_FIELDS,
      f"{rh._WIN} vs {vcp.WINDOW_FIELDS}")

total = passed + len(fails)
print(f"VCP 用例 {total} 条")
if fails:
    print(f"FAIL {len(fails)} 条:")
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("ALL OK")
