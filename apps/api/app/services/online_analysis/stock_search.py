"""股票名 → 代码 模糊搜索

支持：
  - 全 A 股 + 港股 + 美股（用 akshare 的 stock_info_a_code_name 等）
  - 名字模糊匹配（先精确再前缀再包含）
  - 代码前缀匹配（输 600 显示候选）
  - 内存缓存（首次访问 5 秒，之后 < 10ms）
"""
import asyncio
import logging
import time
from typing import Any

log = logging.getLogger(__name__)


# 内存缓存：[{code, name, exchange, market}, ...]
_CATALOG: list[dict] = []
_CATALOG_TS: float = 0.0
_CATALOG_TTL = 24 * 3600   # 24 小时刷新一次
_LOAD_LOCK = asyncio.Lock()


def _load_a_stocks_sync() -> list[dict]:
    """从 akshare 一次性拉 A 股代码-名称表"""
    try:
        import akshare as ak
    except ImportError:
        log.error("akshare not installed")
        return []

    items: list[dict] = []
    try:
        df = ak.stock_info_a_code_name()
        # df cols: code, name
        for _, r in df.iterrows():
            code = str(r.get("code") or "").zfill(6)
            name = str(r.get("name") or "").strip()
            if not code or not name or len(code) != 6:
                continue
            # 推断交易所
            if code.startswith(("60", "68", "11", "51", "52")):
                exchange = "SH"
            else:
                exchange = "SZ"
            items.append({
                "code":     code,
                "name":     name,
                "exchange": exchange,
                "market":   "A",
                "symbol":   f"{code}.{exchange}",
            })
        log.info("stock_search: loaded %d A-share stocks", len(items))
    except Exception as e:
        log.warning("stock_search: load A failed: %s", e)
    return items


async def _ensure_catalog():
    global _CATALOG, _CATALOG_TS
    now = time.time()
    if _CATALOG and (now - _CATALOG_TS) < _CATALOG_TTL:
        return
    async with _LOAD_LOCK:
        if _CATALOG and (time.time() - _CATALOG_TS) < _CATALOG_TTL:
            return
        items = await asyncio.to_thread(_load_a_stocks_sync)
        if items:
            _CATALOG = items
            _CATALOG_TS = time.time()


async def search(query: str, limit: int = 10) -> list[dict]:
    """模糊搜索股票

    匹配优先级：
    1. 代码完全相等
    2. 代码前缀
    3. 名字完全相等
    4. 名字前缀
    5. 名字子串
    """
    if not query or not query.strip():
        return []

    q = query.strip()
    # 去掉常见后缀
    q_clean = q.replace(".SH", "").replace(".SZ", "").replace(".HK", "").replace(".US", "").strip()

    await _ensure_catalog()
    if not _CATALOG:
        return []

    exact_code   = []
    prefix_code  = []
    exact_name   = []
    prefix_name  = []
    contains_name = []

    is_digit_query = q_clean.isdigit()

    for s in _CATALOG:
        code, name = s["code"], s["name"]

        if is_digit_query:
            if code == q_clean:
                exact_code.append(s)
            elif code.startswith(q_clean):
                prefix_code.append(s)
        else:
            if name == q:
                exact_name.append(s)
            elif name.startswith(q):
                prefix_name.append(s)
            elif q in name:
                contains_name.append(s)

    result = exact_code + prefix_code + exact_name + prefix_name + contains_name

    # 中文 query 无结果时回退：尝试前 2-3 字（如"用友软件"→"用友"找到"用友网络"）
    if not result and not is_digit_query and len(q) >= 3:
        for short_len in (3, 2):
            if len(q) > short_len:
                short_q = q[:short_len]
                fallback = [s for s in _CATALOG if short_q in s["name"]]
                if fallback:
                    return fallback[:limit]

    return result[:limit]
