"""美港股新闻 —— GET /api/gm/news/{market}/{code}"""
from fastapi import APIRouter, Query
from app.services.gm import news_src, findata_db

router = APIRouter()


@router.get("/news/{market}/{code}")
async def gm_news(market: str, code: str, limit: int = Query(12, ge=1, le=30)):
    market = market.upper()
    if market == "US":
        # 读库优先(每日采集入库), 库里没有再实时拉Alpaca兜底
        items = findata_db.us_news_db(code, limit) or news_src.us_news(code, limit)
    elif market == "HK":
        items = findata_db.hk_news_db(code, limit) or news_src.hk_news(code, limit)
    else:
        return {"items": [], "count": 0, "error": "unsupported_market"}
    return {"items": items, "count": len(items)}
