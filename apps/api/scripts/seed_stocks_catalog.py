"""同步全 A 股 catalog 到 hermes DB `stocks_catalog` 表。

数据源优先级(与 stock_search.py 保持一致):
  1) akshare `stock_info_a_code_name()` - 首选,数据最新最全
  2) 若失败, 从静态 baseline JSON (`api/data/stocks_catalog_baseline.json`) 兜底

调用方式:
  - 手动: `python scripts/seed_stocks_catalog.py`
  - 自动: main.py lifespan 里 APScheduler 每日 03:00 触发 seed_stocks_catalog()

写入方式: UPSERT (INSERT ... ON CONFLICT UPDATE),不删除任何已存在的股票
  - 新股上市 → INSERT
  - 已存在 → UPDATE name/exchange (处理更名)
  - 从源里消失的老股 → 保留在表里,只标 enabled=false (通过一次性扫描判断)
    (MVP 阶段不做退市清理,避免误伤;有需要时人工 UPDATE)
"""
import json
import logging
import os
import sys

log = logging.getLogger(__name__)

# 允许直接执行本文件
_API_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)
_HERMES_ROOT = os.path.dirname(_API_ROOT)
if _HERMES_ROOT not in sys.path:
    sys.path.insert(0, _HERMES_ROOT)


_BASELINE_JSON = os.path.join(_API_ROOT, "data", "stocks_catalog_baseline.json")


def _fetch_from_akshare() -> list[dict]:
    """从 akshare 拉全 A 股 code-name 表。失败返 []。"""
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        items: list[dict] = []
        for _, r in df.iterrows():
            code = str(r.get("code") or "").zfill(6)
            name = str(r.get("name") or "").strip()
            if not code or not name or len(code) != 6:
                continue
            exchange = "SH" if code.startswith(("60", "68", "11", "51", "52")) else "SZ"
            items.append({
                "code": code, "name": name,
                "exchange": exchange, "market": "A",
                "symbol": f"{code}.{exchange}",
            })
        return items
    except Exception as e:
        log.warning("[seed catalog] akshare 失败: %s", e)
        return []


def _fetch_from_baseline() -> list[dict]:
    """从项目里的静态 JSON 加载(兜底)。"""
    if not os.path.exists(_BASELINE_JSON):
        return []
    try:
        with open(_BASELINE_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("items") or []
    except Exception as e:
        log.warning("[seed catalog] baseline JSON 读取失败: %s", e)
        return []


def seed_stocks_catalog() -> dict:
    """执行同步,返回统计信息。可从 APScheduler 或手工调用。"""
    from app.services.database import get_conn

    items = _fetch_from_akshare()
    source = "akshare"
    if not items:
        items = _fetch_from_baseline()
        source = "baseline_json"
    if not items:
        log.error("[seed catalog] 所有数据源都失败, 放弃本次 seed")
        return {"ok": False, "inserted": 0, "updated": 0, "source": "none"}

    conn = get_conn()
    cur = conn.cursor()
    inserted = 0
    updated = 0
    for it in items:
        code = str(it.get("code") or "")
        name = str(it.get("name") or "")
        exchange = str(it.get("exchange") or "")
        market = str(it.get("market") or "A")
        symbol = str(it.get("symbol") or f"{code}.{exchange}")
        if not (code and name and exchange):
            continue
        cur.execute("""
            INSERT INTO stocks_catalog (code, name, exchange, market, symbol, enabled, updated_at)
            VALUES (%s, %s, %s, %s, %s, TRUE, NOW())
            ON CONFLICT (code) DO UPDATE SET
              name = EXCLUDED.name,
              exchange = EXCLUDED.exchange,
              market = EXCLUDED.market,
              symbol = EXCLUDED.symbol,
              enabled = TRUE,
              updated_at = NOW()
            RETURNING (xmax = 0) AS did_insert
        """, (code, name, exchange, market, symbol))
        row = cur.fetchone()
        if row and row[0]:
            inserted += 1
        else:
            updated += 1
    conn.commit()
    conn.close()
    log.info("[seed catalog] source=%s inserted=%d updated=%d total=%d",
             source, inserted, updated, len(items))
    return {"ok": True, "source": source, "inserted": inserted,
            "updated": updated, "total": len(items)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = seed_stocks_catalog()
    print(json.dumps(result, ensure_ascii=False, indent=2))
