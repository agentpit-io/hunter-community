"""复用rs_daily与既有腾讯采集器；完整窗口更新，禁止混用旧klines。"""
from datetime import datetime
from zoneinfo import ZoneInfo
import psycopg2.extras
from app.services.fin import store
from app.services import stock_rules as rules

MARKETS={'CN_A':'a','HK':'hk','US':'us'}
ZONES={'CN_A':'Asia/Shanghai','HK':'Asia/Hong_Kong','US':'America/New_York'}


def instruments():
    conn=store.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT DISTINCT i.code,i.market,i.exchange FROM fin_instrument i "
                        "JOIN fin_instrument_sector s ON s.code=i.code AND s.market=i.market "
                        "JOIN fin_horse h ON h.sector=s.sector AND h.market=s.market "
                        "WHERE i.is_active=true AND h.project_id IS NOT NULL ORDER BY i.market,i.code")
            return list(cur.fetchall())
    finally:
        conn.close()


def collect(market):
    from app.services.quant import rs_history as rh
    records={}
    errors={}
    current=datetime.now(ZoneInfo(ZONES[market])).date()
    for item in instruments():
        if item['market']!=market:
            continue
        exchange={'SH':'SSE','SZ':'SZSE'}.get(item['exchange'],item['exchange'])
        symbol=rh.tx_symbol(MARKETS[market],item['code'],exchange)
        if not symbol:
            errors[item['code']]='无法确定行情代码'
            continue
        try:
            rows=rh.fetch_bars(symbol,n=320)
            bars=[dict(ts=str(b[0]),close=b[1],high=b[2],low=b[3],volume=b[4],open=b[5])
                  for b in (rows or []) if b[0]<current]
            rules.validate_bars(bars)
            if len(bars)<131:
                errors[item['code']]='历史不足131根'
                continue
            records[item['code']]=bars
        except rh.WafBlocked:
            errors[item['code']]='行情源限流，中止本批采集'
            break
        except (ValueError,TypeError,ArithmeticError):
            errors[item['code']]='源数据校验未通过'
    return dict(market=market,source='tencent_qfq_complete_320',records=records,errors=errors)


def apply_snapshot(snapshot):
    from app.services.quant import rs_history as rh
    from datetime import date
    market=MARKETS[snapshot['market']]
    conn=store.get_conn()
    try:
        for code,bars in snapshot['records'].items():
            rules.validate_bars(bars)
            rows=[(date.fromisoformat(b['ts']),b['close'],b['high'],b['low'],b['volume'],b['open']) for b in bars]
            rh._upsert(conn,market,code,rows)
    finally:
        conn.close()


def refresh_if_active(market):
    conn=store.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT EXISTS(SELECT 1 FROM fin_horse h JOIN fin_param p ON p.project_id=h.project_id "
                        "WHERE h.market=%s AND EXISTS(SELECT 1 FROM jsonb_array_elements(p.strategies) s "
                        "WHERE s->>'active'='true' AND s->>'key' LIKE 'tq_daily_v%%'))",(market,))
            enabled=cur.fetchone()[0]
    finally:
        conn.close()
    if not enabled:
        return None
    snapshot=collect(market)
    apply_snapshot(snapshot)
    return dict(updated=len(snapshot['records']),errors=snapshot['errors'])
