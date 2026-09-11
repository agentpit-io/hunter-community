# -*- coding: utf-8 -*-
"""美股回测自检 —— 下过美股数据后,在 api 容器里跑:

    docker compose exec -T -w /app -e PYTHONPATH=/app api python scripts/selfcheck_us_backtest.py

逐条断言美股回测没有沿用 A 股的口径(每一条都是加美股时查出来会静默出错的地方):
调仓日是纽交所交易日、基准是标普500、费率是美股预设(无印花税)、交易代码不带 .SZ、
成交额没有多乘 100、因子行标成 US。退出码非 0 = 有一条不对。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

from app.services.database import get_conn
from app.services.quant import backtest_engine as bt, market as mk, universe as uv

FAILS: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  ✅ " if ok else "  ❌ ") + what)
    if not ok:
        FAILS.append(what)


def main() -> int:
    codes = uv.covered_codes(mk.US)
    print(f"已下载美股 {len(codes)} 只")
    if len(codes) < 30:
        print("美股太少,先在数据页下载(或跑试跑脚本)")
        return 2
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT max(ts) FROM klines WHERE code=%s", (mk.US_BENCH,))
    end = cur.fetchone()[0]
    start = end - timedelta(days=365)
    cur.execute("SELECT ts FROM klines WHERE code=%s AND ts BETWEEN %s AND %s", (mk.US_BENCH, start, end))
    inx_days = {r[0] for r in cur.fetchall()}

    strat = {"factors": [{"key": "momentum_6m", "weight_pct": 50}, {"key": "vol_20d_inv", "weight_pct": 50}],
             "config": {"universe": "us_all", "top_n": 20, "rebalance": "M", "cost_bps": 5,
                        "slippage_model": "sqrt_impact"}}
    r = bt.run_backtest(strat, start, end)
    check("error" not in r, f"回测跑通 {r.get('error', '')} {r.get('message', '')}")
    if "error" in r:
        return 1
    sched = {date.fromisoformat(p["date"]) for p in r.get("nav_series", [])}
    check(bool(sched) and sched <= inx_days, f"调仓日全是标普500的交易日({len(sched)} 个)")
    check(not any(d.month == 10 and d.day in (1, 2, 3) and d.year == 2025 and d not in inx_days for d in sched),
          "没有混进 A 股日历")
    b = r.get("benchmark") or {}
    check(b.get("code") == mk.US_BENCH and b.get("name") == "标普 500", f"基准 = 标普500({b.get('code')})")
    trades = r.get("trades") or []
    check(bool(trades) and all(str(t.get("code", "")).endswith(".US") for t in trades),
          f"交易代码都是 .US 后缀({len(trades)} 笔,例 {trades[0].get('code') if trades else '-'})")
    check(all(float(t.get("stamp_tax") or 0) == 0 for t in trades), "没有印花税(美股费率)")
    q = r.get("quality") or {}
    check(q.get("survivorship_ok") is False and "幸存者偏差" in (q.get("note") or ""), "成色说明写了幸存者偏差")

    adv = bt._price_and_adv(["AAPL"], end).get("AAPL")
    if adv and adv[1]:
        check(3e9 < adv[1] < 1e12, f"AAPL 日均成交额 {adv[1] / 1e8:.0f} 亿美元,量级合理(没有多乘 100)")
    cur.execute("SELECT market, count(*) FROM factor_value WHERE code = ANY(%s) GROUP BY 1", (codes,))
    by = dict(cur.fetchall())
    check(by.get("US", 0) > 0 and not by.get("A"), f"美股因子行都标 US:{by}")
    conn.close()

    # 基准和股票池不同市场 → 不给基准并说明(不偷换成别的)
    strat2 = {**strat, "config": {**strat["config"], "benchmark": "000300"}}
    r2 = bt.run_backtest(strat2, start, end)
    check(not r2.get("benchmark") and "benchmark_note" in (r2.get("quality") or {}),
          "美股池选沪深300 → 不给基准并说明")
    print(f"\n{'全部通过' if not FAILS else f'{len(FAILS)} 条不对'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
