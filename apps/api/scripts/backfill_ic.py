"""D-2 · IC 一次性回填 · 全启用因子 × [5,10,20] horizon × 近 N 期

用法:
  # community docker
  docker exec -e PYTHONPATH=/app -w /app hunter-community-api-1 \\
    python3 scripts/backfill_ic.py [months_back=6]

  # hermes prod
  ./venv/bin/python3 scripts/backfill_ic.py 6

耗时:
- 19 因子 × 3 horizon × 12 期 · 每期一次 Spearman(全 py 无 scipy) · ~2-3 分钟
- 需要 factor_value 有数据 · 且 klines 有未来 horizon+5 天 close

单期 IC 计算逻辑:
- 取该期 factor_value.z_score(近 45 天可用的最新一条)
- 取每只票从 trade_date 起未来 horizon 交易日 close · 算累计收益
- Spearman(z_score, forward_return)
"""
import sys
from datetime import date
from app.services.quant import ic_engine


def get_month_ends(months_back: int = 6) -> list[date]:
    today = date.today()
    result = []
    for i in range(months_back, 0, -1):
        y = today.year
        m = today.month - i
        while m <= 0:
            m += 12; y -= 1
        result.append(date(y, m, 15))
    return result


def main(months_back: int = 6):
    dates = get_month_ends(months_back)
    horizons = [5, 10, 20]
    print(f"[backfill_ic] {len(dates)} 期 × 19 因子 × {len(horizons)} horizon")
    total = 0
    for d in dates:
        result = ic_engine.compute_daily(d, "hs300", horizons)
        n = sum(result.values())
        print(f"  {d}: {n} rows")
        total += n
    print(f"\n✅ IC 回填完成 · 共 {total} 行 · 前端 factors.html /factors/ic-ranking 应有数据")


if __name__ == "__main__":
    months = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    main(months)
