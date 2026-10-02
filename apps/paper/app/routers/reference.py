"""参考数据的读与写：标的元数据 / 交易日历 / 费用模型 / 执行模型。

这些不是账本（不在「只追加」的表里），是风控与撮合的**输入**：
涨跌停幅度来自 `fin_instrument`、交易时段来自 `fin_market_calendar`、
费率来自 `fin_fee_model`、滑点与最小变动价位来自 `fin_execution_model`。
M2 提供 upsert 让 `fin-worker`（M4）能把它们同步进来；M1 已把表建好，本模块只补入口。

写入一律 `INSERT ... ON CONFLICT DO UPDATE`（幂等）——参考数据是可被更正的；
账本数据才只追加。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app import db, ledger
from app.schemas import CalendarIn, ExecutionModelIn, FeeModelIn, InstrumentIn

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
               lot_size, listed_at, is_active, market, currency, source)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (code) DO UPDATE SET
              name = EXCLUDED.name, exchange = EXCLUDED.exchange, board = EXCLUDED.board,
              is_st = EXCLUDED.is_st, limit_up_pct = EXCLUDED.limit_up_pct,
              limit_down_pct = EXCLUDED.limit_down_pct, lot_size = EXCLUDED.lot_size,
              listed_at = EXCLUDED.listed_at, is_active = EXCLUDED.is_active,
              market = EXCLUDED.market, currency = EXCLUDED.currency,
              source = EXCLUDED.source, updated_at = now()
            RETURNING *
            """,
            (body.code, body.name, body.exchange, body.board, body.is_st,
             body.limit_up_pct, body.limit_down_pct, body.lot_size, body.listed_at,
             body.is_active, body.market, body.currency, body.source),
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
            INSERT INTO fin_market_calendar (market, trade_date, is_trading, sessions, note)
            VALUES (%s,%s,%s,%s,%s)
            -- 0029 起主键是 (market, trade_date)：冲突目标必须跟着换，否则本端点会报
            -- 「no unique or exclusion constraint matching the ON CONFLICT specification」。
            -- market 缺省 'CN_A'（旧调用方行为不变）；港美股日历种子显式传市场。
            ON CONFLICT (market, trade_date) DO UPDATE SET
              is_trading = EXCLUDED.is_trading, sessions = EXCLUDED.sessions, note = EXCLUDED.note
            RETURNING *
            """,
            (body.market, body.trade_date, body.is_trading,
             psycopg2.extras.Json(body.sessions), body.note),
        )
        return cur.fetchone()


@router.get("/api/v1/market-calendar/{trade_date}")
def get_calendar(trade_date: str, market: str = "CN_A") -> dict:
    """按 `(market, trade_date)` 读（缺省 CN_A 保持旧调用方行为）。"""
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM fin_market_calendar WHERE market = %s AND trade_date = %s",
            (market, trade_date),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"没有 {market} {trade_date} 的交易日历")
    return row


@router.put("/api/v1/fee-models/{version}")
def upsert_fee_model(version: str, body: FeeModelIn) -> dict:
    if body.version != version:
        raise HTTPException(400, "路径版本与请求体版本不一致")
    with db.cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO fin_fee_model
              (version, commission_pct, commission_min, stamp_tax_pct, transfer_fee_pct,
               market, currency, stamp_side)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (version) DO UPDATE SET
              commission_pct = EXCLUDED.commission_pct, commission_min = EXCLUDED.commission_min,
              stamp_tax_pct = EXCLUDED.stamp_tax_pct, transfer_fee_pct = EXCLUDED.transfer_fee_pct,
              market = EXCLUDED.market, currency = EXCLUDED.currency,
              stamp_side = EXCLUDED.stamp_side
            RETURNING *
            """,
            (body.version, body.commission_pct, body.commission_min,
             body.stamp_tax_pct, body.transfer_fee_pct,
             body.market, body.currency, body.stamp_side),
        )
        return cur.fetchone()


@router.get("/api/v1/market-rules")
def list_market_rules() -> dict:
    """三个市场各一行：时区 / 时段 / **调度时点** / 可卖规则 / 手数 / 价格带模式 / 币种。

    N4 起 fin-worker 的调度从这张表读「每个市场一组时点 + 该市场时区」——
    fin-worker **没有账本库连接**（`01方案 §5.1`），只能经这个 HTTP 端点读。
    顺序固定 `CN_A → HK → US`。
    """
    with db.cursor() as cur:
        return {"items": ledger.list_market_rules(cur)}


@router.get("/api/v1/fee-models")
def list_fee_models() -> dict:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM fin_fee_model ORDER BY effective_from DESC, version DESC")
        return {"items": cur.fetchall()}


@router.put("/api/v1/execution-models/{version}")
def upsert_execution_model(version: str, body: ExecutionModelIn) -> dict:
    """执行模型：滑点 = `slippage_ticks` 个**最小变动价位**（`09 §4.3`）。

    一期默认行 `paper-model-v1`（`slippage_ticks=1, tick_size=0.01`）由迁移
    `0025` 种下；这里提供入口是为了换参数时不用改代码。
    """
    if body.version != version:
        raise HTTPException(400, "路径版本与请求体版本不一致")
    with db.cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO fin_execution_model (version, slippage_ticks, tick_size, part_fill, note)
            VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (version) DO UPDATE SET
              slippage_ticks = EXCLUDED.slippage_ticks,
              tick_size = EXCLUDED.tick_size,
              part_fill = EXCLUDED.part_fill,
              note = EXCLUDED.note,
              effective_from = now()
            RETURNING *
            """,
            (body.version, body.slippage_ticks, body.tick_size, body.part_fill, body.note),
        )
        return cur.fetchone()


@router.get("/api/v1/execution-models")
def list_execution_models() -> dict:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM fin_execution_model ORDER BY effective_from DESC, version DESC")
        return {"items": cur.fetchall()}
