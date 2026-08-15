"""数据源注册表 —— 三层能力模型里的**数据源层**。

`_14` §4 / §6 Step B。用户原话:「数据源按 A股/港股/美股 分,**每一类数据都是
一个 api**」。这份注册表就是把那句话落成数据结构:一条记录 = 一个
(市场, 数据类型, 具体 API)。

**为什么要有它**:在这之前,"开源版到底能拿到什么数据"没有任何地方说得清 ——
代码里是十几个散落的 `httpx.get`,UI 上一个字都没有。用户查美股查不出来,
看到的是空列表,不知道是没数据、没配 key、还是根本没通道。三种情况的处理方式
完全不同,却长得一模一样(`_13` §3.2 说的静默失败)。

**available 与 configured 是两件事**,不要混:
  · `available`  —— **通道在开源版存不存在**。静态事实,跟用户配了什么无关。
                    美股 K线走 `gm/findata_db.py` 直连数据库,用户拿不到
                    `FINDATA_DB_URL` → available=False。这不是"没配",是**没门**。
  · `configured` —— 用户有没有给够凭证。运行时算。
  · `health`     —— 真实调用出来的成功率,见 `source_health`。
三者合成 UI 上那个状态点,逻辑在 `status_of()`。

**这份表必须能被验证**:`_13` §3.1 的教训是"凡是某某清单必须有自动校验",
否则清单和现实各漂各的。这里的约束是 —— 注册了 endpoint 的源必须能探活,
`scripts/check_sources.py` 负责这件事。
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from enum import Enum


class Market(str, Enum):
    A = "a"
    HK = "hk"
    US = "us"
    GLOBAL = "global"


MARKET_LABEL = {
    Market.A: "A股", Market.HK: "港股",
    Market.US: "美股", Market.GLOBAL: "全球/跨市场",
}

# UI 上市场的排列顺序 —— A股在最前是因为开源版目前能力最全的就是它
MARKET_ORDER = [Market.A, Market.HK, Market.US, Market.GLOBAL]


class DataKind(str, Enum):
    QUOTE = "quote"            # 实时行情
    KLINE = "kline"            # K线
    NEWS = "news"              # 新闻资讯
    ANNOUNCE = "announce"      # 公告
    FINANCIAL = "financial"    # 财务报表
    CAPITAL = "capital"        # 资金流向
    HOLDER = "holder"          # 股东与治理
    RESEARCH = "research"      # 券商研报
    VALUATION = "valuation"    # 估值与对标
    FORECAST = "forecast"      # 预测
    INTEL = "intel"            # 情报
    GEO = "geo"                # 地缘


KIND_LABEL = {
    DataKind.QUOTE: "实时行情", DataKind.KLINE: "K线", DataKind.NEWS: "新闻资讯",
    DataKind.ANNOUNCE: "公告", DataKind.FINANCIAL: "财务报表", DataKind.CAPITAL: "资金流向",
    DataKind.HOLDER: "股东与治理", DataKind.RESEARCH: "券商研报", DataKind.VALUATION: "估值对标",
    DataKind.FORECAST: "预测", DataKind.INTEL: "情报", DataKind.GEO: "地缘",
}


class SourceTier(str, Enum):
    OFFICIAL = "official"            # 平台自建库 · 需 key
    FREE_STABLE = "free_stable"      # 免 key 且实测稳定
    FREE_UNSTABLE = "free_unstable"  # 免 key 但容器内经常打不通
    PREMIUM = "premium"              # 需用户自备第三方 key


@dataclass
class DataSource:
    key: str                          # 稳定 ID · 也是 source_health 的主键
    name: str
    market: Market
    kind: DataKind
    provider: str                     # finance-data / yahoo / akshare / findata-db / kronos / truesource
    endpoint: str = ""                # 相对上游的路径 · 空 = 不是 HTTP 取数
    tier: SourceTier = SourceTier.OFFICIAL
    volume_hint: str = ""             # "960万条分钟线" —— UI 直接显示,让用户感知家底
    requires_key: bool = True
    available: bool = True            # False = 有数据但开源版走不到
    unavailable_reason: str = ""
    note: str = ""
    weight: float = 1.0               # 多源合并时的采信权重 · Step D 用
    used_by: list[str] = field(default_factory=list)   # 哪些工具/SKILL 依赖它


# ══════════════════════════════════════════════════════════════
# A股 · finance-data 官方库,经 hunter 网关,一把 hunt_tools_ key 全通
# 端点取自 services/finance_data_client.py 的真实调用(不是照文档抄的)
# ══════════════════════════════════════════════════════════════
_A: list[DataSource] = [
    DataSource("a.quote", "实时行情", Market.A, DataKind.QUOTE, "finance-data",
               "/api/v1/quote/{symbol}", volume_hint="全市场 5000+ 只",
               note="含五档盘口", used_by=["watchlist_stock_quickview"]),
    DataSource("a.kline", "K线", Market.A, DataKind.KLINE, "finance-data",
               "/api/v1/kline/{symbol}", note="1m/5m/1d · 前复权"),
    DataSource("a.orderbook", "盘口", Market.A, DataKind.QUOTE, "finance-data",
               "/api/v1/orderbook/{symbol}"),
    DataSource("a.news", "个股新闻", Market.A, DataKind.NEWS, "finance-data",
               "/api/v1/news"),
    DataSource("a.news_articles", "新闻全文", Market.A, DataKind.NEWS, "finance-data",
               "/api/v1/news/articles", note="带正文 · 深度分析用"),
    DataSource("a.announce", "巨潮公告", Market.A, DataKind.ANNOUNCE, "finance-data",
               "/api/v1/cninfo/announcements"),
    DataSource("a.financial", "财务报表", Market.A, DataKind.FINANCIAL, "finance-data",
               "/api/v1/financial/{symbol}"),
    DataSource("a.money_flow", "资金流向", Market.A, DataKind.CAPITAL, "finance-data",
               "/api/v1/money_flow/{symbol}"),
    DataSource("a.lhb", "龙虎榜", Market.A, DataKind.CAPITAL, "finance-data",
               "/api/v1/lhb/{symbol}"),
    DataSource("a.fund_holders", "基金持仓", Market.A, DataKind.HOLDER, "finance-data",
               "/api/v1/fund_holders/{symbol}"),
    DataSource("a.governance", "公司治理", Market.A, DataKind.HOLDER, "finance-data",
               "/api/v1/governance/{symbol}", note="高管 · 股权结构"),
    DataSource("a.research", "券商研报", Market.A, DataKind.RESEARCH, "finance-data",
               "/api/v1/research/{symbol}"),
    DataSource("a.peers", "同业对标", Market.A, DataKind.VALUATION, "finance-data",
               "/api/v1/peers/{symbol}"),
    # 免 key 兜底 —— 但要如实说明它的问题
    DataSource("a.akshare", "AKShare(免 key)", Market.A, DataKind.QUOTE, "akshare",
               tier=SourceTier.FREE_UNSTABLE, requires_key=False,
               note="设 DATA_SOURCE_PROVIDER=akshare 启用。容器内经常连不通,"
                    "且是全局切换(会一并影响港美股),仅作没有 key 时的兜底"),
]

# ══════════════════════════════════════════════════════════════
# 港股
# 实测(2026-08-15,容器内):
#   · 行情走 Yahoo chart 接口 —— **免 key 真能出数**(00700 → 440.0 HKD)
#   · 其余全部走 gm/findata_db.py 直连数据库 —— 开源版拿不到 FINDATA_DB_URL
# ══════════════════════════════════════════════════════════════
_HK: list[DataSource] = [
    DataSource("hk.quote", "港股行情", Market.HK, DataKind.QUOTE, "yahoo",
               "/api/gm/quote/hk/{code}", tier=SourceTier.FREE_STABLE,
               requires_key=False, volume_hint="全市场",
               note="Yahoo 免费源 · 延迟约 15 分钟 · Redis 缓存 60s"),
    DataSource("hk.kline", "港股K线", Market.HK, DataKind.KLINE, "yahoo",
               "/api/gm/kline/hk/{code}", tier=SourceTier.FREE_STABLE,
               requires_key=False, note="同上 · 日K缓存 30min"),
    DataSource("hk.master", "港股主表", Market.HK, DataKind.QUOTE, "findata-db",
               volume_hint="2,817 只", available=False,
               unavailable_reason="走直连数据库(FINDATA_DB_URL),开源版无此凭证",
               note="缺它只影响中文名与每手股数,行情本身不受影响"),
    DataSource("hk.kline_db", "港股历史K线(库)", Market.HK, DataKind.KLINE, "findata-db",
               volume_hint="日线 66.7万 · 5分钟 59万", available=False,
               unavailable_reason="走直连数据库,开源版无此凭证"),
    DataSource("hk.financial", "港股财报", Market.HK, DataKind.FINANCIAL, "findata-db",
               "/api/gm/fundamentals/hk/{code}", available=False,
               unavailable_reason="走直连数据库,开源版无此凭证"),
    DataSource("hk.filings", "港交所公告", Market.HK, DataKind.ANNOUNCE, "findata-db",
               volume_hint="1,152 条", available=False,
               unavailable_reason="走直连数据库,开源版无此凭证"),
    DataSource("hk.southbound", "南向资金", Market.HK, DataKind.CAPITAL, "findata-db",
               volume_hint="每日累积", available=False,
               unavailable_reason="走直连数据库,开源版无此凭证"),
    DataSource("hk.ah_premium", "AH 溢价", Market.HK, DataKind.VALUATION, "findata-db",
               available=False,
               unavailable_reason="走直连数据库,开源版无此凭证"),
]

# ══════════════════════════════════════════════════════════════
# 美股
# 实测(2026-08-15,容器内):
#   · /api/gm/quote/us/AAPL → 404 not_found(走 findata_db,库连不上)
#   · /api/gm/kline/us/AAPL → 200 但 bars=[] ← **最坏的一种**:装作成功
#   · yfinance 库 → 拿不到(它打的 v7/quoteSummary 接口被 Yahoo 拒了)
#   · 但 query1.finance.yahoo.com/v8/finance/chart/AAPL → 200,305.93 USD
#     ↑ 港股用的就是这个接口。**美股行情其实免 key 可用,只是代码没走这条路。**
# ══════════════════════════════════════════════════════════════
_US: list[DataSource] = [
    DataSource("us.quote", "美股行情", Market.US, DataKind.QUOTE, "findata-db",
               "/api/gm/quote/us/{code}", volume_hint="13,019 只主表", available=False,
               unavailable_reason="当前实现读 us_kline 最新bar(直连数据库),开源版无此凭证",
               note="可修:改走港股同款 Yahoo chart 接口即免 key 可用(实测已验证)"),
    DataSource("us.kline", "美股K线", Market.US, DataKind.KLINE, "findata-db",
               "/api/gm/kline/us/{code}",
               volume_hint="分钟 960万 · 5分钟 870万 · 日线 288万", available=False,
               unavailable_reason="走直连数据库,开源版无此凭证",
               note="当前返回空数组而非报错 —— 静默失败,Step D 要改成明确告知"),
    DataSource("us.news", "美股新闻", Market.US, DataKind.NEWS, "findata-db",
               "/api/gm/news/us/{code}", volume_hint="1万条", available=False,
               unavailable_reason="走直连数据库,开源版无此凭证"),
    DataSource("us.filings", "SEC 公告", Market.US, DataKind.ANNOUNCE, "findata-db",
               volume_hint="8,459 条", available=False,
               unavailable_reason="走直连数据库,开源版无此凭证"),
    DataSource("us.analyst", "分析师评级", Market.US, DataKind.RESEARCH, "findata-db",
               volume_hint="6,867 条", available=False,
               unavailable_reason="走直连数据库,开源版无此凭证"),
    DataSource("us.yfinance", "yfinance(免 key)", Market.US, DataKind.QUOTE, "yfinance",
               tier=SourceTier.FREE_UNSTABLE, requires_key=False, available=False,
               unavailable_reason="实测容器内取不到数:yfinance 打的 Yahoo v7 接口返回非 JSON",
               note="不要因为'装了 yfinance'就以为美股能用 —— 实测 AAPL 返回 price=None"),
]

# ══════════════════════════════════════════════════════════════
# 跨市场 · 平台自有能力(已在 Step 3 包成 MCP,模型可直接调)
# ══════════════════════════════════════════════════════════════
_GLOBAL: list[DataSource] = [
    DataSource("global.kronos", "Kronos K线预测", Market.GLOBAL, DataKind.FORECAST, "kronos",
               "/api/saas/kronos/predict", note="经 hunter 网关 · 同一把 key",
               used_by=["hunter_cap_kpred"]),
    DataSource("global.truesource_brief", "情报简报", Market.GLOBAL, DataKind.INTEL, "truesource",
               "/api/saas/truesource/brief", note="经 hunter 网关 · 同一把 key",
               used_by=["hunter_cap_truesource_brief"]),
    DataSource("global.truesource_scout", "主动情报采集", Market.GLOBAL, DataKind.INTEL, "truesource",
               "/api/saas/truesource/scout", note="经 hunter 网关 · 同一把 key",
               used_by=["hunter_cap_truesource_scout"]),
    DataSource("global.geo", "地缘冲突数据", Market.GLOBAL, DataKind.GEO, "finance-data",
               "/api/geo/overview", requires_key=False,
               note="本地聚合 · 无外部依赖"),
]

CATALOG: list[DataSource] = _A + _HK + _US + _GLOBAL

_BY_KEY = {s.key: s for s in CATALOG}


# ── 运行时状态 ────────────────────────────────────────────────

def _has_platform_key() -> bool:
    """有没有配 hunter 平台 key(env 或网页里填的)。

    走 finance_data_auth 这个唯一入口 —— 之前这套 fallback 抄在四个文件里,
    结果网页填的 key 喂不到深度分析且不报错(见 `_13` §1.3)。
    """
    try:
        from app.services import finance_data_auth as _auth
        return bool(_auth.data_token())
    except Exception:
        return False


def is_configured(src: DataSource) -> bool:
    if not src.requires_key:
        return True
    if src.provider == "findata-db":
        return bool(os.getenv("FINDATA_DB_URL"))
    return _has_platform_key()


def status_of(src: DataSource) -> str:
    """UI 上那个状态点。**四种状态对应四种完全不同的用户动作**:

      unavailable —— 开源版没这条通道。用户做什么都没用,别让他白折腾
      need_key    —— 去申请一把 key 就能用。这是最该被看见的一种
      ok/degraded/down —— 通道和凭证都齐了,是上游的事
      unknown     —— 还没调用过。**不假装健康**
    """
    if not src.available:
        return "unavailable"
    if not is_configured(src):
        return "need_key"
    from app.services import source_health
    return source_health.health_of(src.key)


def to_dict(src: DataSource) -> dict:
    from app.services import source_health
    d = asdict(src)
    d["market"] = src.market.value
    d["market_label"] = MARKET_LABEL[src.market]
    d["kind"] = src.kind.value
    d["kind_label"] = KIND_LABEL[src.kind]
    d["tier"] = src.tier.value
    d["configured"] = is_configured(src)
    d["status"] = status_of(src)
    d["health"] = source_health.stats(src.key)
    return d


# ── 查询 ──────────────────────────────────────────────────────

def get(key: str) -> DataSource | None:
    return _BY_KEY.get(key)


def all_sources() -> list[DataSource]:
    return list(CATALOG)


def by_market(market: Market | str) -> list[DataSource]:
    m = Market(market) if not isinstance(market, Market) else market
    return [s for s in CATALOG if s.market is m]


def grouped() -> list[dict]:
    """按市场分组 —— `/api/catalog/sources` 的返回结构,也是侧栏的显示结构。"""
    out = []
    for m in MARKET_ORDER:
        items = [to_dict(s) for s in by_market(m)]
        # 叫 ready 不叫 usable 是有意的:通道在、凭证齐 ≠ 已经验证过能出数。
        # 从没调用过的源(status=unknown)也算 ready —— 它没有任何已知障碍,
        # 但我们**没有资格**说它可用。整件事的意义就是不夸大能力。
        ready = [i for i in items if i["status"] not in ("unavailable", "need_key")]
        out.append({
            "market": m.value,
            "label": MARKET_LABEL[m],
            "total": len(items),
            "ready": len(ready),
            "sources": items,
        })
    return out
