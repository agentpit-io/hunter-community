import akshare as ak
from fastapi import APIRouter
from app.config import STOCK_MAP

router = APIRouter()

@router.get("/news/{code}")
async def get_news(code: str, limit: int = 20):
    if code not in STOCK_MAP:
        return {"code": code, "news": []}
    try:
        df = ak.stock_news_em(symbol=code)
        df = df.head(limit)
        news = [{"title": r["新闻标题"], "source": r.get("新闻来源",""), 
                 "url": r.get("新闻链接",""), "published_at": str(r.get("发布时间",""))}
                for _, r in df.iterrows()]
        return {"code": code, "news": news}
    except Exception as e:
        return {"code": code, "news": [], "error": str(e)}

@router.get("/news")
async def get_all_news(limit: int = 5):
    result = {}
    for s in STOCK_MAP.values():
        try:
            df = ak.stock_news_em(symbol=s["code"])
            df = df.head(limit)
            result[s["code"]] = [{"title": r["新闻标题"], "published_at": str(r.get("发布时间",""))}
                                  for _, r in df.iterrows()]
        except:
            result[s["code"]] = []
    return {"news": result}
