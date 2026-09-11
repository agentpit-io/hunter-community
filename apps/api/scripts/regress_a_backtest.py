# -*- coding: utf-8 -*-
"""A 股回测逐字节回归 —— 加美股之前录基线,改完之后比对。

    # 在 api 容器里跑(需要数据库)
    docker compose exec -T -w /app -e PYTHONPATH=/app api python scripts/regress_a_backtest.py --record /tmp/a_base.json
    docker compose exec -T -w /app -e PYTHONPATH=/app api python scripts/regress_a_backtest.py --compare /tmp/a_base.json

## 为什么要有它

加美股要改三处"全表"查询:交易日历(klines 全表日期并集)、每日因子批次(covered_codes)、
因子截面(compute_and_store)。任何一处漏了市场过滤,**A 股回测结果会悄悄变**,
而回测结果按 spec_hash 缓存,缓存键不含数据版本 —— 用户永远看到旧的,新跑的又不一样,没人发现。
所以约定:改动前录一份,改完逐字节比对,**有任何差异不上线**。

## 截止日为什么固定在 45 天之前

17:10 本地流水线每天重写最近 45 天的日线(scheduler.daily_local_pipeline,lo = today-45),
前复权基准一变,那段收盘价就变了。截止日取在那之前,基线才不会自己漂 ——
否则一夜之后,即使代码一行没动,比对也会报差异。

直接调 run_backtest,绕开 backtest_result 缓存。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date

from app.services.database import get_conn
from app.services.quant import backtest_engine as bt
from app.services.quant import universe as uv

END = date(2026, 7, 15)
WINDOWS = {"1y": date(2025, 7, 15), "3y": date(2023, 8, 14)}

TECH = [{"key": "momentum_6m", "weight_pct": 40}, {"key": "ma_align", "weight_pct": 30},
        {"key": "vol_20d_inv", "weight_pct": 30}]
MIXED = [{"key": "momentum_12m_1m", "weight_pct": 30}, {"key": "roa", "weight_pct": 25},
         {"key": "gross_margin", "weight_pct": 20}, {"key": "beta_60", "weight_pct": 25}]
PARAM = [{"key": "rsi", "weight_pct": 50, "params": {"period": 10, "oversold": 25, "overbought": 75}},
         {"key": "ma_align", "weight_pct": 50, "params": {"ma1": 5, "ma2": 20, "ma3": 60, "ma4": 120}}]


def _cases() -> list[tuple[str, dict, date, date]]:
    out = []
    # 纯技术因子:全矩阵
    for uni in ("hs300", "zz500"):
        for freq in ("W", "M", "Q"):
            for win, start in WINDOWS.items():
                for slip in ("bp_static", "sqrt_impact"):
                    bench = "000300" if uni == "hs300" else "000905"
                    cfg = {"universe": uni, "top_n": 20, "rebalance": freq, "cost_bps": 15,
                           "benchmark": bench, "slippage_model": slip}
                    out.append((f"tech·{uni}·{freq}·{win}·{slip}", {"factors": TECH, "config": cfg},
                                start, END))
    # 混合 / 带参数:挑几组(带参数的要实时重算 z,慢)
    for name, fs in (("mixed", MIXED), ("param", PARAM)):
        for uni, freq, win in (("hs300", "M", "1y"), ("zz500", "W", "1y"), ("hs300", "Q", "3y")):
            cfg = {"universe": uni, "top_n": 30, "rebalance": freq, "cost_bps": 10,
                   "benchmark": "000300"}
            out.append((f"{name}·{uni}·{freq}·{win}", {"factors": fs, "config": cfg},
                        WINDOWS[win], END))
    return out


_VOLATILE = {"duration_ms", "elapsed_ms", "elapsed", "took_ms", "computed_at", "created_at"}


def _canon(x):
    if isinstance(x, dict):
        return {k: _canon(v) for k, v in sorted(x.items()) if k not in _VOLATILE}
    if isinstance(x, (list, tuple)):
        return [_canon(v) for v in x]
    if isinstance(x, float):
        return repr(x)                   # 逐字节:浮点按 repr 比,不做容差
    if isinstance(x, date):
        return x.isoformat()
    return x


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(_canon(obj), ensure_ascii=False, sort_keys=True,
                                     default=str).encode()).hexdigest()


def collect() -> dict:
    res: dict = {"end": END.isoformat(), "cases": {}, "aux": {}}
    for name, strat, start, end in _cases():
        try:
            r = bt.run_backtest(strat, start, end)
        except Exception as e:                                  # noqa: BLE001
            r = {"exception": f"{type(e).__name__}: {e}"}
        res["cases"][name] = {"hash": _h(r), "body": _canon(r)}
        print(f"  {name:40s} {res['cases'][name]['hash'][:12]}", flush=True)

    for freq in ("W", "M", "Q", "H"):
        d = bt._rebalance_dates(WINDOWS["3y"], END, freq)
        res["aux"][f"rebalance_{freq}"] = [x.isoformat() for x in d]
    res["aux"]["covered_codes"] = sorted(uv.covered_codes())
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""SELECT md5(string_agg(trade_date::text || factor_key || code
                                         || coalesce(raw_value::text,'') || coalesce(z_score::text,'')
                                         || coalesce(pct_rank::text,''),
                                         '|' ORDER BY trade_date, factor_key, code)), count(*)
                     FROM factor_value WHERE trade_date <= %s""", (END,))
    md5, n = cur.fetchone()
    res["aux"]["factor_value"] = {"md5": md5, "rows": n}
    cur.close()
    conn.close()
    return res


def main() -> int:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--record")
    g.add_argument("--compare")
    a = p.parse_args()
    cur = collect()
    if a.record:
        with open(a.record, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False)
        print(f"基线已录:{len(cur['cases'])} 组回测 · factor_value {cur['aux']['factor_value']}")
        return 0
    with open(a.compare, encoding="utf-8") as f:
        base = json.load(f)
    bad = []
    for name, v in base["cases"].items():
        now = cur["cases"].get(name)
        if not now or now["hash"] != v["hash"]:
            bad.append(name)
    for k, v in base["aux"].items():
        if cur["aux"].get(k) != v:
            bad.append("aux:" + k)
    if bad:
        print(f"❌ {len(bad)} 处与基线不同:")
        for b in bad:
            print("   ", b)
            if not b.startswith("aux:"):
                x, y = base["cases"][b]["body"], cur["cases"].get(b, {}).get("body")
                for key in sorted(set(x) | set(y or {})):
                    if (y or {}).get(key) != x.get(key):
                        print(f"       字段 {key} 不同")
        return 1
    print(f"✅ 与基线逐字节一致:{len(base['cases'])} 组回测 + {len(base['aux'])} 项辅助数据")
    return 0


if __name__ == "__main__":
    sys.exit(main())
