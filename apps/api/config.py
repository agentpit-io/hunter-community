# 6只自选股配置
STOCKS = [
    {"code": "002595", "name": "豪迈科技", "market": "A", "exchange": "SZ"},
    {"code": "000933", "name": "神火股份", "market": "A", "exchange": "SZ"},
    {"code": "601899", "name": "紫金矿业", "market": "A", "exchange": "SH"},
    {"code": "002001", "name": "新和成",   "market": "A", "exchange": "SZ"},
    {"code": "02333",  "name": "长城汽车", "market": "HK", "exchange": "HK"},
    {"code": "01378",  "name": "中国宏桥", "market": "HK", "exchange": "HK"},
]

# AkShare 代码格式
def ak_code(stock: dict) -> str:
    if stock["market"] == "HK":
        return stock["code"]
    prefix = "sh" if stock["exchange"] == "SH" else "sz"
    return f"{prefix}{stock['code']}"

STOCK_MAP = {s["code"]: s for s in STOCKS}
