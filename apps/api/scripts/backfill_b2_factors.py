"""B2 · 13 新因子 12 期回填 · 用 compute_and_store 走真数据

因子(13):
- K 线 7:momentum_1m/6m · ma_align · macd · rsi · vol_20d_inv · candle_5d
- 财务 6:pb_inv · roa · gross_margin · debt_ratio_inv · revenue_growth_yoy · earnings_growth_yoy

用法:
  docker exec -e PYTHONPATH=/app -w /app hunter-community-api-1 \\
    python3 scripts/backfill_b2_factors.py

耗时:
- K 线 7:12 期 × 7 因子 · 每次 <1s · ~2 分钟
- 财务 6:29 只 × 12 期 × 6 因子 · akshare 有 LRU 缓存 · ~15 分钟
"""
from datetime import date
from app.services.quant import factor_engine

STOCKS = [
    '600519','000651','000858','600036','300750','601318','000333','002594',
    '600887','600900','600276','601899','688981','300760','600030','601166',
    '000725','600809','002415','600690','601288','601398','600009','600585',
    '601668','600028','601857','600016','601088',
]

KLINE_FACTORS = [
    'momentum_1m', 'momentum_6m', 'ma_align',
    'macd', 'rsi', 'vol_20d_inv', 'candle_5d',
]

FIN_FACTORS = [
    'pb_inv', 'roa', 'gross_margin',
    'debt_ratio_inv', 'revenue_growth_yoy', 'earnings_growth_yoy',
]


def get_month_ends(months_back: int = 12) -> list[date]:
    today = date.today()
    result = []
    for i in range(months_back, 0, -1):
        y = today.year
        m = today.month - i
        while m <= 0:
            m += 12; y -= 1
        result.append(date(y, m, 15))
    return result


def backfill(kind: str):
    factors = KLINE_FACTORS if kind == 'kline' else FIN_FACTORS
    dates = get_month_ends(12)
    print(f"[b2-{kind}] {len(dates)} 期 × {len(factors)} 因子")
    total = 0
    for key in factors:
        for d in dates:
            n = factor_engine.compute_and_store(key, STOCKS, d)
            print(f"  {d} · {key}: {n} rows")
            total += n
    print(f"\n✅ {kind} 完成 · 共 {total} 行")


if __name__ == "__main__":
    import sys
    kind = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if kind in ('kline', 'all'):
        backfill('kline')
    if kind in ('fin', 'all'):
        backfill('fin')
