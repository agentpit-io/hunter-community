"""股票名 → 代码 模糊搜索

三层数据源(冷启动优先级):
  1) hermes DB stocks_catalog 表 (内网, ~50ms)  ← 主路径,由每日 seed 保持新鲜
  2) 项目里的静态 JSON api/data/stocks_catalog_baseline.json (~10ms 读文件)  ← 兜底
  3) akshare stock_info_a_code_name() (~5s,GCP 有时失败)  ← 最后降级

匹配:
  - 全 A 股(港股/美股暂不接)
  - 名字精确/前缀/子串; 代码精确/前缀
  - 内存缓存(冷启动后 <10ms)
"""
import asyncio
import json
import logging
import os
import time

log = logging.getLogger(__name__)


# 内存缓存：[{code, name, exchange, market}, ...]
_CATALOG: list[dict] = []
_CATALOG_TS: float = 0.0
_CATALOG_TTL = 24 * 3600   # 24 小时刷新一次
_LOAD_LOCK = asyncio.Lock()


# 项目静态 JSON baseline 路径 (由 dump 脚本或 CI 定期刷新)
_BASELINE_JSON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "api", "data", "stocks_catalog_baseline.json",
)


def _load_from_db_sync() -> list[dict]:
    """从 hermes DB stocks_catalog 表读全量 A 股。表空/不存在返 []。"""
    try:
        # 延迟 import,避免 agents 模块被非 hermes 环境加载时报错
        import sys
        _api_root = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "api",
        )
        if _api_root not in sys.path:
            sys.path.insert(0, _api_root)
        from app.services.database import get_conn
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT code, name, exchange, market, symbol "
            "FROM stocks_catalog WHERE enabled = TRUE ORDER BY code"
        )
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return []
        items = [{"code": r[0], "name": r[1], "exchange": r[2],
                  "market": r[3], "symbol": r[4]} for r in rows]
        log.info("stock_search: loaded %d stocks from DB", len(items))
        return items
    except Exception as e:
        log.warning("stock_search: DB load failed: %s", e)
        return []


def _load_from_baseline_sync() -> list[dict]:
    """从项目里的静态 JSON 读全量 A 股。文件不存在或格式错误返 []。"""
    if not os.path.exists(_BASELINE_JSON):
        return []
    try:
        with open(_BASELINE_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("items") or []
        log.info("stock_search: loaded %d stocks from baseline JSON (%s)",
                 len(items), data.get("generated_at", "?"))
        return items
    except Exception as e:
        log.warning("stock_search: baseline JSON load failed: %s", e)
        return []


def _load_from_akshare_sync() -> list[dict]:
    """akshare 兜底(GCP 有时可,有时被墙)。失败重试 3 次。"""
    try:
        import akshare as ak
    except ImportError:
        log.error("akshare not installed")
        return []

    import time as _time
    for attempt in range(3):
        try:
            df = ak.stock_info_a_code_name()
            items: list[dict] = []
            for _, r in df.iterrows():
                code = str(r.get("code") or "").zfill(6)
                name = str(r.get("name") or "").strip()
                if not code or not name or len(code) != 6:
                    continue
                exchange = "SH" if code.startswith(("60", "68", "11", "51", "52")) else "SZ"
                items.append({
                    "code": code, "name": name,
                    "exchange": exchange, "market": "A",
                    "symbol": f"{code}.{exchange}",
                })
            if items:
                log.info("stock_search: loaded %d stocks from akshare (attempt %d)",
                         len(items), attempt + 1)
                return items
        except Exception as e:
            log.warning("stock_search: akshare load failed (attempt %d): %s",
                        attempt + 1, e)
        if attempt < 2:
            _time.sleep(1)
    return []


def _load_catalog_sync() -> list[dict]:
    """三层降级加载:DB → 静态 JSON → akshare。"""
    items = _load_from_db_sync()
    if items:
        return items
    log.info("stock_search: DB 空/失败, 降级读 baseline JSON")
    items = _load_from_baseline_sync()
    if items:
        return items
    log.info("stock_search: baseline JSON 空/失败, 最后降级 akshare")
    return _load_from_akshare_sync()


async def _ensure_catalog():
    global _CATALOG, _CATALOG_TS
    now = time.time()
    if _CATALOG and (now - _CATALOG_TS) < _CATALOG_TTL:
        return
    async with _LOAD_LOCK:
        if _CATALOG and (time.time() - _CATALOG_TS) < _CATALOG_TTL:
            return
        items = await asyncio.to_thread(_load_catalog_sync)
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
