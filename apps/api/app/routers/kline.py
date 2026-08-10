from fastapi import APIRouter
from app.services.finance_data_client import get_kline, get_timeshare, to_symbol

router = APIRouter()

@router.get("/kline/{code}")
async def get_kline_route(code: str, period: str = "daily", limit: int = 120):
    if to_symbol(code) is None:
        return []
    return get_kline(code, period=period, limit=limit)


@router.get("/timeshare/{code}")
async def get_timeshare_route(code: str):
    if to_symbol(code) is None:
        return []
    return get_timeshare(code)
