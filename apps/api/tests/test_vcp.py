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

total = passed + len(fails)
print(f"VCP 用例 {total} 条")
if fails:
    print(f"FAIL {len(fails)} 条:")
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("ALL OK")
