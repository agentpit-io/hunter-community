"""从 hunter.agentpit.io API 拉真 K 线 · 落 klines 表
(2026-08-17 · Phase A · 替代 mock K 线)

前置:
- .env 里 HUNTER_API_KEY=hunt_tools_xxx 有效
- finance_data_client 默认路由 https://hunter.agentpit.io/api/saas/data

用法:
  docker exec -e PYTHONPATH=/app -w /app hunter-community-api-1 \\
    python3 scripts/backfill_klines_from_hunter_api.py
"""
from datetime import date
from app.services.database import get_conn
from app.services import finance_data_client as fd

STOCKS = [
    ('600519', '贵州茅台'), ('000651', '格力电器'), ('000858', '五粮液'),
    ('600036', '招商银行'), ('300750', '宁德时代'), ('601318', '中国平安'),
    ('000333', '美的集团'), ('002594', '比亚迪'), ('600887', '伊利股份'),
    ('600900', '长江电力'), ('600276', '恒瑞医药'), ('601899', '紫金矿业'),
    ('688981', '中芯国际'), ('300760', '迈瑞医疗'), ('600030', '中信证券'),
    ('601166', '兴业银行'), ('000725', '京东方 A'), ('600809', '山西汾酒'),
    ('002415', '海康威视'), ('600690', '海尔智家'), ('601288', '农业银行'),
    ('601398', '工商银行'), ('600009', '上海机场'), ('600585', '海螺水泥'),
    ('601668', '中国建筑'), ('600028', '中国石化'), ('601857', '中国石油'),
    ('600837', '海通证券'), ('600016', '民生银行'), ('601088', '中国神华'),
]


def backfill():
    conn = get_conn(); cur = conn.cursor()
    total_stocks = 0
    total_klines = 0
    failed = []

    for code, name in STOCKS:
        cur.execute(
            """INSERT INTO stocks (code, name, market, exchange, asset_type, enabled, user_id)
               VALUES (%s, %s, 'A', %s, 'stock', TRUE, '')
               ON CONFLICT (code, user_id) DO NOTHING""",
            (code, name, 'SH' if code.startswith('6') else 'SZ'))

        try:
            kl = fd.get_kline(code, 'daily', 300)
        except Exception as e:
            print(f"[{code}] err · {e}")
            failed.append(code)
            continue

        if not kl:
            print(f"[{code}] hunter API 返 None")
            failed.append(code)
            continue

        cnt = 0
        for row in kl:
            ts = row.get('ts')
            if not ts:
                continue
            if isinstance(ts, str):
                from datetime import date as _d
                ts = _d.fromisoformat(ts)
            cur.execute(
                """INSERT INTO klines (code, period, ts, open, high, low, close, volume)
                   VALUES (%s, 'daily', %s, %s, %s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (code, ts, row.get('open'), row.get('high'),
                 row.get('low'), row.get('close'), row.get('volume', 0)))
            cnt += 1
        conn.commit()
        print(f"[{code}] {name} · {cnt} klines")
        total_stocks += 1
        total_klines += cnt

    cur.close(); conn.close()
    print(f"\n✅ {total_stocks}/{len(STOCKS)} 只 · {total_klines} 行 K 线")
    if failed:
        print(f"⚠  失败: {failed}")


def recompute_momentum():
    from app.services.quant import factor_engine
    codes = [s[0] for s in STOCKS]
    n = factor_engine.compute_and_store("momentum_12m_1m", codes, date.today())
    print(f"[factor] momentum_12m_1m 重算 · {n} 行(真 K 线)")


if __name__ == "__main__":
    backfill()
    recompute_momentum()
