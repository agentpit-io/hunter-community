"""从 akshare 拉 CSI300(沪深300)成分股 · seed 三张表。

灌入:
  1. company_master —— ST 判定 / 行业 / 板块过滤(裸 6 位代码)
  2. stock_universe —— K线 ETL 池(klines_etl.universe 读它 → daily_close),
                       裸 6 位代码 · market='cn' · priority=1
  3. stocks         —— scheduler 的 watchlist 池(resolve_pool→collect_symbols 读它),
                       裸 6 位代码 · market='A' · 挂在系统用户 user_id 下

注意几处已核对过的真实约束(勿改回想当然的写法):
  - stock_universe 主键是 code 单列        → ON CONFLICT (code)
  - stocks 主键是 (code, user_id) 复合      → ON CONFLICT (code, user_id)
    且 exchange / asset_type NOT NULL 无默认 → 必须显式给值
  - 三张表都存**裸 6 位**代码;后缀(.SH/.SZ)由 filters.market_symbol() 在
    入池时按沪深首字补齐,故这里不要带后缀。

用法(容器内):
    docker compose exec -T api python /app/scripts/seed_csi300_universe.py
"""
import os
import sys

sys.path.insert(0, "/app")

import psycopg2
import psycopg2.extras

# scheduler 的 watchlist 池按 code 去重(collect_symbols 用 DISTINCT code,忽略 user_id),
# 挂在这个系统用户名下,既能被池收集、又不污染任何真实用户的自选(PK 复合不冲突)。
# 需要回滚时:DELETE FROM stocks WHERE user_id = 'csi300-seed';
SYSTEM_USER = "csi300-seed"

# akshare 不通时的兜底(沪深300 权重前 20)· 绝不 fake 到 300
HARDCODED_TOP20 = [
    ("600519", "贵州茅台"), ("601318", "中国平安"), ("600036", "招商银行"),
    ("601398", "工商银行"), ("601988", "中国银行"), ("601857", "中国石油"),
    ("600028", "中国石化"), ("600900", "长江电力"), ("601288", "农业银行"),
    ("600030", "中信证券"), ("000858", "五粮液"), ("600276", "恒瑞医药"),
    ("601166", "兴业银行"), ("000333", "美的集团"), ("601668", "中国建筑"),
    ("600809", "山西汾酒"), ("601899", "紫金矿业"), ("000651", "格力电器"),
    ("601012", "隆基绿能"), ("600887", "伊利股份"),
]


def board_of(code: str) -> str:
    """裸 6 位 → 板块。CSI300 只含沪深主板 / 创业板 / 科创板。"""
    if code.startswith("688"):
        return "科创板"
    if code.startswith("6"):
        return "沪主板"
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith(("4", "8")):
        return "北交所"
    return "深主板"


def exchange_of(code: str) -> str:
    """裸 6 位 → 交易所代码(与 filters.market_symbol 的沪深判定一致)。"""
    if code.startswith(("6", "9")):
        return "SH"
    if code.startswith(("4", "8")):
        return "BJ"
    return "SZ"


def fetch_csi300():
    """akshare CSI300 成分股 → [(裸6位, 名称)]。失败降级 top20 并明确打日志。"""
    try:
        import akshare as ak
        df = ak.index_stock_cons_csindex(symbol="000300")
        pairs = [(str(r["成分券代码"]).strip(), str(r["成分券名称"]).strip())
                 for _, r in df.iterrows()]
        pairs = [(c, n) for c, n in pairs if c]
        if not pairs:
            raise RuntimeError("akshare 返回空表")
        print(f"[akshare] 拉到 CSI300 {len(pairs)} 只")
        return pairs
    except Exception as e:
        print(f"[akshare] 失败({e}) · 降级 hardcoded top 20")
        return HARDCODED_TOP20


def seed():
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL 未设")
    pairs = fetch_csi300()
    if not pairs:
        raise RuntimeError("CSI300 为空 · 且降级失败 · 终止")

    conn = psycopg2.connect(dsn)
    try:
        # ── tx1: company_master + stock_universe(核心两表,一起提交)──
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, """
                INSERT INTO company_master(stock_code, name, market, board, updated_at)
                VALUES (%s, %s, 'cn', %s, NOW())
                ON CONFLICT (stock_code) DO UPDATE SET
                    name = EXCLUDED.name, board = EXCLUDED.board, updated_at = NOW()
            """, [(code, name, board_of(code)) for code, name in pairs])
            print(f"[company_master] UPSERT {len(pairs)} 行")

            # stock_universe 已由 klines_etl 灌过 300 个裸代码(name 为空),
            # 这里回填 name + 置 priority=1(核心优先跑)。
            psycopg2.extras.execute_batch(cur, """
                INSERT INTO stock_universe(code, market, name, priority, enabled, added_at)
                VALUES (%s, 'cn', %s, 1, TRUE, NOW())
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name, priority = 1, enabled = TRUE
            """, [(code, name) for code, name in pairs])
            print(f"[stock_universe] UPSERT {len(pairs)} 行 · market=cn · priority=1")
        conn.commit()

        # ── tx2: stocks(scheduler watchlist 池)· 单独事务,失败不拖累前两表 ──
        try:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO stocks(code, name, market, exchange, asset_type,
                                       enabled, user_id, shares)
                    VALUES (%s, %s, 'A', %s, 'stock', TRUE, %s, 0)
                    ON CONFLICT (code, user_id) DO UPDATE SET
                        name = EXCLUDED.name, exchange = EXCLUDED.exchange, enabled = TRUE
                """, [(code, name, exchange_of(code), SYSTEM_USER)
                      for code, name in pairs])
            conn.commit()
            print(f"[stocks] UPSERT {len(pairs)} 行 · market=A · user_id={SYSTEM_USER}")
        except Exception as e:
            conn.rollback()
            print(f"[stocks] skip(前两表已提交)· {e}")
    finally:
        conn.close()
    print(f"✅ seed 完 · CSI300 {len(pairs)} 只")


if __name__ == "__main__":
    seed()
