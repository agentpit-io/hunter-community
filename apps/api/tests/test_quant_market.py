# -*- coding: utf-8 -*-
"""量化侧的市场判定(quant/market.py)—— 不联网、不连库,不依赖 pytest。

    cd apps/api && PYTHONPATH=. python tests/test_quant_market.py

市场判定错一个,后果都是静默的:A 股回测的交易日历混进美股日期、
因子截面把美股和 A 股放在一起排名、每日流水线拿 AAPL 去打 A 股接口。
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys

_API = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "market", os.path.join(_API, "app", "services", "quant", "market.py"))
mk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mk)

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


SAMPLE = {
    "600519": "a", "000300": "a", "000905": "a", "688981": "a", "00700": "a",
    "AAPL": "us", "BRK.A": "us", "BF.A": "us", ".INX": "us", "MU": "us",
}


@case
def 代码形态判市场():
    for code, want in SAMPLE.items():
        assert mk.market_of_code(code) == want, (code, mk.market_of_code(code))


@case
def Python判定与SQL正则同一口径():
    # SQL 用 Postgres 的 ~ / !~,这里用同一个正则在 Python 里验一遍,防止两处口径漂移
    pat = re.search(r"'(.+)'", mk.SQL_IS_A).group(1)
    for code, want in SAMPLE.items():
        is_a = re.match(pat, code) is not None
        assert is_a == (want == "a"), code
    assert mk.SQL_IS_US.replace("!~", "~") == mk.SQL_IS_A


@case
def 分组保持原顺序_只返回非空组():
    g = mk.split_by_market(["600519", "AAPL", "000858", "NVDA"])
    assert g == {"a": ["600519", "000858"], "us": ["AAPL", "NVDA"]}, g
    assert mk.split_by_market(["600519"]) == {"a": ["600519"]}
    assert mk.split_by_market([]) == {}


@case
def 美股基准不是股票():
    assert mk.is_benchmark(".INX") and not mk.is_benchmark("AAPL")
    assert mk.bench_for("us") == ".INX" and mk.bench_for("a") == "000300"


@case
def 股票池到市场_没登记的都是A股():
    assert mk.market_of_universe("us_all") == "us"
    for k in ("hs300", "zz500", "my_watchlist", None, ""):
        assert mk.market_of_universe(k) == "a", k


@case
def 成交量换算成股():
    # A 股腾讯源是手(×100),科创板 688 是股(见 CLAUDE.md 数据坑);美股是股
    assert mk.lot_multiplier("600519") == 100
    assert mk.lot_multiplier("688981") == 1
    assert mk.lot_multiplier("AAPL") == 1


def _run():
    fails = []
    for fn in CASES:
        try:
            fn()
        except AssertionError as e:
            fails.append(f"{fn.__name__}: {e}")
    return fails


def test_quant_market():
    fails = _run()
    assert not fails, "\n" + "\n".join(fails)


if __name__ == "__main__":
    fails = _run()
    print(f"市场判定用例 {len(CASES)} 条")
    if fails:
        print(f"FAIL {len(fails)} 条:")
        for f in fails:
            print("  " + f)
        sys.exit(1)
    print("ALL OK")
