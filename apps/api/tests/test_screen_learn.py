# -*- coding: utf-8 -*-
"""对照表(从 AI 识别里学来的说法)回归用例 —— 纯逻辑,不联网不连库。

    cd apps/api && PYTHONPATH=. python tests/test_screen_learn.py

对照表出错的方式和本地规则一样:**静默理解错**。而且更糟 —— 学错的一条会被
所有人反复用。所以这里专门测「不该命中的绝不命中」:单位不同、标识符里的数字、
AI 把条件顺序弄乱、以及对照表绝不能盖过本地规则。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_screen_kw as T                      # noqa: E402  复用字段表与加载方式

sd, kw = T._load()
has = lambda n: n in T.FIELDS                    # noqa: E731
RULE_OK = lambda c: kw.rule_match(c, has, T.SMA, T.EMA, T.RSI, T.FIELDS) is not None  # noqa: E731

fails: list[str] = []
passed = 0


def check(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
    else:
        fails.append(f"{name}  {extra}")


def table_of(entries, start_id=1):
    return {k: {"id": start_id + i, "exprs": ex} for i, (k, ex) in enumerate(entries)}


def lookup(text, table):
    h = kw.learned_lookup(text, table)
    return h[0] if h else None


def tr(text, table):
    try:
        r = kw.translate(text, has, T.SMA, T.EMA, T.RSI, names=T.FIELDS, learned=table)
        return [m["expr"] for m in r["matched"]], r["matched"]
    except sd.ScreenError:
        return None, None


# ── 前提:这几句本地规则确实认不出(否则根本不会走到 AI,也就谈不上学) ──
for t in ["RS线连涨超过50天", "市值在100亿以上", "收盘价高于50日均线的1.05倍", "市盈率大于10小于20"]:
    check(f"前提 · 规则认不出「{t}」", not RULE_OK(t))

# ① 单句:学一次,换个数字也能认 ─────────────────────────────
E = kw.learn_entries("RS线连涨超过50天", ["rs_line_up_days > 50"], RULE_OK)
check("单句 · 学出一条", len(E) == 1, str(E))
check("单句 · 数字挖成空位", E and E[0] == ("rs线连涨超过{0}天", ["rs_line_up_days > {0}"]), str(E))
TB = table_of(E)
check("单句 · 原样再问命中", lookup("RS线连涨超过50天", TB) == ["rs_line_up_days > 50"])
check("单句 · ⭐换成 60 天也命中,数字取用户这次的", lookup("RS线连涨超过60天", TB) == ["rs_line_up_days > 60"])
exprs, matched = tr("RS线连涨超过60天", TB)
check("单句 · translate 走通并标明来自对照表",
      exprs == ["rs_line_up_days > 60"] and matched and matched[0].get("learned", {}).get("id") == 1,
      str(matched))
check("单句 · 多出字就不命中(模板是整句精确匹配)", lookup("RS线连涨超过50天以上", TB) is None)

# ② 单位留在模板里 ──────────────────────────────────────────
E = kw.learn_entries("市值在100亿以上", ["market_cap_basic > 10000000000"], RULE_OK)
TB = table_of(E)
check("单位 · 模板是「市值在{0}亿以上」", E and E[0][0] == "市值在{0}亿以上", str(E))
check("单位 · 换数字按亿换算", lookup("市值在50亿以上", TB) == ["market_cap_basic > 5000000000"])
check("单位 · ⭐「万」对不上「亿」的模板,绝不套用", lookup("市值在50万以上", TB) is None)

# ③ 标识符/周期里的数字不挖空 ────────────────────────────────
E = kw.learn_entries("收盘价高于50日均线的1.05倍", ["close > SMA50 * 1.05"], RULE_OK)
TB = table_of(E)
check("周期 · 只挖倍数,均线周期原样留着",
      E and E[0] == ("收盘价高于50日均线的{0}倍", ["close > SMA50 * {0}"]), str(E))
check("周期 · 换倍数命中", lookup("收盘价高于50日均线的1.1倍", TB) == ["close > SMA50 * 1.1"])
check("周期 · ⭐换均线周期不命中(不能把 20 日线套进 SMA50)",
      lookup("收盘价高于20日均线的1.05倍", TB) is None)

# ④ 区间:本地规则故意拒绝的,AI 翻过就能学 ─────────────────────
E = kw.learn_entries("市盈率大于10小于20",
                     ["price_earnings_ttm > 10 and price_earnings_ttm < 20"], RULE_OK)
TB = table_of(E)
check("区间 · 两个数字各占一个空位",
      lookup("市盈率大于5小于30", TB) == ["price_earnings_ttm > 5 and price_earnings_ttm < 30"])

# ⑤ 同一个值出现两次 —— 分不清谁对谁,不挖空 ────────────────────
E = kw.learn_entries("市盈率大于10小于10",
                     ["price_earnings_ttm > 10 and price_earnings_ttm < 10"], RULE_OK)
check("同值 · 不挖空,只记原样", E and "{" not in E[0][0], str(E))

# ⑥ 多句 · 对得齐:逐句学,规则认得的那句不学 ───────────────────
E = kw.learn_entries("成交量大于100万，RS线连涨超过50天",
                     ["volume > 1000000", "rs_line_up_days > 50"], RULE_OK)
check("多句 · 规则认得的「成交量大于100万」不学,只学 RS 那句",
      [k for k, _e in E] == ["rs线连涨超过{0}天"], str(E))
E = kw.learn_entries("市盈率大于10小于20，RS线连涨超过50天",
                     ["price_earnings_ttm > 10 and price_earnings_ttm < 20", "rs_line_up_days > 50"],
                     RULE_OK)
check("多句 · 两句都规则认不出,两句都学", len(E) == 2, str(E))
TB = table_of(E)
exprs, _m = tr("RS线连涨超过30天，市盈率大于8小于25", TB)
check("多句 · ⭐学完之后换顺序、换数字的新组合也能本地认",
      exprs == ["rs_line_up_days > 30", "price_earnings_ttm > 8 and price_earnings_ttm < 25"],
      str(exprs))

# ⑦ 多句 · AI 把顺序弄乱了:不能逐句对错,只记整句 ──────────────
E = kw.learn_entries("市盈率大于10小于20，RS线连涨超过50天",
                     ["rs_line_up_days > 50", "price_earnings_ttm > 10 and price_earnings_ttm < 20"],
                     RULE_OK)
check("错位 · ⭐不逐句学(否则「市盈率…」会被永远翻成 RS 线)",
      len(E) == 1 and "市盈率" in E[0][0] and "rs线" in E[0][0], str(E))   # key 已归一化,全角逗号成了半角
TB = table_of(E)
exprs, _m = tr("市盈率大于10小于20，RS线连涨超过50天", TB)
check("错位 · 整句原样再问能命中",
      exprs is not None and sorted(exprs) == sorted(["rs_line_up_days > 50",
                                                     "price_earnings_ttm > 10 and price_earnings_ttm < 20"]),
      str(exprs))
check("错位 · 单独问其中一句不命中", lookup("市盈率大于10小于20", TB) is None)

# ⑧ 对照表绝不能盖过本地规则 ─────────────────────────────────
BOGUS = table_of([("成交量大于{0}万", ["volume < {0}"])])     # 一条故意学错的
exprs, _m = tr("成交量大于100万", BOGUS)
check("规则优先 · ⭐规则认得的句子,对照表里就算有错的记录也不用", exprs == ["volume > 1000000"], str(exprs))

# ⑨ 把 AI 的中间变量展开成自包含表达式 ─────────────────────────
conds = [{"name": "sma50", "expr": "Average(close, 50)", "is_bool": False},
         {"name": "c1", "expr": "close > sma50", "is_bool": True},
         {"name": "c2", "expr": "volume > 1000000", "is_bool": True}]
check("展开 · 中间变量被替换进去",
      kw.inline_conditions(conds, ["c1", "c2"]) == ["close > (Average(close, 50))", "volume > 1000000"],
      str(kw.inline_conditions(conds, ["c1", "c2"])))
check("展开 · 引用了不存在的条件就不学", kw.inline_conditions(conds, ["c1", "nope"]) is None)
check("展开 · 没有 plot 引用(自定义组合)就不学", kw.inline_conditions(conds, []) is None)

# ⑩ 存储层只按 candidate_keys 去库里查 —— 候选必须覆盖 translate 实际会用到的 key。
#    两边的归一化/切分只要差一点,线上就是「明明学过却查不到」,而且不报错。
ALL = {}
for t, ex in [("RS线连涨超过50天", ["rs_line_up_days > 50"]),
              ("市值在100亿以上", ["market_cap_basic > 10000000000"]),
              ("收盘价高于50日均线的1.05倍", ["close > SMA50 * 1.05"]),
              ("市盈率大于10小于20", ["price_earnings_ttm > 10 and price_earnings_ttm < 20"])]:
    ALL.update(table_of(kw.learn_entries(t, ex, RULE_OK), start_id=len(ALL) + 1))
for q in ["RS线连涨超过60天", "市值在50亿以上", "收盘价高于50日均线的1.1倍",
          "市盈率大于5小于30", "RS线连涨超过30天，市盈率大于8小于25", "  RS 线 连涨 超过 60 天 "]:
    full, _m1 = tr(q, ALL)
    narrowed = {k: v for k, v in ALL.items() if k in set(kw.candidate_keys(q))}
    only, _m2 = tr(q, narrowed)
    check(f"候选 key · 只按候选查也能命中「{q.strip()}」", full is not None and only == full,
          f"整表={full} 只查候选={only}")

total = passed + len(fails)
print(f"对照表用例 {total} 条")
if fails:
    print(f"FAIL {len(fails)} 条:")
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("ALL OK")
