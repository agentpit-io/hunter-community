"""Phase A 尾 · 每月一期 factor_value 回填 · 让回测能选出股票
(2026-08-17 · momentum 每期真算 · pe/roe 稳定 mock)
"""
import random
from datetime import date, timedelta
from app.services.database import get_conn
from app.services.quant import factor_engine

STOCKS = [
    '600519','000651','000858','600036','300750','601318','000333','002594',
    '600887','600900','600276','601899','688981','300760','600030','601166',
    '000725','600809','002415','600690','601288','601398','600009','600585',
    '601668','600028','601857','600016','601088',
]


def get_month_ends(months_back: int = 12) -> list[date]:
    """最近 N 个月的月末交易日(用第 15 日近似 · Phase A 简)"""
    today = date.today()
    result = []
    for i in range(months_back, 0, -1):
        y = today.year
        m = today.month - i
        while m <= 0:
            m += 12; y -= 1
        result.append(date(y, m, 15))
    return result


def seed_mock_pe_roe(dates: list[date]):
    conn = get_conn(); cur = conn.cursor()
    for factor_key, base in [("pe_inv", 0.04), ("roe", 0.15)]:
        for d in dates:
            raw = {}
            for code in STOCKS:
                # 加 date factor · 让 mock 每期略变(模拟财务季度变化)
                rnd = random.Random(hash((code, factor_key, d.month // 3)) & 0xFFFF)
                raw[code] = base * (1 + rnd.gauss(0, 0.4))
            z, rank = factor_engine._winsorize_zscore(raw)
            rows = [(d, factor_key, c, "A", raw[c], z.get(c), rank.get(c)) for c in raw]
            cur.executemany(
                """INSERT INTO factor_value (trade_date, factor_key, code, market, raw_value, z_score, pct_rank)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (trade_date, factor_key, code) DO UPDATE
                     SET raw_value = EXCLUDED.raw_value, z_score = EXCLUDED.z_score,
                         pct_rank = EXCLUDED.pct_rank, updated_at = NOW()""",
                rows)
        print(f"[mock] {factor_key} · {len(dates)} dates · {len(dates)*len(STOCKS)} rows")
    conn.commit(); cur.close(); conn.close()


def compute_momentum_history(dates: list[date]):
    """每个日期都用真 K 线算 momentum"""
    for d in dates:
        n = factor_engine.compute_and_store("momentum_12m_1m", STOCKS, d)
        print(f"[factor] momentum @ {d} · {n} rows")


if __name__ == "__main__":
    dates = get_month_ends(12)
    print(f"[backfill] {len(dates)} 期:{dates[0]} → {dates[-1]}")
    seed_mock_pe_roe(dates)
    compute_momentum_history(dates)
    print("\n✅ 12 期 factor 回填完成 · 回测有历史数据可选")
