# -*- coding: utf-8 -*-
"""RS 相对强度评级(screen_rs)—— 不联网,不依赖 pytest 也能跑。

    cd apps/api && PYTHONPATH=. python tests/test_screen_rs.py

评级行为的用例移植自原项目 IBD-RS-Rating 的 tests/test_rs.py(MIT):
范围 1–99、排序单调、并列取平均名次、次新股预热、覆盖率门槛、空输入。
它那一半关于"数据库增量重算"的用例对应的是它的数据管线,我们没有移植那部分。
"""
from __future__ import annotations

import importlib.util
import os
import sys

_API = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "screen_rs", os.path.join(_API, "app", "services", "quant", "screen_rs.py"))
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)


def _row(p3, p6, p12, sma250=1.0, exchange="NASDAQ", mcap=1e9):
    return {"Perf.3M": p3, "Perf.6M": p6, "Perf.Y": p12, "SMA250": sma250,
            "exchange": exchange, "market_cap_basic": mcap}


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def 手算公式():
    # 3/6/12 月涨 10/20/40% → 9 月插值 30%
    # 0.4*0.1 + 0.2*0.2 + 0.2*0.3 + 0.2*0.4 = 0.04+0.04+0.06+0.08 = 0.22
    got = rs.rs_raw_from_perf(10, 20, 40)
    assert abs(got - 0.22) < 1e-12, got


@case
def 任一周期缺失_返回空_不拿零补():
    assert rs.rs_raw_from_perf(None, 20, 40) is None
    assert rs.rs_raw_from_perf(10, None, 40) is None
    assert rs.rs_raw_from_perf(10, 20, None) is None


@case
def 评级范围_1到99():
    raw = {i: float(i) for i in range(1000)}
    out, _ = rs.rs_ratings(raw, 1000)
    assert min(out.values()) == 1 and max(out.values()) == 99, (min(out.values()), max(out.values()))


@case
def 排序单调_涨得多评级不会更低():
    raw = {i: float(i) for i in range(200)}
    out, _ = rs.rs_ratings(raw, 200)
    vals = [out[i] for i in range(200)]
    assert vals == sorted(vals)


@case
def 并列取平均名次():
    # 与 pandas rank(pct=True, method="average") 一致
    raw = {"a": 1.0, "b": 2.0, "c": 2.0, "d": 3.0}
    out, _ = rs.rs_ratings(raw, 4)
    assert out["b"] == out["c"]
    # b/c 的平均名次 2.5 → 2.5/4*98+1 = 62.25 → 62
    assert out["b"] == 62, out


@case
def 覆盖率不足_整批不给():
    # 分母 100、只有 50 只有效 → 50% < 90%
    raw = {i: float(i) for i in range(50)}
    raw.update({i: None for i in range(50, 100)})
    out, cov = rs.rs_ratings(raw, 100)
    assert out == {} and abs(cov - 0.5) < 1e-12, (len(out), cov)


@case
def 覆盖率刚好过线_给():
    raw = {i: float(i) for i in range(90)}
    raw.update({i: None for i in range(90, 100)})
    out, cov = rs.rs_ratings(raw, 100)
    assert len(out) == 90, len(out)


@case
def 空输入():
    out, cov = rs.rs_ratings({}, 0)
    assert out == {} and cov == 0.0


@case
def 次新股不给评级_即使有一年涨幅():
    # 扫描源对次新股的 Perf.Y 是"上市以来",不是真正的 12 个月 —— 必须看 SMA250
    rows = [_row(i, i, i) for i in range(1, 100)]
    rows.append(_row(50, 80, 300, sma250=None))       # 次新股:涨得最多,但不该有评级
    stat = rs.inject(rows)
    assert rows[-1]["rs_rating"] is None and rows[-1]["rs_raw"] is None
    assert stat["young"] == 1
    # 次新股计入分母:99/100 = 99% ≥ 90%,其余照常评级
    assert rows[0]["rs_rating"] is not None


@case
def 次新股太多时整批不给():
    rows = [_row(i, i, i) for i in range(1, 80)]
    rows += [_row(1, 1, 1, sma250=None) for _ in range(21)]  # 79/100 < 90%
    stat = rs.inject(rows)
    assert stat["gated"] is True
    assert all(r["rs_rating"] is None for r in rows)
    # RS Raw 是单只股票自己的数,照样给 —— 与原项目一致(它也照存 rs_raw)
    assert rows[0]["rs_raw"] is not None


@case
def 评级不受筛选条件影响_在全市场排():
    rows = [_row(i, i, i) for i in range(1, 101)]
    rs.inject(rows)
    top = rows[-1]["rs_rating"]
    assert top == 99, top


@case
def 美股OTC不参与排名_也不进分母():
    # 2026-09-11 实测:扫描源美股里 2439 只 OTC,一年涨 1218 万倍的粉单股占满 RS 99
    rows = [_row(i, i, i) for i in range(1, 101)]
    rows.append(_row(99999, 99999, 99999, exchange="OTC"))
    stat = rs.inject(rows, "us")
    assert rows[-1]["rs_rating"] is None and rows[-1]["rs_raw"] is None
    assert stat["excluded"] == 1 and stat["universe"] == 100
    assert rows[-2]["rs_rating"] == 99      # OTC 没把第一名挤下来


@case
def 微盘股不参与排名():
    rows = [_row(i, i, i) for i in range(1, 101)]
    rows.append(_row(500, 500, 500, mcap=1e7))          # 1000 万美元 < 5000 万
    rs.inject(rows, "us")
    assert rows[-1]["rs_rating"] is None


@case
def 市值缺失不参与排名_判断不了过不过门槛():
    rows = [_row(i, i, i) for i in range(1, 101)]
    rows.append(_row(50, 50, 50, mcap=None))
    stat = rs.inject(rows, "us")
    assert rows[-1]["rs_rating"] is None and stat["excluded"] == 1


@case
def A股不看交易所_只看市值门槛():
    rows = [_row(i, i, i, exchange="SSE", mcap=5e8) for i in range(1, 101)]
    rows.append(_row(5, 5, 5, exchange="SZSE", mcap=1e8))   # 1 亿人民币 < 3.6 亿
    stat = rs.inject(rows, "a")
    assert rows[0]["rs_rating"] is not None and rows[-1]["rs_rating"] is None
    assert stat["universe"] == 100


def _run():
    fails = []
    for fn in CASES:
        try:
            fn()
        except AssertionError as e:
            fails.append(f"{fn.__name__}: {e}")
    return fails


def test_screen_rs():
    fails = _run()
    assert not fails, "\n" + "\n".join(fails)


if __name__ == "__main__":
    fails = _run()
    print(f"RS 评级用例 {len(CASES)} 条")
    if fails:
        print(f"FAIL {len(fails)} 条:")
        for f in fails:
            print("  " + f)
        sys.exit(1)
    print("ALL OK")
