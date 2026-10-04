"""L02 前后对照 · fin-worker 侧（时段唯一来源改造）。

```bash
cd apps/fin-worker
.venv/bin/python ../../scripts/l02_equivalence/dump_finworker.py > /tmp/l02/before_fw.json
```
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "apps", "fin-worker")))

_GRID = ["2026-01-15T00:00:00+00:00", "2026-06-15T12:00:00+00:00",
         "2026-03-08T06:59:00+00:00", "2026-03-08T07:01:00+00:00",
         "2026-11-01T05:00:00+00:00", "2026-11-01T06:00:00+00:00",
         "2026-12-31T16:00:00+00:00"]


def _offsets(tz) -> dict:
    return {at.isoformat(): [int(tz.utcoffset(at).total_seconds()),
                             at.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")]
            for at in (datetime.fromisoformat(s) for s in _GRID)}


def main() -> None:
    from app import activities, schedules
    from app.market_time import MARKET_TZ, market_tz

    rules = schedules.fallback_market_rules()
    out = {
        "market_tz_map": dict(MARKET_TZ),
        "default_sessions": {m: [dict(s) for s in v]
                             for m, v in activities.DEFAULT_SESSIONS.items()},
        "a_sessions": [dict(s) for s in activities.A_SESSIONS],
        "fallback_market_rules": [
            {"market": r["market"], "timezone": r["timezone"],
             "points": list(r["points"]),
             "sessions": [dict(s) for s in r["sessions"]]}
            for r in rules],
        # 消费者行为：时点 Schedule 的 cron（按时段/时点生成，改时段会影响它）
        "point_specs": [
            {"id": s.schedule_id, "cron": s.cron}
            for s in schedules.point_specs(rules)],
        "tz_sites": {
            "activities.SHANGHAI": _offsets(activities.SHANGHAI),
            "market_time.CN_A": _offsets(market_tz("CN_A")),
            "market_time.HK": _offsets(market_tz("HK")),
            "market_time.US": _offsets(market_tz("US")),
        },
    }
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
