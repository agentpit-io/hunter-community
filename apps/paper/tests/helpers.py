"""测试小工具：造参考数据、拼委托请求体。"""

from __future__ import annotations

import uuid


def seed_reference(pg, code="600519", trade_date="2026-10-02"):
    """把风控需要的三份参考数据写进库（标的 / 日历 / 费用模型）。"""
    import psycopg2.extras

    pg.execute(
        """
        INSERT INTO fin_instrument
          (code, name, exchange, board, is_st, limit_up_pct, limit_down_pct, lot_size, source)
        VALUES (%s, '测试标的', 'SH', 'main', false, 0.10, 0.10, 100, 'test')
        ON CONFLICT (code) DO UPDATE SET limit_up_pct = EXCLUDED.limit_up_pct,
          limit_down_pct = EXCLUDED.limit_down_pct, lot_size = EXCLUDED.lot_size
        """,
        (code,),
    )
    pg.execute(
        """
        INSERT INTO fin_market_calendar (trade_date, is_trading, sessions)
        VALUES (%s, true, %s)
        ON CONFLICT (trade_date) DO UPDATE SET is_trading = EXCLUDED.is_trading,
          sessions = EXCLUDED.sessions
        """,
        (trade_date, psycopg2.extras.Json(
            [{"open": "09:30", "close": "11:30"}, {"open": "13:00", "close": "15:00"}]
        )),
    )
    pg.execute(
        """
        INSERT INTO fin_fee_model (version, commission_pct, commission_min, stamp_tax_pct, transfer_fee_pct)
        VALUES ('fee-cn-a-v1', 0.00025, 5.00, 0.0005, 0.00001)
        ON CONFLICT (version) DO NOTHING
        """
    )
    return code


def order_body(project_id, *, code="600519", side="buy", qty=100, price="10.00",
               prev_close="10.00", at="2026-10-02T10:00:00+08:00", snapshot_id=None, **kw):
    body = {
        "project_id": project_id,
        "code": code,
        "side": side,
        "qty": qty,
        "price_type": "limit",
        "limit_price": price,
        "snapshot": {
            "snapshot_id": snapshot_id or f"SNAP-TEST-{uuid.uuid4().hex[:12]}",
            "snapshot_time": at,
            "source": "placeholder",
            "last_price": price,
            "prev_close": prev_close,
        },
    }
    body.update(kw)
    return body
