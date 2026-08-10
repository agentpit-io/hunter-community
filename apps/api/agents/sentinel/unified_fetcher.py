"""F1 · UnifiedFetcher 多源并发抓取

并发拉取 6+ 信源数据，统一返回带元数据（source_tier/weight）的结构。
hermes 在线分析 / 持仓哨兵 V1 都消费此 fetcher。

数据来源（按 D1 决策"混合策略"）：
- 权威源（巨潮、财联社、东方财富个股新闻）→ finance-data API
- 资金/行情（北向、龙虎榜、大盘指数、板块）  → akshare 现拉
- 对立面搜索                                  → akshare 反向 query（见 contrarian_search.py）

设计：
- 单源 10s 超时，全局 30s 超时
- 单源失败不影响其他源
- 自动注入元数据（来自 source_registry）
"""
import asyncio
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any

import httpx

from .source_registry import (
    SOURCE_REGISTRY,
    SufficiencyThresholds,
    is_authoritative,
)

log = logging.getLogger(__name__)

FINANCE_DATA_URL   = os.getenv("FINANCE_DATA_URL",   "https://finance-data.agentpit.io")
FINANCE_DATA_TOKEN = os.getenv("FINANCE_DATA_TOKEN", "FinAPI@2026!")

_HTTPX_HEADERS = {"X-Finance-Token": FINANCE_DATA_TOKEN}
_PER_SOURCE_TIMEOUT = 10.0
_GLOBAL_TIMEOUT = 30.0


@dataclass
class NewsItem:
    title: str
    content: str | None
    publish_time: datetime
    source_key: str
    source_name: str
    source_tier: str
    source_weight: float
    url: str | None = None
    importance: str | None = None      # 巨潮专用 CRITICAL/HIGH/MEDIUM/LOW
    raw_meta: dict | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["publish_time"] = self.publish_time.isoformat() if self.publish_time else None
        return d


@dataclass
class FetchResult:
    news_items: list[NewsItem]                = field(default_factory=list)
    market_data: dict                         = field(default_factory=dict)   # 大盘 / 板块 / 行业
    capital_flow_data: dict                   = field(default_factory=dict)   # 北向 / 龙虎榜 / 大宗
    successful_sources: list[str]             = field(default_factory=list)
    failed_sources: list[dict]                = field(default_factory=list)

    @property
    def authoritative_count(self) -> int:
        return sum(1 for n in self.news_items if is_authoritative(n.source_weight))

    @property
    def high_weight_facts_count(self) -> int:
        """权重 ≥ 0.7 的事实数（F5 用）"""
        return sum(1 for n in self.news_items if n.source_weight >= 0.7)

    @property
    def coverage_score(self) -> float:
        """信源覆盖度：成功源数 / 启用源数"""
        enabled = sum(1 for s in SOURCE_REGISTRY.values() if s["enabled"])
        if enabled == 0:
            return 0.0
        return len(self.successful_sources) / enabled

    @property
    def has_authoritative(self) -> bool:
        return self.authoritative_count >= 1

    def to_dict(self) -> dict:
        return {
            "news_items":          [n.to_dict() for n in self.news_items],
            "market_data":         self.market_data,
            "capital_flow_data":   self.capital_flow_data,
            "successful_sources":  self.successful_sources,
            "failed_sources":      self.failed_sources,
            "coverage_score":      self.coverage_score,
            "authoritative_count": self.authoritative_count,
            "has_authoritative":   self.has_authoritative,
        }


# ─── finance-data 源（HTTP）────────────────────────────────────────────────

async def _fetch_finance_data_news(client: httpx.AsyncClient, symbol: str,
                                    hours: int, min_weight: float,
                                    stock_name: str | None = None) -> list[NewsItem]:
    """从 finance-data /news/articles 拉财联社 + 东方财富等权威源

    重要：news_eastmoney collector 有把宏观新闻无差别标到 watchlist 的 bug。
    在客户端二次过滤：标题或正文必须包含股票名/代码才采信。
    """
    r = await client.get(
        f"{FINANCE_DATA_URL}/api/v1/news/articles",
        params={"symbol": symbol, "hours": hours, "min_weight": min_weight, "limit": 100},
        headers=_HTTPX_HEADERS,
        timeout=_PER_SOURCE_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()

    # 关键词构造：股票名（含简称）+ 6 位代码
    code_6 = symbol.split(".")[0]
    name_short = (stock_name or "").replace("股份", "").replace("有限公司", "").replace("集团", "").strip()
    keywords = {code_6}
    if stock_name:
        keywords.add(stock_name)
    if name_short and name_short != stock_name:
        keywords.add(name_short)

    items = []
    filtered_out = 0
    for it in data.get("items", []):
        try:
            title   = (it.get("title") or "")
            content = (it.get("content") or "")
            blob    = title + " " + content
            # 客户端过滤：必须命中股票名/代码之一
            if keywords and not any(kw in blob for kw in keywords if kw):
                filtered_out += 1
                continue

            pt_str = it.get("published_at") or ""
            pt = datetime.fromisoformat(pt_str.replace("Z", "+00:00")) if pt_str else datetime.now()
            # 统一去掉 tzinfo（避免和 akshare 来源的 naive datetime 在 sort 时报错）
            if pt and pt.tzinfo is not None:
                pt = pt.replace(tzinfo=None)
            items.append(NewsItem(
                title         = title,
                content       = content or None,
                publish_time  = pt,
                source_key    = it.get("source_key") or "unknown",
                source_name   = it.get("source") or "unknown",
                source_tier   = it.get("source_tier") or "media_general",
                source_weight = float(it.get("source_weight") or 0.5),
                url           = it.get("url"),
                raw_meta      = it.get("raw_meta"),
            ))
        except Exception as e:
            log.warning("parse news_item failed: %s | %s", e, it)

    if filtered_out > 0:
        log.info("finance_data_news %s: filtered out %d unrelated items (keywords=%s)",
                 symbol, filtered_out, keywords)
    return items


async def _fetch_cninfo(client: httpx.AsyncClient, symbol: str, days: int) -> list[NewsItem]:
    """从 finance-data /cninfo/announcements 拉巨潮公告"""
    r = await client.get(
        f"{FINANCE_DATA_URL}/api/v1/cninfo/announcements",
        params={"symbol": symbol, "days": days, "limit": 30},
        headers=_HTTPX_HEADERS,
        timeout=_PER_SOURCE_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    items = []
    for it in data.get("items", []):
        try:
            pt_str = it.get("publish_time") or ""
            pt = datetime.fromisoformat(pt_str.replace("Z", "+00:00")) if pt_str else datetime.now()
            # 统一去掉 tzinfo（避免和 akshare 来源的 naive datetime 在 sort 时报错）
            if pt and pt.tzinfo is not None:
                pt = pt.replace(tzinfo=None)
            items.append(NewsItem(
                title         = it.get("title", ""),
                content       = it.get("parsed_facts") and str(it["parsed_facts"]) or None,
                publish_time  = pt,
                source_key    = "cninfo",
                source_name   = "巨潮资讯",
                source_tier   = "regulatory",
                source_weight = 0.95,
                url           = it.get("pdf_url"),
                importance    = it.get("importance"),
                raw_meta      = {"category": it.get("category")},
            ))
        except Exception as e:
            log.warning("parse cninfo failed: %s | %s", e, it)
    return items


# ─── akshare 源（同步包到线程池）────────────────────────────────────────

def _akshare_safe(fn_name: str, *args, **kwargs):
    """同步调用 akshare，统一 try/except"""
    try:
        import akshare as ak
    except ImportError:
        log.error("akshare not installed in hermes-api venv")
        return None
    try:
        fn = getattr(ak, fn_name)
        return fn(*args, **kwargs)
    except Exception as e:
        log.warning("akshare.%s failed: %s", fn_name, e)
        return None


async def _fetch_northbound(symbol: str) -> dict:
    """北向资金（akshare stock_hsgt_individual_em）

    symbol 格式：去掉后缀的 6 位 → "300750"
    返回：{ today_net: 元, 30d_history: [...] }
    """
    code = symbol.split(".")[0]
    df = await asyncio.to_thread(_akshare_safe, "stock_hsgt_individual_em", symbol=code)
    if df is None or df.empty:
        return {}
    try:
        latest = df.iloc[-1]
        return {
            "today_date":    str(latest.get("持股日期") or latest.get("日期", "")),
            "today_net_buy": float(latest.get("当日成交净买额", 0) or 0),
            "hold_market_cap": float(latest.get("持股市值", 0) or 0),
            "history_30d":   [
                {"date": str(r.get("持股日期", "")), "net_buy": float(r.get("当日成交净买额", 0) or 0)}
                for _, r in df.tail(30).iterrows()
            ],
        }
    except Exception as e:
        log.warning("parse northbound failed: %s", e)
        return {}


async def _fetch_longhubang(symbol: str) -> dict:
    """龙虎榜（akshare stock_lhb_stock_detail_em）

    只拉近 3 天数据
    """
    code = symbol.split(".")[0]
    today = datetime.now().strftime("%Y%m%d")
    df = await asyncio.to_thread(_akshare_safe, "stock_lhb_stock_detail_em", symbol=code, date=today)
    if df is None or df.empty:
        return {}
    try:
        # 机构席位净买卖
        inst_rows = df[df.get("营业部名称", "").astype(str).str.contains("机构专用|机构席位", na=False)] if "营业部名称" in df.columns else df.iloc[:0]
        inst_net = 0.0
        for _, r in inst_rows.iterrows():
            inst_net += float(r.get("买入金额", 0) or 0) - float(r.get("卖出金额", 0) or 0)
        return {
            "date":          today,
            "inst_net":      inst_net,
            "total_rows":    len(df),
            "is_inst_selling": inst_net < -100_000_000,   # 机构净卖 1 亿+ 标记
            "is_inst_buying":  inst_net > 100_000_000,
        }
    except Exception as e:
        log.warning("parse longhubang failed: %s", e)
        return {}


async def _fetch_akshare_cninfo_realtime(symbol: str) -> list[NewsItem]:
    """实时调 akshare 按 symbol 拉巨潮公告（非 watchlist 票的关键兜底）

    用 stock_zh_a_disclosure_report_cninfo 按代码精确查最近 30 天公告。
    """
    code_6 = symbol.split(".")[0]
    if not code_6 or not code_6.isdigit() or len(code_6) != 6:
        return []

    end_dt   = datetime.now()
    start_dt = end_dt - timedelta(days=30)

    df = await asyncio.to_thread(
        _akshare_safe,
        "stock_zh_a_disclosure_report_cninfo",
        symbol     = code_6,
        market     = "沪深京",
        start_date = start_dt.strftime("%Y%m%d"),
        end_date   = end_dt.strftime("%Y%m%d"),
    )
    if df is None or df.empty:
        return []

    # 简化的重要性分级（同 news_cninfo.py 的关键词）
    CRITICAL = ["停牌", "重大重组", "立案调查", "*ST", "退市", "实际控制人变更", "破产"]
    HIGH     = ["业绩预告", "业绩快报", "增发", "减持", "关联交易", "重大合同", "诉讼", "处罚", "回购"]
    MEDIUM   = ["定期报告", "年度报告", "季度报告", "半年度报告", "股东大会", "董事会决议"]

    def _level(title: str) -> str:
        for kw in CRITICAL:
            if kw in title: return "CRITICAL"
        for kw in HIGH:
            if kw in title: return "HIGH"
        for kw in MEDIUM:
            if kw in title: return "MEDIUM"
        return "LOW"

    items = []
    for _, r in df.iterrows():
        try:
            title = str(r.get("公告标题") or r.get("标题") or "").strip()
            if not title:
                continue
            pt_str = str(r.get("公告时间") or r.get("公告日期") or "").strip()
            try:
                pt = datetime.strptime(pt_str[:19], "%Y-%m-%d %H:%M:%S") if len(pt_str) > 10 \
                     else datetime.strptime(pt_str[:10], "%Y-%m-%d")
            except Exception:
                pt = datetime.now()
            pdf_url = str(r.get("公告链接") or r.get("网址") or "").strip()

            items.append(NewsItem(
                title         = title,
                content       = None,
                publish_time  = pt,
                source_key    = "cninfo_realtime",
                source_name   = "巨潮资讯（实时）",
                source_tier   = "regulatory",
                source_weight = 0.95,
                url           = pdf_url or None,
                importance    = _level(title),
            ))
        except Exception:
            continue

    log.info("akshare_cninfo_realtime %s: %d announcements", symbol, len(items))
    return items[:30]


# 媒体源分级（决定 eastmoney_realtime 实际权重）
# 大牌媒体（权威级）权重 0.70
_TIER_PREMIUM_SOURCES = {
    "证券时报", "上海证券报", "中国证券报", "证券日报", "财新",
    "经济观察报", "21世纪经济报道", "财联社", "东方财富网", "东方财富Choice数据",
    "界面新闻", "第一财经", "每日经济新闻",
}
# 自媒体/营销号（低权重 0.30）— 名字含「号」或在黑名单
_LOWWEIGHT_SOURCE_KEYWORDS = ["号", "自媒体", "营销", "推广", "解读", "观察家", "评论"]


def _grade_eastmoney_source(source: str) -> float:
    """根据 source 字段给东财个股新闻评权重"""
    if not source:
        return 0.45
    s = source.strip()
    # 大牌权威源
    for premium in _TIER_PREMIUM_SOURCES:
        if premium in s:
            return 0.70
    # 自媒体特征（含「号」「评论」「自媒体」等）
    if any(kw in s for kw in _LOWWEIGHT_SOURCE_KEYWORDS):
        return 0.30
    # 其他一般源
    return 0.45


async def _fetch_akshare_stock_news(stock_name: str, stock_code: str) -> list[NewsItem]:
    """实时调 akshare stock_news_em 拉个股新闻（finance-data 数据缺失时的兜底）

    特别针对非 watchlist 票：finance-data collector 没采过 → news_item 表 0 条
    → 这里实时拉 + 客户端按股票名/代码再过滤一次确保相关
    """
    if not stock_name:
        return []
    df = await asyncio.to_thread(_akshare_safe, "stock_news_em", symbol=stock_name)
    if df is None or df.empty:
        return []

    code_6 = stock_code.split(".")[0]
    name_short = stock_name.replace("股份", "").replace("有限公司", "").replace("集团", "").strip()
    keywords = {code_6, stock_name}
    if name_short and name_short != stock_name:
        keywords.add(name_short)

    items = []
    try:
        for _, r in df.iterrows():
            try:
                title = str(r.get("新闻标题") or r.get("标题") or "").strip()
                content = str(r.get("新闻内容") or r.get("内容") or "")[:500]
                if not title:
                    continue
                # 客户端二次过滤：标题或内容必须包含股票名/代码
                blob = title + " " + content
                if not any(kw in blob for kw in keywords if kw):
                    continue

                pt_str = str(r.get("发布时间") or "").strip()
                try:
                    pt = datetime.strptime(pt_str[:19], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    pt = datetime.now()
                url = str(r.get("新闻链接") or r.get("链接") or "")
                source = str(r.get("文章来源") or r.get("来源") or "东方财富").strip()
                # 按 source 字段动态分级权重（让低质源真的出现在原始流里）
                graded_weight = _grade_eastmoney_source(source)
                graded_tier   = ("media_premium" if graded_weight >= 0.7
                                 else "social"   if graded_weight <= 0.35
                                 else "media_general")

                items.append(NewsItem(
                    title         = title,
                    content       = content or None,
                    publish_time  = pt,
                    source_key    = "eastmoney_realtime",
                    source_name   = source,
                    source_tier   = graded_tier,
                    source_weight = graded_weight,
                    url           = url or None,
                ))
            except Exception:
                continue
    except Exception as e:
        log.warning("parse akshare stock news failed: %s", e)

    log.info("akshare_stock_news %s/%s: %d items (after filter)", stock_name, stock_code, len(items))
    return items[:30]


async def _fetch_market_indices() -> dict:
    """大盘 4 个指数（沪深300/上证/创业板/科创50）"""
    df = await asyncio.to_thread(_akshare_safe, "stock_zh_index_spot_em", symbol="沪深重要指数")
    if df is None or df.empty:
        return {}
    key_map = {
        "000001": "上证综指",
        "000300": "沪深300",
        "399006": "创业板指",
        "000688": "科创50",
    }
    out = {}
    try:
        for _, r in df.iterrows():
            code = str(r.get("代码", ""))
            if code in key_map:
                out[key_map[code]] = float(r.get("涨跌幅") or 0)
    except Exception as e:
        log.warning("parse indices failed: %s", e)
    return out


async def _fetch_industry_sector(symbol: str) -> dict:
    """个股所属行业 + 板块同步性（复用 attribution.fetch_market_context 的逻辑）"""
    code = symbol.split(".")[0]

    # 行业归属
    info = await asyncio.to_thread(_akshare_safe, "stock_individual_info_em", symbol=code)
    if info is None or info.empty:
        return {}
    industry = None
    try:
        for _, r in info.iterrows():
            if r.get("item") == "行业":
                industry = str(r.get("value") or "").strip()
                break
    except Exception:
        pass
    if not industry:
        return {}

    # 板块同步性
    df = await asyncio.to_thread(_akshare_safe, "stock_board_industry_summary_ths")
    if df is None or df.empty:
        return {"industry": industry}
    try:
        m = df[df["板块"].str.contains(industry, na=False)]
        if m.empty:
            return {"industry": industry}
        row = m.iloc[0]
        up    = int(row.get("上涨家数", 0) or 0)
        down  = int(row.get("下跌家数", 0) or 0)
        total = up + down
        return {
            "industry":   industry,
            "sector_name": str(row.get("板块", industry)),
            "change_pct": float(row.get("涨跌幅", 0) or 0),
            "up_count":   up,
            "down_count": down,
            "total":      total,
            "down_ratio": (down / total) if total else 0,
        }
    except Exception as e:
        log.warning("parse industry sector failed: %s", e)
        return {"industry": industry}


# ─── 新增数据源 ────────────────────────────────────────────────────────

async def _fetch_caixin_news(stock_name: str, symbol: str) -> list[NewsItem]:
    """财讯个股新闻（akshare stock_news_main_cx）"""
    if not stock_name:
        return []
    df = await asyncio.to_thread(_akshare_safe, "stock_news_main_cx", symbol=stock_name)
    if df is None or df.empty:
        return []

    code_6 = symbol.split(".")[0]
    name_short = stock_name.replace("股份", "").replace("有限公司", "").replace("集团", "").strip()
    keywords = {code_6, stock_name}
    if name_short and name_short != stock_name:
        keywords.add(name_short)

    items = []
    for _, r in df.iterrows():
        try:
            title = str(r.get("新闻标题") or r.get("标题") or r.get("title") or "").strip()
            if not title:
                continue
            content = str(r.get("新闻内容") or r.get("内容") or r.get("content") or "")[:500]
            blob = title + " " + content
            if keywords and not any(kw in blob for kw in keywords if kw):
                continue
            pt_str = str(r.get("发布时间") or r.get("时间") or r.get("date") or "").strip()
            try:
                pt = datetime.strptime(pt_str[:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                pt = datetime.now()
            items.append(NewsItem(
                title=title, content=content or None, publish_time=pt,
                source_key="caixin_news", source_name="财讯",
                source_tier="media_general", source_weight=0.55,
                url=str(r.get("新闻链接") or r.get("url") or "").strip() or None,
            ))
        except Exception:
            continue
    log.info("caixin_news %s: %d items", stock_name, len(items))
    return items[:20]


def _parse_macro_news_df(df, source_key: str, source_name: str,
                          weight: float, keywords: set) -> list[NewsItem]:
    """通用：解析 akshare 宏观新闻 DataFrame（news_cctv / news_economic_baidu）"""
    items = []
    for _, r in df.iterrows():
        try:
            title = str(
                r.get("新闻标题") or r.get("标题") or r.get("title") or
                r.get("新闻") or r.get("内容") or ""
            ).strip()
            if not title:
                continue
            content = str(r.get("新闻内容") or r.get("内容") or r.get("content") or "")[:400]
            blob = title + " " + content
            if keywords and not any(kw in blob for kw in keywords if kw):
                continue
            pt_str = str(
                r.get("发布时间") or r.get("时间") or r.get("date") or
                r.get("日期") or r.get("pub_time") or ""
            ).strip()
            try:
                pt = datetime.strptime(pt_str[:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                try:
                    pt = datetime.strptime(pt_str[:10], "%Y-%m-%d")
                except Exception:
                    pt = datetime.now()
            items.append(NewsItem(
                title=title, content=content or None, publish_time=pt,
                source_key=source_key, source_name=source_name,
                source_tier="media_premium" if weight >= 0.70 else "media_general",
                source_weight=weight,
                url=str(r.get("链接") or r.get("url") or "").strip() or None,
            ))
        except Exception:
            continue
    return items


async def _fetch_cctv_news(stock_name: str, symbol: str) -> list[NewsItem]:
    """央视新闻（宏观/政策，按股票关键词过滤）"""
    df = await asyncio.to_thread(_akshare_safe, "news_cctv")
    if df is None or df.empty:
        return []
    code_6 = symbol.split(".")[0]
    name_short = stock_name.replace("股份", "").replace("有限公司", "").replace("集团", "").strip()
    keywords = {code_6, stock_name}
    if name_short and name_short != stock_name:
        keywords.add(name_short)
    items = _parse_macro_news_df(df, "cctv_news", "央视新闻", 0.75, keywords)
    log.info("cctv_news %s: %d items", stock_name, len(items))
    return items[:10]


async def _fetch_baidu_finance(stock_name: str, symbol: str) -> list[NewsItem]:
    """百度财经要闻（宏观叙事，按股票关键词过滤）"""
    df = await asyncio.to_thread(_akshare_safe, "news_economic_baidu")
    if df is None or df.empty:
        return []
    code_6 = symbol.split(".")[0]
    name_short = stock_name.replace("股份", "").replace("有限公司", "").replace("集团", "").strip()
    keywords = {code_6, stock_name}
    if name_short and name_short != stock_name:
        keywords.add(name_short)
    items = _parse_macro_news_df(df, "baidu_finance", "百度财经", 0.55, keywords)
    log.info("baidu_finance %s: %d items", stock_name, len(items))
    return items[:10]


async def _fetch_cls_rss(stock_name: str, symbol: str) -> list[NewsItem]:
    """财联社 RSS 实时电报（httpx + XML 解析，按关键词过滤）"""
    import xml.etree.ElementTree as ET

    code_6 = symbol.split(".")[0]
    name_short = stock_name.replace("股份", "").replace("有限公司", "").replace("集团", "").strip()
    keywords = {code_6, stock_name}
    if name_short and name_short != stock_name:
        keywords.add(name_short)

    try:
        async with httpx.AsyncClient(timeout=_PER_SOURCE_TIMEOUT) as client:
            r = await client.get(
                "https://www.cls.cn/rss",
                headers={"User-Agent": "Mozilla/5.0 (compatible; hunter-sentinel/1.0)"},
            )
            r.raise_for_status()
        root = ET.fromstring(r.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall(".//item") or root.findall(".//atom:entry", ns)
        items = []
        for entry in entries:
            def _txt(tag_rss, tag_atom=None):
                el = entry.find(tag_rss)
                if el is None and tag_atom:
                    el = entry.find(tag_atom, ns)
                return (el.text or "").strip() if el is not None else ""
            title   = _txt("title", "atom:title")
            content = _txt("description", "atom:summary") or _txt("content", "atom:content")
            link    = _txt("link", "atom:link")
            pub_raw = _txt("pubDate", "atom:updated") or _txt("updated")
            if not title:
                continue
            blob = title + " " + content[:300]
            if keywords and not any(kw in blob for kw in keywords if kw):
                continue
            try:
                from email.utils import parsedate_to_datetime
                pt = parsedate_to_datetime(pub_raw).replace(tzinfo=None)
            except Exception:
                try:
                    pt = datetime.fromisoformat(pub_raw[:19])
                except Exception:
                    pt = datetime.now()
            items.append(NewsItem(
                title=title, content=content[:400] or None, publish_time=pt,
                source_key="cls_rss", source_name="财联社RSS",
                source_tier="media_premium", source_weight=0.80,
                url=link or None,
            ))
        log.info("cls_rss %s: %d items", stock_name, len(items))
        return items[:15]
    except Exception as e:
        log.warning("cls_rss failed: %s", e)
        return []


async def _fetch_market_sentiment_index() -> dict:
    """大盘情绪指数（akshare index_news_sentiment_scope）→ market_data["sentiment"]"""
    df = await asyncio.to_thread(_akshare_safe, "index_news_sentiment_scope")
    if df is None or df.empty:
        return {}
    try:
        latest = df.iloc[-1]
        score_col = next(
            (c for c in df.columns if "情绪" in c or "sentiment" in c.lower() or "score" in c.lower()),
            df.columns[-1] if len(df.columns) > 1 else None,
        )
        score = float(latest[score_col]) if score_col else 0.0
        date_col = next((c for c in df.columns if "日期" in c or "date" in c.lower()), df.columns[0])
        return {
            "date":  str(latest.get(date_col, "")),
            "score": round(score, 4),
            "level": "偏多" if score > 0.1 else ("偏空" if score < -0.1 else "中性"),
        }
    except Exception as e:
        log.warning("parse market_sentiment failed: %s", e)
        return {}


async def _fetch_hackernews(stock_name: str, symbol: str) -> list[NewsItem]:
    """Hacker News Algolia API（全球科技情绪，仅科技/半导体股有参考价值）"""
    TECH_KEYWORDS = {
        "半导体", "芯片", "AI", "人工智能", "算力", "大模型", "显卡",
        "nvidia", "英伟达", "intel", "tsmc", "台积电", "华为",
        "科技", "互联网", "软件", "云计算", "数据中心",
    }
    name_lower = stock_name.lower()
    is_tech = any(kw.lower() in name_lower for kw in TECH_KEYWORDS)
    if not is_tech:
        return []

    try:
        since_ts = int((datetime.now() - timedelta(days=30)).timestamp())
        async with httpx.AsyncClient(timeout=_PER_SOURCE_TIMEOUT) as client:
            r = await client.get(
                "https://hn.algolia.com/api/v1/search",
                params={
                    "query": stock_name,
                    "tags": "story",
                    "numericFilters": f"created_at_i>{since_ts}",
                    "hitsPerPage": 10,
                },
            )
            r.raise_for_status()
        hits = r.json().get("hits", [])
        items = []
        for h in hits:
            title = str(h.get("title") or "").strip()
            if not title:
                continue
            ts = h.get("created_at_i", 0)
            pt = datetime.fromtimestamp(ts) if ts else datetime.now()
            items.append(NewsItem(
                title=title,
                content=str(h.get("story_text") or "")[:300] or None,
                publish_time=pt,
                source_key="hackernews", source_name="Hacker News",
                source_tier="media_general", source_weight=0.40,
                url=h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID', '')}",
            ))
        log.info("hackernews %s: %d items", stock_name, len(items))
        return items
    except Exception as e:
        log.warning("hackernews failed: %s", e)
        return []


async def _fetch_polymarket(stock_name: str, symbol: str) -> list[NewsItem]:
    """Polymarket 预测市场（宏观/关税事件押注概率）"""
    MACRO_KEYWORDS = {
        "出口", "关税", "贸易战", "美联储", "利率", "地缘",
        "中美", "制裁", "供应链", "稀土", "半导体",
    }
    name_lower = stock_name.lower()
    is_macro_relevant = any(kw in name_lower for kw in MACRO_KEYWORDS)
    query = stock_name if is_macro_relevant else f"{stock_name} China stock"
    try:
        async with httpx.AsyncClient(timeout=_PER_SOURCE_TIMEOUT) as client:
            r = await client.get(
                "https://gamma-api.polymarket.com/public-search",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            r.raise_for_status()
        data = r.json()
        markets = data if isinstance(data, list) else data.get("markets", [])
        items = []
        for m in markets[:5]:
            question = str(m.get("question") or m.get("title") or "").strip()
            if not question:
                continue
            prob = m.get("probability") or m.get("bestAsk") or m.get("lastTradePrice")
            prob_str = f"（预测概率 {float(prob)*100:.0f}%）" if prob else ""
            items.append(NewsItem(
                title=f"[预测市场] {question}{prob_str}",
                content=str(m.get("description") or "")[:300] or None,
                publish_time=datetime.now(),
                source_key="polymarket", source_name="Polymarket",
                source_tier="alternative", source_weight=0.60,
                url=f"https://polymarket.com/event/{m.get('slug', '')}",
            ))
        log.info("polymarket %s: %d markets", stock_name, len(items))
        return items
    except Exception as e:
        log.warning("polymarket failed: %s", e)
        return []


# ─── 主入口 UnifiedFetcher ─────────────────────────────────────────────

class UnifiedFetcher:
    """多源并发抓取器，调一次 fetch_all 拿到全部数据"""

    async def fetch_all(self, symbol: str, hours: int = 72,
                        stock_name: str | None = None,
                        include_contrarian: bool = True) -> FetchResult:
        """
        Args:
            symbol:    finance-data symbol 格式（如 "300750.SZ"）
            hours:     新闻时间窗口（小时），默认 72 小时
            stock_name: 股票中文名（用于客户端二次过滤无关新闻）
            include_contrarian:  是否调用 F8 对立面搜索（默认 True）
        """
        result = FetchResult()
        async with httpx.AsyncClient() as client:
            # 所有任务并发跑
            _sn = stock_name or ""
            tasks = {
                "news_finance_data":  asyncio.create_task(_fetch_finance_data_news(client, symbol, hours, 0.0, stock_name)),
                "cninfo":             asyncio.create_task(_fetch_cninfo(client, symbol, 7)),
                "cninfo_realtime":    asyncio.create_task(_fetch_akshare_cninfo_realtime(symbol)),
                "akshare_news":       asyncio.create_task(_fetch_akshare_stock_news(_sn, symbol)),
                "northbound":         asyncio.create_task(_fetch_northbound(symbol)),
                "longhubang":         asyncio.create_task(_fetch_longhubang(symbol)),
                "market_indices":     asyncio.create_task(_fetch_market_indices()),
                "industry_sector":    asyncio.create_task(_fetch_industry_sector(symbol)),
                # 新增 7 路
                "caixin_news":        asyncio.create_task(_fetch_caixin_news(_sn, symbol)),
                "cctv_news":          asyncio.create_task(_fetch_cctv_news(_sn, symbol)),
                "baidu_finance":      asyncio.create_task(_fetch_baidu_finance(_sn, symbol)),
                "cls_rss":            asyncio.create_task(_fetch_cls_rss(_sn, symbol)),
                "market_sentiment":   asyncio.create_task(_fetch_market_sentiment_index()),
                "hackernews":         asyncio.create_task(_fetch_hackernews(_sn, symbol)),
                "polymarket":         asyncio.create_task(_fetch_polymarket(_sn, symbol)),
            }

            # 全局 30s 超时
            try:
                done, pending = await asyncio.wait(
                    tasks.values(), timeout=_GLOBAL_TIMEOUT,
                    return_when=asyncio.ALL_COMPLETED,
                )
                for p in pending:
                    p.cancel()
            except Exception as e:
                log.warning("fetch_all timeout/error: %s", e)

            # 收集结果
            for name, task in tasks.items():
                if not task.done():
                    result.failed_sources.append({"source": name, "reason": "timeout"})
                    continue
                try:
                    val = task.result()
                except Exception as e:
                    result.failed_sources.append({"source": name, "reason": str(e)})
                    continue

                result.successful_sources.append(name)
                _NEWS_SOURCES = {
                    "news_finance_data", "cninfo", "cninfo_realtime", "akshare_news",
                    "caixin_news", "cctv_news", "baidu_finance", "cls_rss",
                    "hackernews", "polymarket",
                }
                if name in _NEWS_SOURCES:
                    result.news_items.extend(val or [])
                elif name in ("northbound", "longhubang"):
                    result.capital_flow_data[name] = val or {}
                elif name == "market_indices":
                    result.market_data["indices"] = val or {}
                elif name == "industry_sector":
                    result.market_data["sector"] = val or {}
                elif name == "market_sentiment":
                    result.market_data["sentiment"] = val or {}

        # 按权重 + 时间排序
        result.news_items.sort(
            key=lambda n: (n.source_weight, n.publish_time),
            reverse=True,
        )

        log.info(
            "UnifiedFetcher %s: news=%d success=%d failed=%d coverage=%.2f authoritative=%d",
            symbol, len(result.news_items),
            len(result.successful_sources), len(result.failed_sources),
            result.coverage_score, result.authoritative_count,
        )
        return result
