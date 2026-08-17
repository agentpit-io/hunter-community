"""E-4 · hs300 全 300 只成分股 · K 线 + 财务 + 因子 一次性 backfill

前置:
- sql/20260818_index_component.sql 已执行(建表)
- index_component 表已 seed(seed_current)· 若未 seed 会自动 seed

用法(生产):
  cd /opt/hermes-api
  export PYTHONPATH=/opt/hermes-api
  set -a; source /opt/hermes-repo/api/.env; set +a
  nohup ./venv/bin/python3 scripts/backfill_hs300_full.py [kline|factor|all] > /tmp/e4-full.log 2>&1 &

耗时:
- kline · 300 只 × 500 天 · 30-50 min
- factor · 300 只 × 12 期 × 20 因子 · 60-120 min(AKShare 限流)
- all · 3-4 h
"""
import sys
from datetime import date
from app.services.quant import universe as _uv
from app.services.database import get_conn


def ensure_seeded():
    codes = _uv.query_current("000300")
    if len(codes) < 100:
        print(f"[hs300] 未 seed(current={len(codes)}) · 从 AKShare 拉")
        n = _uv.seed_current("000300")
        print(f"[hs300] seeded {n} 只")
    else:
        print(f"[hs300] 已 seed · {len(codes)} 只")
    return _uv.query_current("000300")


def backfill_klines(codes):
    from app.services import finance_data_client as fd
    ok, fail = 0, 0
    for i, code in enumerate(codes):
        conn = get_conn()   # 每 code 独立 conn · 避免事务污染
        cur = conn.cursor()
        try:
            cur.execute(
                """INSERT INTO stocks (code, name, market, exchange, asset_type, enabled, user_id)
                   VALUES (%s, %s, 'A', %s, 'stock', TRUE, '')
                   ON CONFLICT (code, user_id) DO NOTHING""",
                (code, code, "SH" if code.startswith(("6", "9")) else "SZ"),
            )
            kl = fd.get_kline(code, "daily", 500)
            if not kl:
                fail += 1
                cur.close(); conn.close()
                continue
            for k in kl:
                cur.execute(
                    """INSERT INTO klines (code, period, ts, open, high, low, close, volume)
                       VALUES (%s, 'daily', %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (code, period, ts) DO UPDATE
                         SET close = EXCLUDED.close""",
                    (code, k["ts"], k.get("open"), k.get("high"), k.get("low"), k.get("close"), k.get("volume")),
                )
            conn.commit()
            ok += 1
            if (i + 1) % 30 == 0:
                print(f"  [{i+1}/{len(codes)}] {ok} ok · {fail} fail", flush=True)
        except Exception as e:
            print(f"  [{code}] {type(e).__name__}: {str(e)[:120]}", flush=True)
            try: conn.rollback()
            except Exception: pass
            fail += 1
        finally:
            try: cur.close(); conn.close()
            except Exception: pass
    print(f"\n✅ K 线完成 · {ok} 成功 · {fail} 失败")


def backfill_factors(codes):
    """12 期 × 全因子"""
    from app.services.quant import factor_engine, factor_defs
    from datetime import timedelta

    today = date.today()
    dates = []
    for i in range(12, 0, -1):
        y = today.year; m = today.month - i
        while m <= 0: m += 12; y -= 1
        dates.append(date(y, m, 15))

    for fd_obj in factor_defs.enabled_factors():
        key = fd_obj.key
        # kronos 只今日
        if key == "kronos":
            n = factor_engine.compute_and_store(key, codes, today)
            print(f"  kronos 今日: {n} rows")
            continue
        total = 0
        for d in dates:
            n = factor_engine.compute_and_store(key, codes, d)
            total += n
        print(f"  {key}: {total} rows(12 期)")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    codes = ensure_seeded()
    print(f"[hs300] 目标 {len(codes)} 只 · mode={mode}")
    if mode in ("kline", "all"):
        backfill_klines(codes)
    if mode in ("factor", "all"):
        backfill_factors(codes)
    print("[hs300] 全部完成")
