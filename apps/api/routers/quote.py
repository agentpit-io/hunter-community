import json
import redis
import akshare as ak
from fastapi import APIRouter, HTTPException
from app.config import STOCKS, STOCK_MAP

router = APIRouter()
_redis = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)

@router.get("/stocks")
async def list_stocks():
    return {"stocks": STOCKS}

@router.get("/quote/{code}")
async def get_quote(code: str):
    cached = _redis.get(f"quote:{code}")
    if cached:
        return json.loads(cached)
    if code not in STOCK_MAP:
        raise HTTPException(404, f"股票 {code} 不存在")
    return {"code": code, "name": STOCK_MAP[code]["name"], "price": None, "msg": "非交易时间或数据未就绪"}

@router.get("/quotes")
async def get_all_quotes():
    result = []
    for s in STOCKS:
        cached = _redis.get(f"quote:{s['code']}")
        if cached:
            result.append(json.loads(cached))
        else:
            result.append({"code": s["code"], "name": s["name"], "price": None})
    return {"quotes": result}
