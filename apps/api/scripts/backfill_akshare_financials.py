"""Phase B B1 · AKShare 财务因子回填 · 补 12 期(每月 15 号)pe_inv + roe 真值
(2026-08-17 · 替代 Phase A mock)

用法:
  docker exec -e PYTHONPATH=/app -w /app hunter-community-api-1 \\
    python3 scripts/backfill_akshare_financials.py

耗时:29 只 × 12 期 × 2 因子 · 每次 akshare 网络 1-2s · 约 8-12 分钟
"""
from datetime import date
from app.services.database import get_conn
from app.services.quant import factor_engine

STOCKS = [
    '600519','000651','000858','600036','300750','601318','000333','002594',
    '600887','600900','600276','601899','688981','300760','600030','601166',
    '000725','600809','002415','600690','601288','601398','600009','600585',
    '601668','600028','601857','600016','601088',
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


def backfill():
    dates = get_month_ends(12)
    print(f"[akshare-backfill] {len(dates)} 期:{dates[0]} → {dates[-1]}")
    for d in dates:
        for key in ('pe_inv', 'roe'):
            n = factor_engine.compute_and_store(key, STOCKS, d)
            print(f"  {d} · {key}: {n} rows")
    print("\n✅ AKShare 真财务因子回填完成")


if __name__ == "__main__":
    backfill()
