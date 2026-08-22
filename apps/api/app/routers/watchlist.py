from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from app.services.database import (
    get_stocks, add_stock, remove_stock, hard_remove_stock,
    list_stocks_with_thesis, get_thesis, upsert_thesis, delete_thesis,
    get_stocks_by_user, add_stock_by_user, remove_stock_by_user,
    hard_remove_stock_by_user, list_stocks_with_thesis_by_user,
    get_thesis_by_user, upsert_thesis_by_user, delete_thesis_by_user,
)
from app.services.finance_data_client import subscribe as fd_subscribe, register_stocks

router = APIRouter()


class StockIn(BaseModel):
    code: str
    name: str
    market: str
    exchange: str
    asset_type: str = "stock"


class ThesisIn(BaseModel):
    thesis_text: str = ""
    shares:      Optional[int]   = None
    cost_price:  Optional[float] = None
    buy_date:    Optional[str]   = None


def _get_user_id(request: Request) -> str | None:
    return getattr(request.state, "user_id", None)


# ─────────────────────────────────────────────────────────────────────
# 自选股（多租户）
# ─────────────────────────────────────────────────────────────────────

@router.get("/watchlist")
async def list_watchlist(request: Request):
    user_id = _get_user_id(request)
    if user_id:
        return get_stocks_by_user(user_id)
    return get_stocks()  # 兼容无 token 的旧调用


@router.post("/watchlist")
async def add_to_watchlist(stock: StockIn, request: Request):
    if not stock.code or not stock.name:
        raise HTTPException(400, "code 和 name 不能为空")
    if stock.asset_type not in ("stock", "etf", "fund"):
        raise HTTPException(400, "asset_type 必须是 stock / etf / fund")
    if stock.market not in ("A", "HK", "US", "FUND"):
        raise HTTPException(400, "market 必须是 A / HK / US / FUND")
    if stock.exchange not in ("SH", "SZ", "HK", "US", "OF"):
        raise HTTPException(400, "exchange 必须是 SH / SZ / HK / US / OF")

    user_id = _get_user_id(request)
    if user_id:
        added = add_stock_by_user(stock.code, stock.name, stock.market,
                                  stock.exchange, stock.asset_type, user_id)
    else:
        added = add_stock(stock.code, stock.name, stock.market,
                          stock.exchange, stock.asset_type)
    fd_result = fd_subscribe(stock.code, stock.name, stock.market,
                             stock.exchange, stock.asset_type)

    # A 股按需补量化数据 —— 加进自选却选不出来,用户会以为是权重配错了。
    #
    # 因子只算「核心池」(沪深300 ∪ 中证500 = 800 只)。核心池外的票在
    # factor_value 里一条都没有,于是"自选 10 只只选出 3 只"而界面不说为什么。
    # 全 A 股预先算掉不现实(K 线 1.81 秒/只 × 5400 = 2.7 小时),所以
    # **加一只补一只**。
    #
    # 失败不影响加自选本身:补数据是附加价值,不该因为拿不到 K 线
    # 就让用户加不进去。
    quant = None
    if stock.market == "A":
        try:
            from app.services.quant import on_demand
            quant = on_demand.ensure_stock(stock.code, user_id=user_id)
        except Exception as e:                                # noqa: BLE001
            quant = {"code": stock.code, "ok": False,
                     "why": f"{type(e).__name__}: {str(e)[:80]}"}
    return {"ok": True, "added": added, "finance_data": fd_result,
            "quant": quant}


@router.delete("/watchlist/{code}")
async def remove_from_watchlist(code: str, request: Request):
    user_id = _get_user_id(request)
    if user_id:
        remove_stock_by_user(code, user_id)
    else:
        remove_stock(code)
    return {"ok": True}


@router.get("/watchlist/manage")
async def list_for_management(request: Request):
    user_id = _get_user_id(request)
    if user_id:
        return {"items": list_stocks_with_thesis_by_user(user_id)}
    return {"items": list_stocks_with_thesis()}


@router.get("/watchlist/{code}/thesis")
async def read_thesis(code: str, request: Request):
    user_id = _get_user_id(request)
    t = get_thesis_by_user(code, user_id) if user_id else get_thesis(code)
    if t is None:
        return {"code": code, "thesis_text": "", "has_thesis": False}
    return {**t, "has_thesis": bool(t.get("thesis_text"))}


@router.put("/watchlist/{code}/thesis")
async def update_thesis(code: str, payload: ThesisIn, request: Request):
    if len(payload.thesis_text) > 500:
        raise HTTPException(400, "thesis_text 不能超过 500 字")
    user_id = _get_user_id(request)
    if user_id:
        stocks = get_stocks_by_user(user_id)
        if not any(s["code"] == code for s in stocks):
            raise HTTPException(404, f"股票 {code} 不在自选股列表，请先添加")
        ok = upsert_thesis_by_user(code, user_id, payload.thesis_text.strip(),
                                   payload.shares, payload.cost_price, payload.buy_date)
    else:
        stocks = get_stocks()
        if not any(s["code"] == code for s in stocks):
            raise HTTPException(404, f"股票 {code} 不在自选股列表，请先添加")
        ok = upsert_thesis(code, payload.thesis_text.strip(),
                           payload.shares, payload.cost_price, payload.buy_date)
    return {"ok": ok, "code": code}


@router.delete("/watchlist/{code}/thesis")
async def clear_thesis(code: str, request: Request):
    user_id = _get_user_id(request)
    if user_id:
        delete_thesis_by_user(code, user_id)
    else:
        delete_thesis(code)
    return {"ok": True}


@router.delete("/watchlist/{code}/hard")
async def hard_delete_stock(code: str, request: Request):
    user_id = _get_user_id(request)
    if user_id:
        hard_remove_stock_by_user(code, user_id)
    else:
        hard_remove_stock(code)
    return {"ok": True}


# P2 completion: /feishu/config endpoints removed with the Lark push channel.
# When SMTP/Slack channels land (Sprint 06 Day 7), settings-tab UI will host
# their equivalent.

# ─────────────────────────────────────────────────────────────────────
# 股票搜索（东方财富 suggest API）
# ─────────────────────────────────────────────────────────────────────

@router.get("/watchlist/search")
async def search_stocks(q: str = "", limit: int = 10):
    """模糊搜索股票名称或代码，返回供添加自选股用的候选列表。"""
    if not q.strip():
        return {"items": [], "count": 0}
    import urllib.request, json as _json, urllib.parse
    url = (
        "https://searchapi.eastmoney.com/api/suggest/get"
        f"?input={urllib.parse.quote(q)}&type=14"
        "&token=D43BF722C8E33BDC906FB84D85E326E8"
        f"&count={min(limit, 15)}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        # 东财对模糊 query(如"chang c"、"长城qi c")会返回 Data: null,
        # 用 or 兜底 · dict.get 的 default 只在 key 缺失时生效,值为 None 不兜
        qct = data.get("QuotationCodeTable") or {}
        raw = qct.get("Data") or []
    except Exception:
        return {"items": [], "count": 0}

    # JYS: 东财数字码 vs 交易所字符串码同时存在, 两套都要覆盖
    _jys_to_exchange = {
        # A 股 / 数字码
        "1": "SH", "2": "SH", "4": "SZ", "3": "BJ", "7": "HK", "5": "US",
        # 东财 US/HK 实际返回的交易所字符串
        "NASDAQ": "US", "NYSE": "US", "AMEX": "US", "HK": "HK",
    }
    # Classify: 东财对 US/HK 实际返回 "UsStock" / "HK" (非文档所述 "USStock" / "HKStock"),
    # 同时保留期望值做防御. 未知类型 fallback "A" 与旧行为一致.
    _classify_to_market = {
        "AStock": "A", "ETF": "A", "Fund": "FUND",
        "HKStock": "HK", "HK": "HK",
        "USStock": "US", "UsStock": "US",
    }
    items = []
    for r in raw:
        cls = r.get("Classify", "AStock")
        jys = r.get("JYS", "2")
        market = _classify_to_market.get(cls, "A")
        exchange = _jys_to_exchange.get(jys, "SH")
        asset_type = "etf" if cls == "ETF" else "stock"
        items.append({
            "code": r.get("Code", ""),
            "name": r.get("Name", ""),
            "market": market,
            "exchange": exchange,
            "asset_type": asset_type,
            "type_name": r.get("SecurityTypeName", ""),
        })
    return {"items": items, "count": len(items)}

