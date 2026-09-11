# -*- coding: utf-8 -*-
"""RS 线上涨天数 + 精确 RS Raw + 扫描时的日线接入 —— 不联网、不连库,不依赖 pytest。

    cd apps/api && PYTHONPATH=. python tests/test_rs_line.py

口径(2026-09-11 用户选定):RS 线 = 收盘 ÷ 基准指数;
「上涨天数」= RS 线**连续**站在自身 21 日均线之上的交易日数(从最新一天往回数)。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import date, timedelta

_API = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(_API, "app", "services", "quant", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rh = _load("rs_history")
rs = _load("screen_rs")

D0 = date(2026, 1, 1)


def _days(n):
    return [D0 + timedelta(days=i) for i in range(n)]


def _series(vals, bench_vals=None):
    ds = _days(len(vals))
    bench = {d: (bench_vals[i] if bench_vals else 1.0) for i, d in enumerate(ds)}
    return list(zip(ds, vals)), bench


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


# ── rs_line_stats ─────────────────────────────────────────────

@case
def 一路上涨_天数等于能算均线的全部天数_且标记截断():
    st, b = _series([100 + i for i in range(60)])
    s = rh.rs_line_stats(st, b)
    # 第 21 天起才有均线 → 60-20 = 40 天,全部站上
    assert s["up_days"] == 40 and s["up_days_censored"] is True, s


@case
def 最新一天跌破均线_天数为零():
    vals = [100 + i for i in range(59)] + [50]
    st, b = _series(vals)
    s = rh.rs_line_stats(st, b)
    assert s["up_days"] == 0 and s["up_days_censored"] is False, s


@case
def 中途跌破后重新站上_只数最近这一段():
    vals = [100.0] * 30 + [90.0] * 10 + [200.0] * 7
    st, b = _series(vals)
    s = rh.rs_line_stats(st, b)
    assert s["up_days"] == 7 and not s["up_days_censored"], s


@case
def 看的是RS线不是股价_股价涨但跑输基准算下跌():
    n = 60
    stock = [100 * (1.01 ** i) for i in range(n)]        # 每天涨 1%
    bench = [100 * (1.02 ** i) for i in range(n)]        # 基准每天涨 2%
    st, b = _series(stock, bench)
    s = rh.rs_line_stats(st, b)
    assert s["up_days"] == 0, s


@case
def 股价跌但跌得比基准少_算上涨():
    n = 60
    stock = [100 * (0.995 ** i) for i in range(n)]
    bench = [100 * (0.98 ** i) for i in range(n)]
    st, b = _series(stock, bench)
    s = rh.rs_line_stats(st, b)
    assert s["up_days"] == 40, s


@case
def 只在两边都有收盘价的日子上算_错位日不参与():
    ds = _days(40)
    stock = [(d, 100.0 + i) for i, d in enumerate(ds)]
    bench = {d: 1.0 for i, d in enumerate(ds) if i != 35}     # 基准第 36 天休市
    s = rh.rs_line_stats(stock, bench)
    assert s["n_days"] == 39, s


@case
def 不足21天_返回空_不硬算():
    st, b = _series([100.0] * 20)
    assert rh.rs_line_stats(st, b) is None


@case
def 等于均线不算站上():
    st, b = _series([100.0] * 30)
    assert rh.rs_line_stats(st, b)["up_days"] == 0


# ── rs_raw_exact ──────────────────────────────────────────────

@case
def 精确RS_Raw_手算():
    c = [1.0] * 400
    c[-1 - 252] = 0.5     # 12 月前 0.5 → ROC252 = 100%
    c[-1 - 189] = 0.8     # ROC189 = 25%
    c[-1 - 126] = 0.8     # ROC126 = 25%
    c[-1 - 63] = 0.5      # ROC63 = 100%
    got = rh.rs_raw_exact(c)
    want = 0.4 * 1.0 + 0.2 * 0.25 + 0.2 * 0.25 + 0.2 * 1.0
    assert abs(got - want) < 1e-12, got


@case
def 精确RS_Raw_不足253根不给():
    assert rh.rs_raw_exact([1.0] * 252) is None
    assert rh.rs_raw_exact([1.0] * 253) is not None


# ── 拆股校验(腾讯美股的前复权不总是复权了拆股)──────────────────

def _flat(n=300, start=D0, price=100.0):
    return [(start + timedelta(days=i), price) for i in range(n)]


def _anchors(series, **perf):
    return rh.perf_anchors(series[-1][0], perf)


@case
def 对得上的序列原样返回():
    s = _flat()
    out, fixed = rh.repair_splits(s, _anchors(s, **{"Perf.3M": 0.0, "Perf.Y": 0.0}))
    assert out == s and fixed == 0


@case
def 一拆二没复权_找到拆股日并修正():
    s = _flat()
    split = 200
    s = [(d, c * (2 if i < split else 1)) for i, (d, c) in enumerate(s)]   # 拆股前 200,后 100
    # 真实:一路 100 没动(复权后)→ 所有锚点真实涨幅 0
    out, fixed = rh.repair_splits(s, _anchors(s, **{"Perf.W": 0, "Perf.1M": 0, "Perf.3M": 0,
                                                   "Perf.6M": 0, "Perf.Y": 0}))
    assert fixed == 1, fixed
    assert all(abs(c - 100.0) < 1e-9 for _, c in out), out[:3]


@case
def 合股没复权_反方向也能修():
    s = _flat()
    s = [(d, c * (0.1 if i < 250 else 1)) for i, (d, c) in enumerate(s)]    # 1 合 10:前 10、后 100
    out, fixed = rh.repair_splits(s, _anchors(s, **{"Perf.1M": 0, "Perf.3M": 0, "Perf.Y": 0}))
    assert fixed == 1 and all(abs(c - 100.0) < 1e-9 for _, c in out)


@case
def 两次合股_锚点覆盖的修正_覆盖不到的截断():
    # 仿 AZI(2026-09-11 实测):先 1 合 40,再 1 合 8;第二次合股当天股价本身还涨了 20%
    s = _flat()
    s = [(d, c * (1 / 40 if i < 100 else 1) * (1 / 8 if i < 250 else 1)) for i, (d, c) in enumerate(s)]
    s = [(d, c * (1 / 1.2 if i < 250 else 1)) for i, (d, c) in enumerate(s)]   # 真实:第 250 天涨 20%
    anchors = _anchors(s, **{"Perf.W": 0, "Perf.1M": 0, "Perf.3M": 20.0, "Perf.6M": 20.0,
                             "Perf.Y": 20.0})
    out, fixed = rh.repair_splits(s, anchors)
    # 第二次合股(第 250 天)被锚点抓到并修正;第一次(第 100 天)早于最老的可用锚点(6 月,4/27),
    # 核对不了 —— 从那次跳变处截断,只留第 100 天之后
    assert out is not None and fixed == 1, (fixed,)
    assert len(out) == 200 and out[0][0] == D0 + timedelta(days=100), (len(out), out[0])
    assert abs(out[0][1] - 100 / 1.2) < 1e-6 and abs(out[-1][1] - 100) < 1e-9, (out[0], out[-1])


@case
def 真跌一半不是拆股_锚点对得上就不动():
    s = _flat()
    s = [(d, c * (2 if i < 200 else 1)) for i, (d, c) in enumerate(s)]
    # 第 200 天(7 月 20 日)跌一半;扫描源也说 6 个月前到现在跌了一半 —— 那就是真跌,不能"修"
    # (3 个月锚点 7 月 27 日在下跌之后,真实涨幅 0)
    anchors = _anchors(s, **{"Perf.W": 0, "Perf.1M": 0, "Perf.3M": 0, "Perf.6M": -50.0})
    out, fixed = rh.repair_splits(s, anchors)
    assert fixed == 0 and out == s


@case
def 对不上又找不到跳变日_不给数():
    s = [(D0 + timedelta(days=i), 100.0 + i) for i in range(300)]      # 平滑上涨,没有单日跳变
    out, _ = rh.repair_splits(s, _anchors(s, **{"Perf.3M": -60.0}))
    assert out is None


@case
def 次新股_早于上市日的锚点跳过():
    s = _flat(n=40)
    out, fixed = rh.repair_splits(s, _anchors(s, **{"Perf.Y": 500.0, "Perf.W": 0}))
    assert out == s and fixed == 0


# ── tx_symbol ─────────────────────────────────────────────────

@case
def 腾讯代码映射():
    assert rh.tx_symbol("us", "AAPL", "NASDAQ") == "usAAPL.OQ"
    assert rh.tx_symbol("us", "JPM", "NYSE") == "usJPM.N"
    assert rh.tx_symbol("us", "BRK.A", "NYSE") == "usBRK.A.N"
    assert rh.tx_symbol("us", "XYZ", "OTC") is None          # 拿不准不猜
    assert rh.tx_symbol("a", "600519", "SSE") == "sh600519"
    assert rh.tx_symbol("a", "000858", "SZSE") == "sz000858"
    assert rh.tx_symbol("a", "830799", "BSE") is None        # 北交所腾讯日线未验证
    assert rh.tx_symbol("hk", "700", "HKEX") == "hk00700"


# ── screen_rs.inject 接入日线 ─────────────────────────────────

TODAY = date(2026, 9, 11)
ASOF = date(2026, 9, 10)


def _rows(n=100):
    return [{"_code": f"S{i}", "Perf.3M": i, "Perf.6M": i, "Perf.Y": i, "SMA250": 1.0,
             "exchange": "NASDAQ", "market_cap_basic": 1e9} for i in range(1, n + 1)]


def _hist(n=100, as_of=ASOF, raw=lambda i: -i):
    return {f"S{i}": {"as_of": as_of, "up_days": i, "censored": False,
                      "rs_raw_exact": raw(i)} for i in range(1, n + 1)}


@case
def 日线齐全_评级用精确法():
    rows = _rows()
    st = rs.inject(rows, "us", _hist(), today=TODAY)
    assert st["method"] == "exact", st
    # 精确 raw 故意和快照反着排:S100 快照最强、精确最弱
    # (100 只里垫底:0.01×98+1 = 1.98 → 2)
    assert rows[-1]["rs_rating"] == 2 and rows[0]["rs_rating"] == 99, (rows[0]["rs_rating"], rows[-1]["rs_rating"])
    assert rows[49]["rs_line_up_days"] == 50


@case
def 日线覆盖不足90pct_整批退回快照法_不混用():
    rows = _rows()
    h = _hist(n=80)                                  # 80/100 < 90%
    st = rs.inject(rows, "us", h, today=TODAY)
    assert st["method"] == "snapshot", st
    assert rows[-1]["rs_rating"] == 99               # 快照口径
    # RS 线天数与评级算法无关:有就给
    assert rows[0]["rs_line_up_days"] == 1 and rows[-1]["rs_line_up_days"] is None


@case
def 日线过期_天数全空_评级退回快照():
    rows = _rows()
    st = rs.inject(rows, "us", _hist(as_of=date(2026, 9, 1)), today=TODAY)
    assert st["method"] == "snapshot" and st["hist_stale"] is True, st
    assert all(r["rs_line_up_days"] is None for r in rows)


@case
def 个股日期落后于全市场_当作没有():
    rows = _rows()
    h = _hist()
    h["S5"]["as_of"] = date(2026, 9, 8)              # 停牌 / 昨晚没拉到
    rs.inject(rows, "us", h, today=TODAY)
    assert rows[4]["rs_line_up_days"] is None and rows[5]["rs_line_up_days"] == 6


@case
def 不在排名池里的票_不给天数():
    rows = _rows()
    rows[0]["exchange"] = "OTC"
    rs.inject(rows, "us", _hist(), today=TODAY)
    assert rows[0]["rs_line_up_days"] is None


@case
def 没有日线_行为与原来完全一样():
    a, b = _rows(), _rows()
    rs.inject(a, "us")
    rs.inject(b, "us", {}, today=TODAY)
    assert [r["rs_rating"] for r in a] == [r["rs_rating"] for r in b]
    assert all(r["rs_line_up_days"] is None for r in a)


def _run():
    fails = []
    for fn in CASES:
        try:
            fn()
        except AssertionError as e:
            fails.append(f"{fn.__name__}: {e}")
    return fails


def test_rs_line():
    fails = _run()
    assert not fails, "\n" + "\n".join(fails)


if __name__ == "__main__":
    fails = _run()
    print(f"RS 线用例 {len(CASES)} 条")
    if fails:
        print(f"FAIL {len(fails)} 条:")
        for f in fails:
            print("  " + f)
        sys.exit(1)
    print("ALL OK")
