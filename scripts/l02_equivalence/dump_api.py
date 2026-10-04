"""L02 前后对照 · api 侧：把「同一事实」各来源**读出来的值**存成 JSON。

改前改后各跑一次，`diff` 必须为空（`L02.md` §1.1 行为等价硬要求）。

```bash
cd apps/api
.venv/bin/python ../../scripts/l02_equivalence/dump_api.py --side before > /tmp/l02/before_api.json
# …改代码…
.venv/bin/python ../../scripts/l02_equivalence/dump_api.py --side after  > /tmp/l02/after_api.json
diff /tmp/l02/before_api.json /tmp/l02/after_api.json && echo IDENTICAL
```

`--side before` 从**旧符号**（各模块里手写的 `CST` / `SHANGHAI` / `_CST` / `_SH`）取值；
`--side after` 从**新的单一来源**（`app.services.market_time`）取值。
两侧 key 逐字相同 ⇒ 直接 `diff`。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "apps", "api")))

# 对照时刻：两段夏令时 + 春/秋切换前后（美东 DST 边界）。
_GRID = [
    "2026-01-15T00:00:00+00:00",
    "2026-03-06T20:00:00+00:00",
    "2026-03-08T06:59:00+00:00",   # 美东 01:59 EST（切换前）
    "2026-03-08T07:01:00+00:00",   # 美东 03:01 EDT（切换后）
    "2026-06-15T12:00:00+00:00",
    "2026-09-30T04:00:00+00:00",
    "2026-10-31T18:00:00+00:00",
    "2026-11-01T05:00:00+00:00",   # 美东 01:00 EDT（回拨前）
    "2026-11-01T06:00:00+00:00",   # 美东 01:00 EST（回拨后）
    "2026-11-15T12:00:00+00:00",
    "2026-12-31T16:00:00+00:00",
]

_MARKET_TZ_NAME = {"CN_A": "Asia/Shanghai", "HK": "Asia/Hong_Kong",
                   "US": "America/New_York"}

# 两侧共用的 site 键（before 从旧符号取，after 从 market_time 取）
_SITE_KEYS = (
    "fin_data", "fin_data.us", "collector", "signal_monitor", "gm_alert_checker",
    "kpred_html_renderer", "report_composer", "screen_quota", "backtest_jobs",
    "backtest_scheduler", "klines_etl", "fin_dashboard_svc", "fin_memory",
    "fin_regime", "fin_report", "fin_review", "router_chat_debate",
    "router_chat_kpred", "router_fin_dashboard", "router_online_analysis",
    "router_quote", "agents_graph",
)

# 本地时刻边界（盘中 / 午休 / 盘后 / 收市竞价 / 边界点）
_BOUNDS = {
    "CN_A": ["09:29", "09:30", "11:30", "11:31", "13:00", "15:00", "15:01"],
    "HK": ["09:29", "09:30", "12:00", "12:01", "13:00", "15:59",
           "16:00", "16:05", "16:10", "16:11"],
    "US": ["09:29", "09:30", "16:00", "16:01"],
}


def _instants() -> list[datetime]:
    out = [datetime.fromisoformat(s) for s in _GRID]
    for mk, times in _BOUNDS.items():
        tz = ZoneInfo(_MARKET_TZ_NAME[mk])
        for day in ("2026-01-15", "2026-07-15"):
            for t in times:
                out.append(datetime.fromisoformat(f"{day}T{t}:00").replace(tzinfo=tz))
    return out


def _offsets(tz) -> dict:
    return {at.isoformat(): [int(tz.utcoffset(at).total_seconds()),
                             at.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")]
            for at in _instants()}


def _sites(side: str) -> dict:
    """site_label → tzinfo。两侧 key 相同。"""
    return _sites_before() if side == "before" else _sites_after()


def _sites_before() -> dict:
    if True:  # 缩进块，保持 from-import 局部
        from app.services import collector, fin_data, signal_monitor
        from app.services.backtest import jobs as bt_jobs, scheduler as bt_sched
        from app.services.chat_debate import kpred_html_renderer, report_composer
        from app.services.data import klines_etl
        from app.services.fin import dashboard, memory, regime, report, review
        from app.services.gm import alert_checker
        from app.services.quant import screen_quota
        from app.routers import chat_debate as cd, chat_kpred as ck, fin_dashboard as fd
        from app.routers import online_analysis as oa, quote
        import agents.graph as agraph

        return {
            "fin_data": fin_data.CST,
            "fin_data.us": fin_data._market_tz("us"),
            "collector": collector.CST,
            "signal_monitor": signal_monitor.CST,
            "gm_alert_checker": alert_checker.CST,
            "kpred_html_renderer": kpred_html_renderer._CST,
            "report_composer": report_composer._CST,
            "screen_quota": screen_quota._SH,
            "backtest_jobs": bt_jobs.CST,
            "backtest_scheduler": bt_sched.CST,
            "klines_etl": klines_etl.CST,
            "fin_dashboard_svc": dashboard.SHANGHAI,
            "fin_memory": memory.SHANGHAI,
            "fin_regime": regime.SHANGHAI,
            "fin_report": report.SHANGHAI,
            "fin_review": review.SHANGHAI,
            "router_chat_debate": cd._CST,
            "router_chat_kpred": ck._CST,
            "router_fin_dashboard": fd.SHANGHAI,
            "router_online_analysis": oa._CST,
            "router_quote": quote.CST,
            "agents_graph": agraph._CST,
        }


def _sites_after() -> dict:
    from app.services import market_time

    one = market_time.market_tz("CN_A")
    assert set(_SITE_KEYS) == set(_sites_before().keys()), "site 键两侧不一致"
    return {k: (market_time.market_tz("US") if k == "fin_data.us" else one)
            for k in _SITE_KEYS}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--side", choices=("before", "after"), required=True)
    args = ap.parse_args()

    from app.services.fin import market_calendar, markets

    out = {
        "side": args.side,
        "tz_sites": {k: _offsets(v) for k, v in _sites(args.side).items()},
        "market_tz_map": dict(markets.MARKET_TZ),
        "sessions": {m: [dict(s) for s in v]
                     for m, v in market_calendar.MARKET_SESSIONS.items()},
        # 消费者行为：时段 → 状态判定（api 侧市场卡片口径，只看 hour/minute）
        "state_from_sessions": {
            m: {t: markets._state_from_sessions(
                    market_calendar.MARKET_SESSIONS[m],
                    datetime(2026, 6, 15, int(t[:2]), int(t[3:])))
                for t in _BOUNDS[m]}
            for m in market_calendar.MARKET_SESSIONS
        },
    }
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
