"""F8 · 对立面主动搜索（视野层防御）

很多投毒做法是"只放利好不放利空"。Hamais 主动反向搜索，
强制 AI 看到正反两面。

实现策略（MVP）:
  - 用 akshare stock_news_em 反向 query 个股相关风险/减持/利空信息
  - 关键词组合 5 个 query，去重 + 合并
  - 不依赖外部搜索 API（避免引入新依赖）

V1 升级方向：接 Bing/Google Search API 拿真实搜索结果
"""
import asyncio
import logging
from datetime import datetime
from typing import Any

from .unified_fetcher import NewsItem

log = logging.getLogger(__name__)


# 对立面 query 模板（按风险维度分组）
CONTRARIAN_QUERIES = {
    "财务风险":   ["业绩下滑", "业绩不及预期", "毛利率下降", "商誉减值", "财务问题"],
    "治理风险":   ["减持", "高管辞职", "内斗", "实控人变更"],
    "监管风险":   ["立案调查", "处罚决定", "ST", "监管警示"],
    "业务风险":   ["客户流失", "竞争加剧", "订单下降", "产能过剩"],
    "做空/质疑": ["做空", "质疑", "造假", "目标价下调", "评级下调"],
}


def _akshare_safe_news(stock_name: str, keyword: str, limit: int = 10) -> list[dict]:
    """同步调用 akshare stock_news_em 反向 query"""
    try:
        import akshare as ak
    except ImportError:
        log.error("akshare not installed")
        return []
    try:
        # stock_news_em 是按个股代码查新闻，没有 keyword 参数
        # 我们 fallback 到 stock_news_em(symbol=...) 然后客户端 filter
        # 或者用 ak.stock_news_main_cx (财新主流新闻全文)
        # MVP 暂用最简单：搜全市场新闻 + 关键词
        df = ak.stock_news_em(symbol=stock_name)
        if df is None or df.empty:
            return []

        # 客户端关键词过滤
        col_title = "新闻标题" if "新闻标题" in df.columns else df.columns[0]
        mask = df[col_title].astype(str).str.contains(keyword, na=False)
        matched = df[mask].head(limit)

        items = []
        for _, r in matched.iterrows():
            try:
                title = str(r.get(col_title, "")).strip()
                pt_str = str(r.get("发布时间", "") or "").strip()
                pt = None
                try:
                    pt = datetime.strptime(pt_str[:19], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    pt = datetime.now()
                items.append({
                    "title":        title,
                    "content":      str(r.get("新闻内容", "") or "")[:300],
                    "url":          str(r.get("新闻链接", "") or ""),
                    "publish_time": pt,
                    "matched_keyword": keyword,
                })
            except Exception:
                continue
        return items
    except Exception as e:
        log.warning("contrarian search '%s' for %s failed: %s", keyword, stock_name, e)
        return []


async def search_contrarian(stock_name: str, per_query_limit: int = 5,
                             max_total: int = 15) -> list[NewsItem]:
    """主动搜索对立面新闻，返回 NewsItem 列表（来源标 contrarian）

    Args:
        stock_name:       股票中文名
        per_query_limit:  每个 query 最多保留几条
        max_total:        总条数上限

    Returns:
        list[NewsItem]: 已识别为对立面的新闻（去重）
    """
    # 把所有关键词扁平化，去掉部分（避免一次性发太多 query）
    all_keywords = []
    for category, keywords in CONTRARIAN_QUERIES.items():
        all_keywords.extend([(category, kw) for kw in keywords[:3]])  # 每类只取前 3

    # 并发查（限制并发数避免被限流）
    semaphore = asyncio.Semaphore(3)
    async def _bounded_search(cat: str, kw: str) -> tuple[str, list[dict]]:
        async with semaphore:
            items = await asyncio.to_thread(_akshare_safe_news, stock_name, kw, per_query_limit)
            return cat, items

    tasks = [asyncio.create_task(_bounded_search(c, k)) for c, k in all_keywords]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 合并 + 去重（按 title）
    seen_titles = set()
    contrarian_items: list[NewsItem] = []
    for r in results:
        if isinstance(r, Exception):
            continue
        category, items = r
        for it in items:
            title = it["title"]
            if title in seen_titles:
                continue
            seen_titles.add(title)
            contrarian_items.append(NewsItem(
                title         = title,
                content       = it.get("content"),
                publish_time  = it["publish_time"],
                source_key    = "contrarian",
                source_name   = f"对立面搜索 · {category}",
                source_tier   = "media_general",
                source_weight = 0.65,
                url           = it.get("url"),
                raw_meta      = {
                    "category":         category,
                    "matched_keyword":  it.get("matched_keyword"),
                },
            ))
            if len(contrarian_items) >= max_total:
                break
        if len(contrarian_items) >= max_total:
            break

    log.info("F8 contrarian search %s: found %d items", stock_name, len(contrarian_items))
    return contrarian_items
