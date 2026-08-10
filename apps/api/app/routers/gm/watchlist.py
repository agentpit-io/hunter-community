"""美港股自选（gm 端）。

隔离策略（MVP）：复用现有 stocks 表的 (code, user_id) 多租户机制，
gm 端只读写 market IN ('US','HK') 的行；A股端 /api/watchlist 已过滤
market NOT IN ('US','HK')——两端同一张表、互不可见。
后续按方案迁 securities/gm_watchlist 时本文件只改 DAO。
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from app.services.database import get_conn, add_stock_by_user, remove_stock_by_user

router = APIRouter()


class GmStockIn(BaseModel):
    code: str
    name: str
    market: str          # US / HK
    exchange: str = ""   # US / HK
    asset_type: str = "stock"


def _uid(request: Request) -> str:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "需要登录")
    return user_id


@router.get("/watchlist")
async def gm_list_watchlist(request: Request):
    user_id = _uid(request)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT code, name, market, exchange, COALESCE(asset_type,'stock') "
        "FROM stocks WHERE enabled = TRUE AND user_id = %s AND market IN ('US','HK') "
        "ORDER BY market, code",
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [{"code": r[0], "name": r[1], "market": r[2],
             "exchange": r[3], "asset_type": r[4]} for r in rows]


@router.post("/watchlist")
async def gm_add_watchlist(stock: GmStockIn, request: Request):
    user_id = _uid(request)
    if stock.market not in ("US", "HK"):
        raise HTTPException(400, "gm端 market 必须是 US / HK")
    if not stock.code or not stock.name:
        raise HTTPException(400, "code 和 name 不能为空")
    code = stock.code.upper() if stock.market == "US" else stock.code.zfill(5)
    exchange = stock.exchange or stock.market
    added = add_stock_by_user(code, stock.name, stock.market, exchange,
                              stock.asset_type, user_id)
    return {"ok": True, "added": added}


@router.delete("/watchlist/{code}")
async def gm_remove_watchlist(code: str, request: Request):
    user_id = _uid(request)
    remove_stock_by_user(code, user_id)
    return {"ok": True}
