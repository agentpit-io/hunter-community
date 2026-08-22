"""基本面因子回填 —— 不需要 key,但很慢。

这 10 个因子(PE/PB/ROE/毛利率/营收同比…)走 AKShare 直连。和技术因子
一样不需要任何凭据,区别只是 AKShare 对财务接口有限流:300 只跑一个日期
是分钟级,补一年是小时级。

所以它不在每日流水线里,而是:
  · 每周六 02:00 由 scheduler.weekly_akshare_factors() 算**当天**
  · 历史用这个脚本补

用法:
  docker compose exec -T api python3 /app/scripts/backfill_akshare_factors.py [月数] [因子...]

  月数    默认 12
  因子    不传就是 factor_engine.AKSHARE_ONLY 全部

慢是常态,建议放后台跑并把输出重定向到文件。
"""
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, "/app")

from app.services.quant import backtest_engine as bt          # noqa: E402
from app.services.quant import factor_engine as fe            # noqa: E402
from app.services.quant import universe as uv                 # noqa: E402


def main() -> int:
    months = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    keys = sys.argv[2:] or fe.AKSHARE_ONLY
    bad = [k for k in keys if k not in fe.AKSHARE_ONLY]
    if bad:
        print(f"[!] 这些不是 AKShare 因子:{bad}")
        print(f"    可选:{fe.AKSHARE_ONLY}")
        return 1

    end = date.today()
    start = end - timedelta(days=months * 31)
    codes = uv.query_current("000300")
    if len(codes) < 100:
        print(f"[!] 股票池只有 {len(codes)} 只 · 先 seed 成分股")
        return 1

    # 基本面数据按季度变,**按月取点就够了** —— 按周补是白等,
    # 财报没变的日期上算出来的是同一个值,只是多花几倍时间
    days = bt._rebalance_dates(start, end, "M")
    if not days:
        print("[!] 这个区间 klines 里没有交易日")
        return 1

    print(f"{len(codes)} 只 · {len(days)} 个月度节点 ({days[0]} ~ {days[-1]}) "
          f"· {len(keys)} 个因子")
    print("AKShare 有限流,一个因子一个日期就要几分钟 —— 预计很久\n")

    t0 = time.time()
    total = 0
    for i, d in enumerate(days, 1):
        for k in keys:
            ts = time.time()
            try:
                n = fe.compute_and_store(k, codes, d)
            except Exception as e:                            # noqa: BLE001
                print(f"  [{i}/{len(days)}] {d} {k} 失败: "
                      f"{type(e).__name__} {str(e)[:60]}")
                n = 0
            total += n
            print(f"  [{i}/{len(days)}] {d} {k:22} {n:4} 行 · {time.time()-ts:.0f}s")
    print(f"\n完成 · 共 {total} 行 · 耗时 {(time.time()-t0)/60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
