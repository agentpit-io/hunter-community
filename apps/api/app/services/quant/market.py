"""量化侧的「市场」—— 唯一口径。回测、因子、数据下载按市场分开,都从这里判。

2026-09-11 起支持美股回测(用户要求:数据页勾选全美股下载 → 工作台回测)。
`klines` / `data_coverage` / `factor_value` 都**没有可靠的市场列**(factor_value 的 market 一直写死 'A'),
而加列要迁移几十万行的大表。所以市场由**代码形态**决定:

    'a'   纯数字   600519 / 000300(A 股个股与 A 股指数)/ 00700(历史遗留的 5 位也归这里)
    'us'  含字母   AAPL / BRK.A,以及美股基准 .INX

为什么 5 位数字也归 'a':上线前库里 559 只全是 6 位代码(实测),把"所有数字代码"划给 'a',
A 股的每一条 SQL 结果与改动前**逐字节相同**(scripts/regress_a_backtest.py 兜着)。
港股回测不在这次范围,真要做时再从 'a' 里拆出来。

## 美股基准存成 `.INX`

标普500 写进 `klines` 时 code = `.INX`(腾讯代码 us.INX)。以点开头 —— 不可能与任何真实代码撞。
A 股这边吃过撞号的亏:000905(中证500)同时是厦门港务的代码,两边会互相覆盖。
美股交易日历就取 `.INX` 的日期(纽交所真实交易日),不取全表并集。
"""
from __future__ import annotations

import re

A = "a"
US = "us"

US_BENCH = ".INX"
US_BENCH_TX = "us.INX"
BENCH_LABEL = {US_BENCH: "标普 500"}

# 显式白名单:这些"代码"是指数,不是股票 —— 不能进股票池、不能算因子
US_BENCHMARKS = {US_BENCH}

# SQL 片段(列名固定叫 code)。和 market_of_code 必须是同一个口径
SQL_IS_A = "code ~ '^[0-9]+$'"
SQL_IS_US = "code !~ '^[0-9]+$'"

_DIGITS = re.compile(r"^[0-9]+$")

# 股票池 → 市场。没列出来的都是 A 股池(hs300 / zz500 / my_watchlist …)
_UNIVERSE_MARKET = {"us_all": US}


def market_of_code(code: str) -> str:
    return A if _DIGITS.match(str(code or "")) else US


def sql_filter(market: str) -> str:
    return SQL_IS_US if market == US else SQL_IS_A


def split_by_market(codes) -> dict[str, list[str]]:
    """按市场分组,保持原顺序。只返回非空的组。"""
    out: dict[str, list[str]] = {}
    for c in codes:
        out.setdefault(market_of_code(c), []).append(c)
    return out


def is_benchmark(code: str) -> bool:
    return code in US_BENCHMARKS


def market_of_universe(key: str | None) -> str:
    return _UNIVERSE_MARKET.get(key or "", A)


def bench_for(market: str) -> str:
    return US_BENCH if market == US else "000300"


def lot_multiplier(code: str) -> int:
    """klines.volume 的单位 → 股数的倍数。美股是股(1);A 股是手(100),科创板 688 例外是股。
    A 股的判断保持 factor_engine._shares_traded 原来的逻辑,见仓内 CLAUDE.md「数据坑」。"""
    if market_of_code(code) == US:
        return 1
    return 1 if str(code).startswith("688") else 100
