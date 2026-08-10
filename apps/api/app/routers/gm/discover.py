"""gm 发现页：ETF 热度榜（us_kline_1d 现成数据）+ 美股财报日历（Nasdaq）。"""
from fastapi import APIRouter
from app.services.gm import findata_db, earnings_cal

router = APIRouter()


@router.get("/discover/etf-hot")
async def gm_etf_hot():
    items = findata_db.etf_hot()
    return {"items": items, "count": len(items)}


@router.get("/discover/earnings")
async def gm_earnings_week():
    # 读库优先(每日采集入库), 空则实时拉Nasdaq兜底
    days = findata_db.earnings_week_db()
    partial = False
    if not days:
        meta = earnings_cal.week_earnings_meta()
        days, partial = meta["days"], meta["partial"]
    return {"days": days, "partial": partial,
            "count": sum(len(d["items"]) for d in days)}


@router.get("/discover/analysts-top")
async def gm_analysts_top():
    """近期分析师上调/新覆盖动向"""
    items = findata_db.analysts_top(10, 15)
    return {"items": items, "count": len(items)}


@router.get("/analysts/{code}")
async def gm_analysts_symbol(code: str):
    """单只美股评级变动历史"""
    items = findata_db.analysts_by_symbol(code, 10)
    return {"items": items, "count": len(items)}


@router.get("/discover/hk")
async def gm_discover_hk():
    """港股发现: 南向资金汇总 + AH溢价榜"""
    from app.services.gm import ah_south
    return {
        "southbound": ah_south.southbound_summary(),
        "ah_premium": ah_south.ah_premium(15),
    }
