"""美港股一手情报(官方公告) —— GET /api/gm/scout/{market}/{code}"""
from fastapi import APIRouter, HTTPException, Query
from app.services.gm import filings, findata_db

router = APIRouter()


@router.get("/scout/{market}/{code}")
async def gm_scout(market: str, code: str, limit: int = Query(10, ge=1, le=20)):
    market = market.upper()
    code = code.upper() if market == "US" else code.zfill(5)
    if market == "US":
        # 读库优先(EDGAR每日索引入库), 空则实时查SEC兜底
        items = findata_db.us_filings_db(code, limit) or filings.us_filings(code, limit)
        src = "SEC EDGAR(官方)"
    elif market == "HK":
        items = findata_db.hk_filings_db(code, limit) or filings.hk_filings(code, limit)
        src = "港交所披露易(官方)"
    else:
        raise HTTPException(400, "market 必须是 US / HK")
    return {"items": items, "count": len(items), "source": src}
