from fastapi import APIRouter
from app.services.finance_data_client import get_news, get_all_news_bulk, to_symbol

router = APIRouter()


@router.get("/news/{code}")
async def get_news_route(code: str, limit: int = 20):
    if to_symbol(code) is None:
        return []
    return get_news(code, limit=limit)


@router.get("/news")
async def get_all_news(limit: int = 100):
    return get_all_news_bulk(limit=limit)
