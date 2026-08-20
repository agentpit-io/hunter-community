"""A 股/港股 quote 双通道 · 东财 → 腾讯 · 一处封装 · agents/ + app/ 共用

内部原来分散在 3 个位置:
  · providers/data_source/akshare_impl.get_quote(东财单通道)
  · providers/data_source/akshare_impl._tencent_quote_sync(手写解析 · 下标 [6] 错误 · ×100 犯 §5.3 单位坑)
  · services/source_mapping.BUILTIN["tencent"]["quote"](spec · 用户自定义源用)

现在:统一走这里 · 腾讯解析复用 source_mapping.apply(spec 驱动 · 下标 [36]
· _scale + _market 单位归一) —— 与 §5.2/§5.3 沉淀的约定同步。

未来加新浪 quote 兜底也在这里 append。
"""
from __future__ import annotations

import urllib.request

from loguru import logger


def _tencent_symbol(bare: str, market: str = "A") -> str:
    """腾讯行情前缀 · sh/sz/hk/us + code"""
    code = (bare or "").strip().upper()
    market = (market or "A").upper()
    if market == "A":
        return f"sh{code}" if code.startswith(("6", "9")) else f"sz{code}"
    if market == "HK":
        return f"hk{code.zfill(5)}"
    if market == "US":
        return f"us{code}"
    return code.lower()


def _fetch_tencent(bare: str, market: str = "A") -> dict | None:
    """腾讯 quote · qt.gtimg.cn · GBK 编码 · 复用 source_mapping._delimited spec

    · A 股:走 BUILTIN["tencent"]["quote"] · 下标 [36] volume(手) ×100 → 股
    · 港股:走 _market["hk"] 覆盖 · 下标同 · volume/amount **不换算**(§5.2/§5.3)
    """
    from app.services.source_mapping import apply as apply_mapping, MappingError

    sym = _tencent_symbol(bare, market)
    url = f"https://qt.gtimg.cn/q={sym}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        # 绕过 HTTPS_PROXY(容器里配了 clash · 走代理反而被拒)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        raw = opener.open(req, timeout=6).read().decode("gbk", errors="replace")
    except Exception as e:
        logger.warning("[akshare_quote] tencent HTTP {} 失败: {}", bare, e)
        return None

    try:
        mapped = apply_mapping(
            "tencent", "quote", raw,
            market=("hk" if market.upper() == "HK" else ""),
        )
    except MappingError as e:
        logger.warning("[akshare_quote] tencent mapping {} 失败: {}", bare, e)
        return None

    mapped["code"] = bare
    mapped["_src"] = "tencent"
    return mapped


def _fetch_eastmoney(bare: str) -> dict | None:
    """东财 quote · akshare stock_zh_a_spot_em · 只支持 A 股 · 拉全市场快照后 filter code

    ⚠️ akshare stock_zh_a_spot_em 假设单位:volume=股 · amount=元(akshare 库内部已归一)
    · 与直接打 push2.eastmoney.com 的**手/万元**不同 —— §5.3 表说的是原始 API 单位
    · 若日后交叉核对发现对不上,再按 §5.3 加 ×100 换算
    """
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
    except Exception as e:
        logger.warning("[akshare_quote] eastmoney spot 拉取失败: {}", e)
        return None
    if df is None or df.empty:
        return None
    matched = df[df["代码"] == bare]
    if matched.empty:
        return None
    row = matched.iloc[0]

    def _f(v):
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return {
        "code": bare,
        "name": str(row.get("名称", "")),
        "price": _f(row.get("最新价")),
        "change_pct": _f(row.get("涨跌幅")),
        "volume": _f(row.get("成交量")),
        "amount": _f(row.get("成交额")),
        "high": _f(row.get("最高")),
        "low": _f(row.get("最低")),
        "open": _f(row.get("今开")),
        "prev_close": _f(row.get("昨收")),
        "_src": "eastmoney",
    }


def fetch_quote(bare: str, market: str = "A") -> dict | None:
    """返 quote dict · None = 全部通道失败。

    通道顺序:
      1. 东财 · akshare stock_zh_a_spot_em · 字段全 · 数据慢(拉全市场)
         只支持 A 股 · Docker Desktop for Mac vpnkit 环境可能挂(§5.1)
      2. 腾讯 · qt.gtimg.cn · 字段够用 · 快 · docker 出网通 · 覆盖 A/HK/US

    字段约定(遵守 §5.3 volume 单位统一):
      code / name / price / change_pct / volume(股) / amount(元)
      / high / low / open / prev_close / _src(eastmoney|tencent)
    """
    market = (market or "A").upper()

    # 港股/美股直接走腾讯(东财 spot_em 不覆盖)
    if market != "A":
        return _fetch_tencent(bare, market)

    # A 股:东财优先 · 失败落腾讯
    q = _fetch_eastmoney(bare)
    if q:
        return q
    logger.info("[akshare_quote] {} 东财失败 · 落腾讯", bare)
    return _fetch_tencent(bare, market)
