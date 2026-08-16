"""Phase A · MOCK 数据回填(纯演示用 · 生产严禁跑)
(2026-08-17 · quant-strategy-tech-plan.md Phase A 尾巴)

背景:hunter-community 本地无 finance-data 数据采集 · klines 表空
为让 MVP 端到端可演示 · 生成 mock K 线 + factor_value

使用:
  docker exec hunter-community-api-1 python3 /app/scripts/mock_backfill_for_quant_demo.py
"""
import random
from datetime import date, timedelta
from app.services.database import get_conn

STOCKS = [
    ('600519', '贵州茅台', 1350.0), ('000651', '格力电器', 40.0),
    ('000858', '五粮液', 150.0), ('600036', '招商银行', 35.0),
    ('300750', '宁德时代', 195.0), ('601318', '中国平安', 45.0),
    ('000333', '美的集团', 72.0), ('002594', '比亚迪', 240.0),
    ('600887', '伊利股份', 28.0), ('600900', '长江电力', 26.0),
    ('600276', '恒瑞医药', 45.0), ('601899', '紫金矿业', 32.0),
    ('688981', '中芯国际', 105.0), ('300760', '迈瑞医疗', 270.0),
    ('600030', '中信证券', 22.0), ('601166', '兴业银行', 18.0),
    ('000725', '京东方 A', 4.5), ('600809', '山西汾酒', 190.0),
    ('002415', '海康威视', 35.0), ('600690', '海尔智家', 27.0),
    ('601288', '农业银行', 5.5), ('601398', '工商银行', 6.5),
    ('600009', '上海机场', 38.0), ('600585', '海螺水泥', 22.0),
    ('601668', '中国建筑', 6.0), ('600028', '中国石化', 6.8),
    ('601857', '中国石油', 10.5), ('600837', '海通证券', 9.5),
    ('600016', '民生银行', 4.2), ('601088', '中国神华', 40.0),
]


def gen_kline_series(base_price: float, days: int, seed: int):
    rnd = random.Random(seed)
    price = base_price
    series = []
    today = date.today()
    for i in range(days, 0, -1):
        ts = today - timedelta(days=i)
        if ts.weekday() >= 5:
            continue
        price *= (1 + rnd.gauss(0, 0.018))
        o = price * (1 + rnd.gauss(0, 0.005))
        h = max(o, price) * (1 + abs(rnd.gauss(0, 0.008)))
        l = min(o, price) * (1 - abs(rnd.gauss(0, 0.008)))
        c = price
        v = int(rnd.uniform(100_000, 500_000_000))
        series.append((ts, round(o, 3), round(h, 3), round(l, 3), round(c, 3), v))
    return series


def seed_klines():
    conn = get_conn(); cur = conn.cursor()
    total = 0
    for code, name, base_price in STOCKS:
        cur.execute(
            """INSERT INTO stocks (code, name, market, exchange, asset_type, enabled, user_id)
               VALUES (%s, %s, 'A', %s, 'stock', TRUE, '')
               ON CONFLICT (code, user_id) DO NOTHING""",
            (code, name, 'SH' if code.startswith('6') else 'SZ'))
        series = gen_kline_series(base_price, 300, seed=hash(code) & 0xFFFF)
        for ts, o, h, l, c, v in series:
            cur.execute(
                """INSERT INTO klines (code, period, ts, open, high, low, close, volume)
                   VALUES (%s, 'daily', %s, %s, %s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (code, ts, o, h, l, c, v))
        total += len(series)
    conn.commit(); cur.close(); conn.close()
    print(f"[mock] seeded {len(STOCKS)} stocks · {total} klines")


def seed_factor_values():
    from app.services.quant import factor_engine
    codes = [s[0] for s in STOCKS]
    today = date.today()
    print("[factor] computing momentum_12m_1m from mock klines...")
    n = factor_engine.compute_and_store("momentum_12m_1m", codes, today)
    print(f"[factor] momentum_12m_1m · {n} rows")

    conn = get_conn(); cur = conn.cursor()
    for factor_key, base in [("pe_inv", 0.04), ("roe", 0.15)]:
        raw = {}
        for code, _, _ in STOCKS:
            rnd = random.Random(hash((code, factor_key)) & 0xFFFF)
            raw[code] = base * (1 + rnd.gauss(0, 0.4))
        z, rank = factor_engine._winsorize_zscore(raw)
        rows = [(today, factor_key, c, "A", raw[c], z.get(c), rank.get(c)) for c in raw]
        cur.executemany(
            """INSERT INTO factor_value (trade_date, factor_key, code, market, raw_value, z_score, pct_rank)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (trade_date, factor_key, code) DO UPDATE
                 SET raw_value = EXCLUDED.raw_value, z_score = EXCLUDED.z_score,
                     pct_rank = EXCLUDED.pct_rank, updated_at = NOW()""",
            rows)
        print(f"[factor] {factor_key} · {len(rows)} rows (mock)")
    conn.commit(); cur.close(); conn.close()


if __name__ == "__main__":
    seed_klines()
    seed_factor_values()
    print("\n✅ MOCK 回填完成")
