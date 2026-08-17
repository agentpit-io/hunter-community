"""C1 · 3 新因子回填 · dividend_yield / kronos / main_flow

因子特性差异:
- dividend_yield · 分红历史完整 · 可回填 12 期(每月一天)
- main_flow · 走 finance-data · 覆盖范围有限(watchlist 外返 None)
- kronos · T-0 预测 · 无法回填历史 · 只写"今日"一行

用法:
  docker exec -e PYTHONPATH=/app -w /app hunter-community-api-1 \\
    python3 scripts/backfill_c1_factors.py [dividend|main_flow|kronos|all]

耗时:
- dividend 12 期 × 29 只 · 每 code 一次 fetch · ~3-5 分钟
- main_flow 12 期 · finance-data 快 · ~1 分钟
- kronos 只今日 1 期 · sem=3 · ~2-3 分钟(GPU 排队)
"""
from datetime import date
from app.services.quant import factor_engine
from app.services.quant.factor_engine import _fetch_klines_close as _fetch  # 借用

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


def backfill_dividend():
    """dividend_yield · 12 期"""
    print("[c1-dividend] 12 期 × 29 只")
    total = 0
    for d in get_month_ends(12):
        n = factor_engine.compute_and_store('dividend_yield', STOCKS, d)
        print(f"  {d}: {n} rows")
        total += n
    print(f"✅ dividend_yield 完成 · 共 {total} 行")


def backfill_main_flow():
    """main_flow · 12 期(finance-data 数据窗口有限 · 部分期可能全 None)"""
    print("[c1-flow] 12 期 × 29 只")
    total = 0
    for d in get_month_ends(12):
        n = factor_engine.compute_and_store('main_flow', STOCKS, d)
        print(f"  {d}: {n} rows")
        total += n
    print(f"✅ main_flow 完成 · 共 {total} 行")


def backfill_kronos_today():
    """kronos · T-0 预测 · 只写今日一行 · 历史期无值(APScheduler 每日累积)"""
    print("[c1-kronos] 只写今日 · Kronos 是 T-0 预测")
    n = factor_engine.compute_and_store('kronos', STOCKS, date.today())
    print(f"  {date.today()}: {n} rows")
    print(f"✅ kronos 完成 · 共 {n} 行")


if __name__ == "__main__":
    import sys
    kind = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if kind in ('dividend', 'all'):
        backfill_dividend()
    if kind in ('main_flow', 'flow', 'all'):
        backfill_main_flow()
    if kind in ('kronos', 'all'):
        backfill_kronos_today()
