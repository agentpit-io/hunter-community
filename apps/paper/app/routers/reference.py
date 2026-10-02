"""参考数据的读与写：标的元数据 / 交易日历 / 费用模型。

这些不是账本（不在「只追加」的表里），是风控的**输入**：
涨跌停幅度来自 `fin_instrument`、交易时段来自 `fin_market_calendar`、
费率来自 `fin_fee_model`。M2 提供 upsert 让 `fin-worker`（M4）能把它们同步进来；
M1 已把表建好，本模块只补入口。

写入一律 `INSERT ... ON CONFLICT DO UPDATE`（幂等）——参考数据是可被更正的；
账本数据才只追加。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app import db
from app.schemas import CalendarIn, FeeModelIn, InstrumentIn

router = APIRouter(tags=["reference"])


@router.put("/api/v1/instruments/{code}")
def upsert_instrument(code: str, body: InstrumentIn) -> dict:
    if body.code != code:
        raise HTTPException(400, "路径 code 与请求体 code 不一致")
    with db.cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO fin_instrument
              (code, name, exchange, board, is_st, limit_up_pct, limit_down_pct,
               lot_size, listed_at, is_active, source)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (code) DO UPDATE SET
              name = EXCLUDED.name, exchange = EXCLUDED.exchange, board = EXCLUDED.board,
              is_st = EXCLUDED.is_st, limit_up_pct = EXCLUDED.limit_up_pct,
              limit_down_pct = EXCLUDED.limit_down_pct, lot_size = EXCLUDED.lot_size,
              listed_at = EXCLUDED.listed_at, is_active = EXCLUDED.is_active,
              source = EXCLUDED.source, updated_at = now()
            RETURNING *
            """,
            (body.code, body.name, body.exchange, body.board, body.is_st,
             body.limit_up_pct, body.limit_down_pct, body.lot_size, body.listed_at,
             body.is_active, body.source),
        )
        return cur.fetchone()


@router.get("/api/v1/instruments/{code}")
def get_instrument(code: str) -> dict:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM fin_instrument WHERE code = %s", (code,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"账本里没有标的 {code} 的元数据")
    return row


@router.put("/api/v1/market-calendar/{trade_date}")
def upsert_calendar(trade_date: str, body: CalendarIn) -> dict:
    if str(body.trade_date) != trade_date:
        raise HTTPException(400, "路径日期与请求体日期不一致")
    import psycopg2.extras

    with db.cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO fin_market_calendar (trade_date, is_trading, sessions, note)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (trade_date) DO UPDATE SET
              is_trading = EXCLUDED.is_trading, sessions = EXCLUDED.sessions, note = EXCLUDED.note
            RETURNING *
            """,
            (body.trade_date, body.is_trading, psycopg2.extras.Json(body.sessions), body.note),
        )
        return cur.fetchone()


@router.get("/api/v1/market-calendar/{trade_date}")
def get_calendar(trade_date: str) -> dict:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM fin_market_calendar WHERE trade_date = %s", (trade_date,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"没有 {trade_date} 的交易日历")
    return row


@router.put("/api/v1/fee-models/{version}")
def upsert_fee_model(version: str, body: FeeModelIn) -> dict:
    if body.version != version:
        raise HTTPException(400, "路径版本与请求体版本不一致")
    with db.cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO fin_fee_model
              (version, commission_pct, commission_min, stamp_tax_pct, transfer_fee_pct)
            VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (version) DO UPDATE SET
              commission_pct = EXCLUDED.commission_pct, commission_min = EXCLUDED.commission_min,
              stamp_tax_pct = EXCLUDED.stamp_tax_pct, transfer_fee_pct = EXCLUDED.transfer_fee_pct
            RETURNING *
            """,
            (body.version, body.commission_pct, body.commission_min,
             body.stamp_tax_pct, body.transfer_fee_pct),
        )
        return cur.fetchone()


@router.get("/api/v1/fee-models")
def list_fee_models() -> dict:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM fin_fee_model ORDER BY effective_from DESC, version DESC")
        return {"items": cur.fetchall()}
