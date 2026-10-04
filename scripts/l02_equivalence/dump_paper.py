"""L02 前后对照 · paper 侧（时段判定的消费者行为）。

```bash
cd apps/paper
.venv/bin/python ../../scripts/l02_equivalence/dump_paper.py > /tmp/l02/before_paper.json
```
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "apps", "paper")))

# 消费者行为用「每个市场的常设时段」跑边界判定（时段值来自各服务）——
# 这里直接写死与 `fin_market_rule` 种子等价的三组，**只为对照判定行为**，
# 不是第二份来源（该文件只是测试脚手架，不进任何服务的运行路径）。
_SESSIONS = {
    "CN_A": [{"open": "09:30", "close": "11:30"}, {"open": "13:00", "close": "15:00"}],
    "HK": [{"open": "09:30", "close": "12:00"}, {"open": "13:00", "close": "16:00"},
           {"open": "16:00", "close": "16:10"}],
    "US": [{"open": "09:30", "close": "16:00"}],
}
_TZ = {"CN_A": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}
_BOUNDS = {
    "CN_A": ["09:29", "09:30", "11:30", "11:31", "13:00", "15:00", "15:01"],
    "HK": ["09:29", "09:30", "12:00", "12:01", "13:00", "15:59",
           "16:00", "16:05", "16:10", "16:11"],
    "US": ["09:29", "09:30", "16:00", "16:01"],
}


def main() -> None:
    from app.market_time import MARKET_TZ, market_tz
    from app.risk import session

    out = {
        "market_tz_map": dict(MARKET_TZ),
        "tz_sites": {f"market_time.{m}": {
            at.isoformat(): [int(market_tz(m).utcoffset(at).total_seconds()),
                             at.astimezone(market_tz(m)).strftime("%Y-%m-%d %H:%M:%S")]
            for at in (datetime.fromisoformat(s) for s in
                       ("2026-01-15T00:00:00+00:00", "2026-06-15T12:00:00+00:00"))}
            for m in ("CN_A", "HK", "US")},
        "resolve_sessions": {
            "cal_empty_falls_back": session.resolve_sessions({}, _SESSIONS["HK"]),
            "cal_wins": session.resolve_sessions({"sessions": _SESSIONS["US"]},
                                                 _SESSIONS["HK"]),
        },
        "in_any_session": {
            m: {t: session.in_any_session(
                    _SESSIONS[m],
                    datetime(2026, 6, 15, int(t[:2]), int(t[3:])).replace(
                        tzinfo=ZoneInfo(_TZ[m])),
                    m)
                for t in _BOUNDS[m]}
            for m in _SESSIONS
        },
    }
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
