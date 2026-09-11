# -*- coding: utf-8 -*-
"""美股日线(quant/us_kline.py)纯计算部分 —— 不联网、不连库,不依赖 pytest。

    cd apps/api && PYTHONPATH=. python tests/test_us_kline.py

每一条对应一个"静默出错"的方式:字段顺序认错(收盘当最高)、拆股修正只修了收盘、
盘中半截 K 线当收盘价入库、复权基准变了却只补尾巴(拆股日凭空 -50%)。
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

_API = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _API)          # 有的本机 Python 不把当前目录放进 sys.path

from app.services.quant import rs_history as rh   # noqa: E402
from app.services.quant import us_kline as uk     # noqa: E402

D0 = date(2026, 1, 1)
CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def _bars(n=300, price=100.0, vol=1_000_000):
    # (d, o, c, h, l, v)
    return [(D0 + timedelta(days=i), price, price, price * 1.01, price * 0.99, vol) for i in range(n)]


def _anchors(bars, **perf):
    return uk.anchors_for(bars[-1][0], perf)


@case
def 腾讯字段顺序_收盘在最高前面():
    raw = [["2026-09-10", "316.670", "326.570", "326.740", "316.510", "70011913.000"]]
    d, o, c, h, l, v = uk.parse_bars(raw)[0]
    assert (o, c, h, l, v) == (316.67, 326.57, 326.74, 316.51, 70011913.0), (o, c, h, l, v)


@case
def 解析丢掉残缺和非正的K线():
    raw = [["2026-09-10", "1", "0", "1", "1", "5"], ["坏日期", "1", "1", "1", "1", "1"],
           ["2026-09-11", "1", "2"], ["2026-09-12", "10", "11", "12", "9", "100"]]
    out = uk.parse_bars(raw)
    assert len(out) == 1 and out[0][0] == date(2026, 9, 12), out


@case
def 拆股修正作用到开高低收和成交量():
    b = _bars()
    # 第 200 天 1 拆 2 没复权:之前价格是 200、量是一半
    b = [(d, o * 2, c * 2, h * 2, l * 2, v / 2) if i < 200 else (d, o, c, h, l, v)
         for i, (d, o, c, h, l, v) in enumerate(b)]
    out, nfix = uk.repair_ohlcv(b, _anchors(b, **{"Perf.W": 0, "Perf.1M": 0, "Perf.3M": 0,
                                                  "Perf.6M": 0, "Perf.Y": 0}))
    assert nfix == 1 and out is not None
    d, o, c, h, l, v = out[0]
    assert abs(c - 100) < 1e-9 and abs(h - 101) < 1e-9 and abs(l - 99) < 1e-9, out[0]
    assert abs(v - 1_000_000) < 1e-6, v            # 股数翻倍还原,成交额不变


@case
def 修不好就不入库():
    b = [(D0 + timedelta(days=i), 100 + i, 100 + i, 101 + i, 99 + i, 1e6) for i in range(300)]
    out, _ = uk.repair_ohlcv(b, _anchors(b, **{"Perf.3M": -60.0}))
    assert out is None


@case
def K线大面积不自洽_整只不要():
    b = _bars()
    b = [(d, o, c, l, h, v) for d, o, c, h, l, v in b]       # 高低互换 = 格式变了
    out, _ = uk.repair_ohlcv(b, _anchors(b, **{"Perf.W": 0}))
    assert out is None


@case
def 远端锚点_3年5年够得着():
    last = date(2026, 9, 10)
    a = dict(uk.anchors_for(last, {"Perf.3Y": 50.0, "Perf.5Y": 100.0}))
    assert a[date(2023, 9, 10)] == 1.5 and a[date(2021, 9, 10)] == 2.0, a


@case
def 盘中半截K线不入库():
    b = _bars(3)
    today = b[-1][0]
    assert len(uk.drop_partial(b, datetime(today.year, today.month, today.day, 11, 0))) == 2
    assert len(uk.drop_partial(b, datetime(today.year, today.month, today.day, 17, 0))) == 3
    nxt = today + timedelta(days=1)
    assert len(uk.drop_partial(b, datetime(nxt.year, nxt.month, nxt.day, 9, 0))) == 3


@case
def 请求根数_封顶1500():
    t = date(2026, 9, 11)
    assert uk.bars_needed(t - timedelta(days=365), t) == 282
    assert uk.bars_needed(t - timedelta(days=365 * 10), t) == uk.N_MAX
    assert uk.bars_needed(t - timedelta(days=7), t) == 60


@case
def 复权基准没变_只补尾巴():
    new = _bars(30)
    old = {d: c for d, o, c, h, l, v in new[:25]}
    assert uk.same_basis(new, old)


@case
def 复权基准变了_整只重写():
    new = _bars(30)
    old = {d: c * 2 for d, o, c, h, l, v in new[:25]}     # 库里还是拆股前的价格
    assert not uk.same_basis(new, old)


@case
def 低价股的入库四舍五入不算基准变化():
    new = [(D0 + timedelta(days=i), 0.35, 0.3504, 0.36, 0.34, 1e6) for i in range(30)]
    old = {d: 0.350 for d, *_ in new}                       # NUMERIC(12,3) 存成 0.350
    assert uk.same_basis(new, old)


@case
def 重叠太少_当作变了走整只重写():
    new = _bars(30)
    assert not uk.same_basis(new, {new[0][0]: 100.0})


@case
def WAF_501必须抛出():
    class _R:
        status_code = 501
        text = '<!DOCTYPE html><script>window.location.href="https://waf.tencent..."'

    class _S:
        def get(self, *a, **k):
            return _R()
    old, rh._tls.s = getattr(rh._tls, "s", None), _S()
    old_rate = rh._RATE
    rh._RATE = 1000.0
    try:
        uk.fetch_ohlcv("usAAPL.OQ", 10)
        raise AssertionError("没有抛 WafBlocked")
    except rh.WafBlocked:
        pass
    finally:
        rh._tls.s = old
        rh._RATE = old_rate


def _run():
    fails = []
    for fn in CASES:
        try:
            fn()
        except AssertionError as e:
            fails.append(f"{fn.__name__}: {e}")
    return fails


def test_us_kline():
    fails = _run()
    assert not fails, "\n" + "\n".join(fails)


if __name__ == "__main__":
    fails = _run()
    print(f"美股日线用例 {len(CASES)} 条")
    if fails:
        print(f"FAIL {len(fails)} 条:")
        for f in fails:
            print("  " + f)
        sys.exit(1)
    print("ALL OK")
