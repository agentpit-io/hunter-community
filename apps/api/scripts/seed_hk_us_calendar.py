"""把港美股交易日历种进 `fin_market_calendar` 并**对账**（拍板 §3.1 三步）。

来源（按优先顺序）：

1. **官方页结构化数据** —— HKEX `DataSource` JSON / NYSE `hours-calendars` 表
   （见 `app/services/fin/market_calendar.py`）；
2. 官方拿不到 → **人工种子兜底**（同模块 `MANUAL_SEED`，每条带来源 URL 与录入日期）；
3. **对账** —— 用行情通道（指数日线）回验过去 ≥20 个自然日，打印不一致的日期。

用法::

    DATABASE_URL=postgresql://hunter:hunter@127.0.0.1:5598/hunter \
      cd apps/api && PYTHONPATH=. python scripts/seed_hk_us_calendar.py            # 写入 + 对账
    DATABASE_URL=... python scripts/seed_hk_us_calendar.py --dry-run             # 只看不写
    DATABASE_URL=... python scripts/seed_hk_us_calendar.py --fix                 # 对账不一致时改表

`--fix` 的口径（拍板 §3.1 第 3 条「对账发现不一致 → 改表，不改结论」）：
只自动修「行情有数据、日历却说休市」那一类（明显是休市日录错）；
反向（日历说开市、行情没数据）可能是行情缺口，**只报告不自动改**，交人工复核。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

from app.services.fin import market_calendar as mcal  # noqa: E402

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://hunter:hunter@localhost:5432/hunter")

# 对账用的指数（其「有数据的日子」即该市场的真实交易日）：与 N0 报告同一口径。
INDEX_SYMBOL = {"HK": "hkHSI", "US": "us.INX"}
RECONCILE_DAYS = 32          # ≥ 拍板要求的 20 个自然日

_TENCENT_KLINE = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
                  "?param={sym},day,,,{n},qfq")


def _fetch_index_dates(symbol: str, n: int = 60, timeout: float = 20.0) -> list[str]:
    url = _TENCENT_KLINE.format(sym=urllib.parse.quote(symbol), n=n)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    rows = ((payload.get("data") or {}).get(symbol) or {}).get("day") or []
    return sorted(str(r[0])[:10] for r in rows if r)


def _window(holidays: dict[str, str], coverage: tuple[date, date]) -> tuple[date, date]:
    today = datetime.now(timezone.utc).date()
    start = date(today.year, 1, 1)
    end = min(coverage[1], date(today.year + 1, 12, 31))
    return start, end


def seed_market(cur, market: str, info: dict, *, dry_run: bool = False) -> dict:
    start, end = _window(info["holidays"], mcal.COVERAGE[market])
    sessions = mcal.MARKET_SESSIONS[market]
    cal_days = mcal.trading_days(start, end, info["holidays"])
    trading = set(cal_days)
    written = 0
    cur_date = start
    while cur_date <= end:
        iso = cur_date.isoformat()
        is_trading = iso in trading
        name = info["holidays"].get(iso)
        note = (f"{info['source_label']} · {name}" if (is_trading is False and name)
                else (f"{info['source_label']} · 交易日" if is_trading else "周末"))
        if not dry_run:
            cur.execute(
                """
                INSERT INTO fin_market_calendar
                  (market, trade_date, is_trading, sessions, note, calendar_source, fetched_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (market, trade_date) DO UPDATE SET
                  is_trading = EXCLUDED.is_trading, sessions = EXCLUDED.sessions,
                  note = EXCLUDED.note, calendar_source = EXCLUDED.calendar_source,
                  fetched_at = EXCLUDED.fetched_at
                """,
                (market, iso, is_trading,
                 psycopg2.extras.Json(sessions if is_trading else []),
                 note, info["source_label"], info["fetched_at"]),
            )
        written += 1
        cur_date += timedelta(days=1)
    return {"market": market, "start": start.isoformat(), "end": end.isoformat(),
            "rows": written, "trading_days": len(cal_days),
            "source_label": info["source_label"], "source": info["source"]}


def apply_fixes(cur, market: str, mismatches: dict) -> int:
    """只修「行情有数据、日历说休市」这一类（明显录错的休市日）。"""
    fixed = 0
    for iso in mismatches.get("bar_open_cal_closed", []):
        cur.execute(
            "UPDATE fin_market_calendar SET is_trading = true, sessions = %s, "
            "note = COALESCE(note,'') || ' · 对账修正为交易日', calendar_source = 'reconcile' "
            "WHERE market = %s AND trade_date = %s AND is_trading = false",
            (psycopg2.extras.Json(mcal.MARKET_SESSIONS[market]), market, iso),
        )
        fixed += cur.rowcount
    return fixed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="只算不写")
    ap.add_argument("--fix", action="store_true", help="对账不一致时改表（只修误标休市）")
    args = ap.parse_args()

    today = datetime.now(timezone.utc).date()
    recon_start = today - timedelta(days=RECONCILE_DAYS)
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    report = {"at": datetime.now(timezone.utc).isoformat(), "markets": {}}
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for market in ("HK", "US"):
                info = mcal.market_holidays(market)
                seeded = seed_market(cur, market, info, dry_run=args.dry_run)
                # 对账：日历说法 vs 指数日线实际有数据的日子
                try:
                    bar_dates = _fetch_index_dates(INDEX_SYMBOL[market])
                    cal_days = mcal.trading_days(recon_start, today, info["holidays"])
                    rec = mcal.reconcile(recon_start, today, cal_days, bar_dates)
                except Exception as exc:  # noqa: BLE001 —— 对账拿不到就如实写「未能完成」
                    rec = {"error": str(exc), "window": [recon_start.isoformat(), today.isoformat()]}
                if args.fix and not args.dry_run and not rec.get("error"):
                    rec["fixed"] = apply_fixes(cur, market, rec)
                seeded["reconcile"] = rec
                # 报告用：该市场 2026 年剩余月份的休市日清单
                seeded["holidays_2026"] = {d: n for d, n in sorted(info["holidays"].items())
                                           if d.startswith("2026") and date.fromisoformat(d) >= today}
                report["markets"][market] = seeded
            if args.dry_run:
                conn.rollback()
            else:
                conn.commit()
    finally:
        conn.close()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    # 退出码：对账有不一致且没修 → 1（让调用方看得见）
    bad = any((m.get("reconcile") or {}).get("mismatches")
              for m in report["markets"].values())
    if bad and not args.fix:
        print("\n⚠️ 对账存在不一致（见上面 mismatches）—— 用 --fix 改表，或人工复核", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
