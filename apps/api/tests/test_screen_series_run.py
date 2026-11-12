# -*- coding: utf-8 -*-
"""筛选器 · 时间序列脚本的全市场运行(screen_source._run_series)回归用例 —— 不联网、不连库。

    cd /app && PYTHONPATH=/app python tests/test_screen_series_run.py     # 必须 ALL OK

引擎本身(screen_series)有 test_screen_series.py 盯着;这里盯的是 _run_series 这一层「取数 / 拼行 / 回溯日期」:
  a ⭐ 回溯(as_of)时快照字段(市值 / PE)整批 NaN → 用到它们的条件是「算不出」,不是「不满足」,也不能拿今天的值顶
  b 回溯日早于日线起点 → ScreenError,报错里写明两个日期
  c 回溯日不是交易日 → 取之前最近的交易日求值,warnings 与 as_of 字段都写实际日期
  d 不回溯时快照字段按今天的值当常量,整段历史都生效(递归计数不会只在最后一根有值);
    warnings 点名这些字段按今天的值当常量(36d3869),「怎么算 / 求值截至」两条说明不回来(5540bce 用户要求去掉)
  e 今天快照里的价格不进求值(收盘价列来自日线)

store / fetch_rows / today_sh 全部换成假的。
"""
from __future__ import annotations

import sys
import time
from datetime import date, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np  # noqa: E402

from app.services.quant import screen_source as ss  # noqa: E402
from app.services.quant import screen_asof, screen_dsl, screen_quota  # noqa: E402
from app.services.quant.screen_dsl import ScreenError  # noqa: E402

FAILS: list[str] = []
N_OK = 0


def check(name, ok, detail=""):
    global N_OK
    ok = bool(ok)
    print(("OK   " if ok else "FAIL ") + name + (("  · " + str(detail)[:300]) if (detail and not ok) else ""))
    if ok:
        N_OK += 1
    else:
        FAILS.append(name)


NL = chr(10)
SMA = [5, 10, 20, 50, 100, 150, 200]
FIELDS = {"close", "open", "high", "low", "volume", "name", "description", "currency", "exchange", "sector",
          "market_cap_basic", "price_earnings_ttm", "RSI", "change", "average_volume_30d_calc",
          "Perf.W", "Perf.1M", "Perf.3M", "Perf.6M", "Perf.YTD", "Perf.Y"}
FIELDS |= {f"SMA{n}" for n in SMA} | {f"EMA{n}" for n in SMA}
has = lambda n: n in FIELDS  # noqa: E731

# ─── 合成日线:60 个工作日,2026-06-01(周一)起 ─────────────────────
DATES: list[date] = []
d = date(2026, 6, 1)
while len(DATES) < 60:
    if d.weekday() < 5:
        DATES.append(d)
    d += timedelta(days=1)
N = len(DATES)


def bars(close):
    c = np.array(close, dtype=float)
    return np.column_stack([c, c * 1.01, c * 0.99, np.full(N, 1e6), c * 0.995])


UP = [10.0 + i for i in range(N)]
DOWN = [100.0 - i for i in range(N)]
# ⚠ 股票只数必须 ≠ 脚本窗口根数(SCRIPT 的 window = 4)。只数恰好等于窗口时,(N,) 快照列沿列方向广播的 bug
# 不报错、只静默错位;线上几千只 × 几根是 ValueError。第 5 只 E 就是为这个加的,下面还专门测了 N == W
STORE = {
    "codes": {"A": (list(DATES), bars(UP)), "B": (list(DATES), bars(UP)),
              "C": (list(DATES), bars(DOWN)), "D": (list(DATES), bars(UP)),
              "E": (list(DATES), bars(DOWN))},
    "bench": {x: 4000.0 + i for i, x in enumerate(DATES)},
    "last": DATES[-1], "loaded_at": 1.0,
}
# 快照:close=999 故意与日线不同,验证「今天的价格不进求值」
SNAP = [{"_code": "A", "_symbol": "NASDAQ:A", "name": "A", "close": 999.0, "market_cap_basic": 2e9},
        {"_code": "B", "_symbol": "NASDAQ:B", "name": "B", "close": 999.0, "market_cap_basic": 5e8},
        {"_code": "C", "_symbol": "NASDAQ:C", "name": "C", "close": 999.0, "market_cap_basic": 2e9},
        {"_code": "D", "_symbol": "NASDAQ:D", "name": "D", "close": 999.0},            # 快照里没有市值
        {"_code": "E", "_symbol": "NASDAQ:E", "name": "E", "close": 999.0, "market_cap_basic": 3e9}]
CALLS = {"fetch_cols": [], "store_perf": []}


def f_fetch_rows(market_key, columns, *a, **k):
    CALLS["fetch_cols"].append(list(columns))
    return [dict(r) for r in SNAP], len(SNAP)


def f_get_store(market_key, perf):
    CALLS["store_perf"].append(sorted(perf))
    return STORE


ss.fetch_rows = f_fetch_rows
screen_asof.get_store = f_get_store
screen_quota.today_sh = lambda: date(2026, 9, 15)
ss.get_meta = lambda m: ss._Meta(set(FIELDS), SMA, SMA, [14])

SCRIPT = NL.join(["def up = close > close[1];",
                  "def big = market_cap_basic > 1000000000;",
                  "plot scan = up and big;"])


def run(script=SCRIPT, as_of=None, sort_by=None, keep_all=False, limit=100):
    c = screen_dsl.compile_script(script, has, SMA, SMA, [14])
    assert c.series is not None, "用例脚本必须走时间序列模式"
    return ss._run_series(c, ss._market("us"), "us", has, limit, sort_by, True, as_of, keep_all, t_start=time.time())


def codes(out):
    return sorted(p["code"] for p in out["picks"])


def miss(out, f):
    return next((m["count"] for m in out["missing_fields"] if m["field"] == f), 0)


# ═══ d / e · 不回溯:快照字段当常量 ═══════════════════════════════
out = run()
check("d 前提:走的是时间序列(返回体有 series)", out.get("series") is not None)
check("d ⭐ 不回溯 · 市值按今天快照参与:只命中 A(B 市值小、C 在跌)", codes(out) == ["A"], codes(out))
check("d 不回溯 · 快照缺市值的 D 计入算不出(不是不满足)", out["skipped_incomplete"] == 1 and miss(out, "market_cap_basic") == 1,
      (out["skipped_incomplete"], out["missing_fields"]))
check("d 不回溯 · as_of = 日线最新一天,as_of_requested 为空", out["as_of"] == str(DATES[-1]) and out["as_of_requested"] is None)
check("d 不回溯 · asof_unavailable 为空", out["asof_unavailable"] == [])
check("d 快照请求里带上了脚本直接写的快照字段", any("market_cap_basic" in cols for cols in CALLS["fetch_cols"]), CALLS["fetch_cols"])
a_row = out["picks"][0]
check("e ⭐ 收盘价列来自日线最后一根(不是快照的 999)", a_row["close"] == UP[-1], a_row["close"])
# 5540bce(2026-09-16 用户要求)去掉了「怎么算」(以「时间序列模式:」开头)与「求值截至 …」两条说明,把同一句里的快照字段口径声明一起删了;
# 36d3869 把口径声明单独补回(只在不回溯且脚本用了快照字段时出,点名字段),两条说明不恢复。两边都盯着
check("d warnings 写明快照字段按今天的值当常量,并点名字段(36d3869)",
      any("当常量" in w and "市值" in w for w in out["warnings"]), out["warnings"])
check("d 不回溯 · 被要求去掉的「怎么算 / 求值截至」两条说明没有回来(5540bce)",
      not any(w.startswith("时间序列模式") or "求值截至" in w for w in out["warnings"]), out["warnings"])
check("d 不回溯 · 要用户处理的提示照留:D 缺市值的「没能判断」、市值口径说明",
      any("没能判断" in w and "市值" in w for w in out["warnings"]) and ss.MARKET_CAP_WARN in out["warnings"], out["warnings"])

# 用 close > open(第 0 根就算得出)。写 close > close[1] 的话,第 0 根没有前一根 → 算不出,
# 一路上涨从不走 else 重置,连续计数整段都是「算不出」—— 那是引擎三值逻辑的口径(screen_series),不是这里要测的
REC = NL.join(["def big = market_cap_basic > 1000000000;",
               "def streak = if big and close > open then streak[1] + 1 else 0;",
               "plot scan = streak >= 5;"])
out = run(REC)
a = next((p for p in out["picks"] if p["code"] == "A"), None)
check("d ⭐ 快照常量在整段历史都生效:递归连续计数 = 60 根(不是只在最后一根有值)且命中 A",
      a is not None and a["fields"].get("streak") == 60, a and a["fields"])
check("d 递归 · B(市值小)不命中、D(没有市值)算不出", "B" not in codes(out) and "D" not in codes(out)
      and out["skipped_incomplete"] == 1, (codes(out), out["skipped_incomplete"]))
check("d 数值型定义列出现在 columns 里", "streak" in out["columns"], out["columns"])

# ═══ a · 回溯:快照字段整批 NaN ════════════════════════════════════
mid = DATES[40]
out = run(as_of=mid)
check("a ⭐ 回溯 · 用到市值的条件全部算不出:0 命中(不能拿今天的市值顶)", out["matched"] == 0, codes(out))
check("a ⭐ 回溯 · A/B/D 上涨但市值算不出 → 3 只 skipped;C 下跌 = 不满足(false and 未知 = false)",
      out["skipped_incomplete"] == 3, out["skipped_incomplete"])
check("a 回溯 · missing_fields 里市值计 3 只,原因是回溯没有历史值",
      miss(out, "market_cap_basic") == 3 and any("回溯" in (m["reason"] or "") for m in out["missing_fields"]), out["missing_fields"])
check("a 回溯 · asof_unavailable 点名市值", out["asof_unavailable"] == ["market_cap_basic"], out["asof_unavailable"])
check("a 回溯 · warnings 点名没有历史值的字段", any("没有历史值" in w and "市值" in w or "market_cap_basic" in w for w in out["warnings"]),
      out["warnings"])
check("a 回溯 · as_of = 那天,收盘价是那天的日线", out["as_of"] == str(mid))
check("a 回溯 · 不带今天的展示列(PE 等)", "price_earnings_ttm" not in out["columns"], out["columns"])
out2 = run("def up = close > close[1];" + NL + "plot scan = up;", as_of=mid)
check("a 回溯 · 不用快照字段的脚本照常命中 A/B/D", codes(out2) == ["A", "B", "D"], codes(out2))
row_a = next(p for p in out2["picks"] if p["code"] == "A")
check("a ⭐ 回溯 · 行里的收盘价是回溯日那根(不是最新一根、不是快照)", row_a["close"] == UP[40], row_a["close"])
out3 = run(REC, as_of=mid)
check("a 回溯 · 递归脚本里用到市值也整批算不出、0 命中", out3["matched"] == 0 and out3["skipped_incomplete"] >= 3,
      (out3["matched"], out3["skipped_incomplete"]))

# ═══ b · 回溯日早于日线起点 ═══════════════════════════════════════
early = DATES[0] - timedelta(days=3)
try:
    run(as_of=early)
    check("b ⭐ 早于日线起点 → ScreenError", False)
except ScreenError as e:
    check("b ⭐ 早于日线起点 → ScreenError,写明所选日期与起点", str(early) in str(e) and str(DATES[0]) in str(e), str(e))
out = run("def up = close > close[1];" + NL + "plot scan = up;", as_of=DATES[0])
check("b 恰好是起点那天 → 不报错(只有一根,偏移算不出)", out["as_of"] == str(DATES[0]) and out["matched"] == 0
      and out["skipped_incomplete"] == 5, (out["matched"], out["skipped_incomplete"]))

# ═══ 形状 · 快照列与 K 线窗口的广播(真实 bug 回归)═══════════════
# 同一份脚本、同一只 A,在「只数 < / = / > 窗口根数」三种池子里结论必须一样:命中
full_codes = STORE["codes"]
for keep in (["A", "C", "E"], ["A", "B", "C", "E"], ["A", "B", "C", "D", "E"]):
    STORE["codes"] = {k: full_codes[k] for k in keep}
    try:
        o = run()
        check(f"⭐ 快照列广播 · {len(keep)} 只票 × 窗口 4 根:A 命中、不报错", "A" in codes(o), codes(o))
    except Exception as e:  # noqa: BLE001
        check(f"⭐ 快照列广播 · {len(keep)} 只票 × 窗口 4 根:A 命中、不报错", False, f"{type(e).__name__}: {e}")
STORE["codes"] = full_codes

# ═══ c · 非交易日 ═════════════════════════════════════════════════
fri = next(x for x in DATES[20:] if x.weekday() == 4)
sat = fri + timedelta(days=1)
out = run("def up = close > close[1];" + NL + "plot scan = up;", as_of=sat)
check("c ⭐ 周六 → 取周五求值(as_of = 周五)", out["as_of"] == str(fri), out["as_of"])
check("c as_of_requested 保留用户选的周六", out["as_of_requested"] == str(sat), out["as_of_requested"])
check("c ⭐ warnings 写明实际日期,并说明所选日不是交易日",
      any(str(fri) in w for w in out["warnings"]) and any("不是交易日" in w for w in out["warnings"]), out["warnings"])
row_a = next(p for p in out["picks"] if p["code"] == "A")
check("c 求值用的是周五那根收盘", row_a["close"] == UP[DATES.index(fri)], row_a["close"])
out = run("def up = close > close[1];" + NL + "plot scan = up;", as_of=DATES[-1] + timedelta(days=30))
check("c 回溯日晚于日线最新一天 → 取最新一天", out["as_of"] == str(DATES[-1]), out["as_of"])
out = run("def up = close > close[1];" + NL + "plot scan = up;", as_of=DATES[10])
check("c 交易日当天 → 不写「不是交易日」", not any("不是交易日" in w for w in out["warnings"]), out["warnings"])

# ═══ 其它:空日线 / 排序 / keep_all / run_script 分流 ═════════════
saved_bench = STORE["bench"]
STORE["bench"] = {}
try:
    run()
    check("日线没建好 → ScreenError", False)
except ScreenError as e:
    check("日线没建好 → ScreenError 说明原因", "还没建好" in str(e), str(e))
STORE["bench"] = saved_bench
out = run("def up = close > close[1];" + NL + "plot scan = up;", as_of=mid, sort_by="market_cap_basic")
check("回溯 · 按结果里没有的列排序 → 改按收盘价并说明", any("改按收盘价" in w for w in out["warnings"]), out["warnings"])
out = run("def up = close > close[1];" + NL + "plot scan = up;", keep_all=True, limit=1)
check("keep_all · 带 _all_picks(全部命中),picks 按 limit 截", len(out.get("_all_picks") or []) == 3 and len(out["picks"]) == 1,
      (len(out.get("_all_picks") or []), len(out["picks"])))
out = run("def up = close > close[1];" + NL + "plot scan = up;")
check("不带 keep_all · 没有 _all_picks", "_all_picks" not in out)
out = ss.run_script(SCRIPT, "us", 100, None, True, mid)
check("run_script 遇到时间序列脚本分流到 _run_series(回溯同样 0 命中、3 只算不出)",
      out.get("series") is not None and out["matched"] == 0 and out["skipped_incomplete"] == 3,
      (out.get("series"), out["matched"], out["skipped_incomplete"]))

print(f"\n{N_OK} passed, {len(FAILS)} failed")
for x in FAILS:
    print("FAIL", x)
print("ALL OK" if not FAILS else "SOME FAILED")
sys.exit(1 if FAILS else 0)
