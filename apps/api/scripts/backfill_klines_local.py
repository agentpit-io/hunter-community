"""个股日线回填 —— **不需要任何 key**。

原来的两个回填脚本都走 `finance_data_client`,而它默认指向
`hunter.agentpit.io/api/saas/data`,要我们的 HUNTER_API_KEY:

    backfill_klines_from_hunter_api.py   30 只   走我们的网关
    backfill_hs300_full.py               300 只  同上

这个脚本走 `local_kline`:用户在数据源页配的源优先,没配就腾讯直连。
两条路都不经过我们的服务。

用法:
  docker compose exec -T api python3 /app/scripts/backfill_klines_local.py [月数] [user_id]

  月数     默认 40(约三年多 · 腾讯一次就能给完)
  user_id  可选 · 传了就优先用这个用户配的 kline 源
"""
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, "/app")

from app.services.database import get_conn                    # noqa: E402
from app.services.quant import local_kline, universe as uv    # noqa: E402


def main() -> int:
    months = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    user_id = sys.argv[2] if len(sys.argv) > 2 else None
    end = date.today()
    start = end - timedelta(days=months * 31)

    codes = uv.covered_codes()
    if len(codes) < 100:
        print(f"[!] 股票池只有 {len(codes)} 只 · 先 seed 成分股")
        return 1

    print(f"{len(codes)} 只 · {start} ~ {end} · "
          f"{'用户源优先(' + user_id[:8] + '…)' if user_id else '腾讯直连'}")

    ok = fail = written = 0
    t0 = time.time()
    for i, code in enumerate(codes, 1):
        rows = local_kline.fetch_daily(code, start, end, user_id)
        if not rows:
            # **拿不到就是拿不到** —— 不写空、不补零。回测那边靠
            # "这只票没有价格"来跳过它,而补一行假价格会让收益凭空出现
            fail += 1
            print(f"  [{i}/{len(codes)}] {code} 拿不到数据")
            continue
        conn = get_conn(); cur = conn.cursor()
        try:
            for r in rows:
                cur.execute(
                    """INSERT INTO klines (code, period, ts, open, high, low, close, volume)
                       VALUES (%s,'daily',%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (code, period, ts) DO UPDATE
                         SET open=EXCLUDED.open, high=EXCLUDED.high,
                             low=EXCLUDED.low, close=EXCLUDED.close,
                             volume=EXCLUDED.volume""",
                    (code, r["ts"], r["open"], r["high"], r["low"],
                     r["close"], int(r["volume"] or 0)))
                written += cur.rowcount
            conn.commit(); ok += 1
        except Exception as e:                                # noqa: BLE001
            conn.rollback(); fail += 1
            print(f"  [{i}/{len(codes)}] {code} 入库失败: {type(e).__name__} {str(e)[:60]}")
        finally:
            cur.close(); conn.close()
        if i % 50 == 0:
            print(f"  [{i}/{len(codes)}] 已成功 {ok} · 失败 {fail} · {time.time()-t0:.0f}s")
        time.sleep(0.15)          # 对上游客气一点

    print(f"\n完成 · 成功 {ok} · 失败 {fail} · 写入 {written} 行 · {time.time()-t0:.0f}s")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
