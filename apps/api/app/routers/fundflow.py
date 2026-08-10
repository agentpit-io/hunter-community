from fastapi import APIRouter
from app.services.finance_data_client import get_money_flow, get_orderbook, to_symbol

router = APIRouter()

@router.get("/fundflow/{code}")
async def get_fund_flow(code: str):
    if to_symbol(code) is None:
        return None
    return get_money_flow(code)


@router.get("/orderbook/{code}")
async def get_order_book(code: str):
    if to_symbol(code) is None:
        return {"code": code, "bids": [], "asks": []}
    data = get_orderbook(code)
    if not data:
        return {"code": code, "bids": [], "asks": []}
    return {
        "code":       code,
        "price":      data.get("price") or data.get("close"),
        "prev_close": data.get("pre_close"),
        "bids": [{"price": b.get("price"), "vol": b.get("vol")} for b in (data.get("bids") or [])],
        "asks": [{"price": a.get("price"), "vol": a.get("vol")} for a in (data.get("asks") or [])],
    }
