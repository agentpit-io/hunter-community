# -*- coding: utf-8 -*-
"""筛选求值(screen_dsl._eval)的缺数据语义 —— 不联网,不依赖 pytest 也能跑。

    cd apps/api && PYTHONPATH=. python tests/test_screen_eval.py

## 为什么要有它

2026-09-11 用户问「2547 只算不出,具体缺哪个字段」。查下来 2537 只(99.6%)
早就被别的条件判了不满足 —— `and` 原来只要一边是空就整体算不出,
把"算不出"放大了 250 倍,用户据此去怀疑数据源,而问题根本不在那里。

这里钉住三值逻辑(Kleene)的真值表,以及"命中集合不因此改变"这条不变量。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_screen_kw import _load  # noqa: E402  复用同一个轻量加载器

sd, _ = _load()

FIELDS = {"close", "volume", "return_on_equity", "SMA50"}
has = lambda n: n in FIELDS                                   # noqa: E731


def _verdict(script: str, row: dict):
    c = sd.compile_script(script, has, [50], [50], [])
    cache = sd.build_resolver_cache(c, has, [50], [50], [])
    env: dict = {}
    for st in c.stmts:
        env[st.name] = sd._eval(st.node, row, env, cache)
    return sd._truthy(env.get(c.plot_name))


CASES = [
    # (说明, 脚本, 行, 期望:True/False/None)
    ("假 且 未知 = 假(已明确不满足,缺什么都不会命中)",
     "plot scan = close > 20 and return_on_equity > 15;",
     {"close": 5, "return_on_equity": None}, False),
    ("未知 且 假 = 假(与左右顺序无关)",
     "plot scan = return_on_equity > 15 and close > 20;",
     {"close": 5, "return_on_equity": None}, False),
    ("真 且 未知 = 未知(这才是真正的算不出)",
     "plot scan = close > 20 and return_on_equity > 15;",
     {"close": 50, "return_on_equity": None}, None),
    ("真 且 真 = 真",
     "plot scan = close > 20 and return_on_equity > 15;",
     {"close": 50, "return_on_equity": 20}, True),
    ("真 或 未知 = 真(或的一边已满足)",
     "plot scan = close > 20 or return_on_equity > 15;",
     {"close": 50, "return_on_equity": None}, True),
    ("假 或 未知 = 未知",
     "plot scan = close > 20 or return_on_equity > 15;",
     {"close": 5, "return_on_equity": None}, None),
    ("非 未知 = 未知",
     "plot scan = not (return_on_equity > 15);",
     {"return_on_equity": None}, None),
    ("比较里有未知 = 未知(不当 0,也不当不满足)",
     "plot scan = close > SMA50;",
     {"close": 50, "SMA50": None}, None),
    ("除零 = 未知",
     "plot scan = close / volume > 1;",
     {"close": 50, "volume": 0}, None),
    ("def 链式传递:中间条件为假,整体为假",
     "def a = close > 20; def b = return_on_equity > 15; plot scan = a and b;",
     {"close": 5, "return_on_equity": None}, False),
]


def _run() -> list[str]:
    fails = []
    for desc, script, row, want in CASES:
        got = _verdict(script, row)
        if got is not want:
            fails.append(f"{desc}\n    期望 {want}  实得 {got}")
    return fails


def test_screen_eval():
    fails = _run()
    assert not fails, "\n" + "\n".join(fails)


if __name__ == "__main__":
    fails = _run()
    print(f"三值逻辑用例 {len(CASES)} 条")
    if fails:
        print(f"FAIL {len(fails)} 条:")
        for f in fails:
            print("  " + f)
        sys.exit(1)
    print("ALL OK")
