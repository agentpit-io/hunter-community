import asyncio
from fastapi import APIRouter
from app.services.finance_data_client import get_kline, get_timeshare, to_symbol

router = APIRouter()


@router.get("/kline/{code}")
async def get_kline_route(code: str, period: str = "daily", limit: int = 120):
    """日/周/月 K 线。

    ## 拿不到的时候要**说拿不到**

    原来这里在 `to_symbol` 返回 None 时直接 `return []`,HTTP 200。
    前端拿到一个合法的空数组,分不清"这只票没有数据"和"还在加载"——
    实测表现就是 K 线区域**永远显示「加载中…」**,用户等多久都没结果,
    还以为是网慢。测试人员加 NVDA 时报的就是这个。

    现在拿不到就带 `error` 字段说明原因。前端据此显示"暂无数据",
    而不是继续转圈。

    **这条改动独立于港美股支持** —— 就算港美股都通了,冷门票、退市票、
    源挂了还是会拿不到,每一次都会长得像同一个 bug。
    """
    rows = await asyncio.to_thread(get_kline, code, period=period, limit=limit)
    if rows:
        return rows
    return {"code": code, "period": period, "data": [],
            "error": "no_data",
            "message": _why(code)}


@router.get("/timeshare/{code}")
async def get_timeshare_route(code: str):
    if to_symbol(code) is None:
        return {"code": code, "data": [], "error": "no_data", "message": _why(code)}
    rows = get_timeshare(code)
    if rows:
        return rows
    return {"code": code, "data": [], "error": "no_data", "message": _why(code)}


def _why(code: str) -> str:
    """说清楚是**哪一类**拿不到 —— 用户能据此判断该等、该换票、还是该报bug。"""
    try:
        from app.services.market_source import market_of
        m = market_of(code)
    except Exception:                                          # noqa: BLE001
        m = "a"
    # 港美股拿不到时分不清是「这只票源里就没有」还是「源在限流」—— 两种都说出来,不能只说限流让人干等。
    # 2026-09-14 执行筛选器用例时实测:OTC 小票 QKIE 源里根本没有日线,原文案让人以为过几分钟就好
    if m == "hk":
        return "这只港股拿不到日线 · 可能是刚上市 / 停牌 / 代码不对,也可能是免费源在限流(过几分钟再试)"
    if m == "us":
        return "这只美股拿不到日线 · 可能是 OTC 场外小票 / 刚上市 / 代码不对,也可能是免费源在限流(过几分钟再试)"
    from app.services.quant import local_kline
    if local_kline.is_unsupported(code):
        return "北交所的历史日线免费源没有 —— 不是故障,是这个板块拿不到"
    # 行情源被拦(WAF 501)时也走到这里,只说「退市 / 停牌 / 代码错」会把源故障说成票的问题(执行用例 KK-006 发现)
    return "这只票拿不到数据 · 可能已退市、长期停牌或代码填错了,也可能是行情源暂时连不上(稍后再试)"
