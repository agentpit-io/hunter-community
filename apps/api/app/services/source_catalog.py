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


# ── 真实上游 ──────────────────────────────────────────────────
#
# `_21` §1.2。**`upstream` 与 `provider` 是两件事,不要合并**:
#   · provider —— 我们**怎么取到**它(finance-data 网关 / 直连库 / 本地路由)
#   · upstream —— 数据**原本来自谁**(XTick / 东方财富 / 巨潮 / SEC …)
#
# 为什么必须分开:33 条里 23 条的 provider 都是 "finance-data",
# 按它分组等于把 70% 塞进一个叫"我们家"的抽屉,比按市场分更没信息量。
# 而用户想替换的恰恰是 upstream —— 他说"我有自己的 Tushare",
# 替换的是来源,不是我们的网关。
#
# 下面每一条的 upstream 都是**读 finance-data 源码核实**的,不是照着名字猜的。
# 证据写在各条目的行内注释里。没核实出来的必须留空,由
# `scripts/check_source_upstream.py` 报错 —— 宁可报错也不要一个猜的值。

UPSTREAM_LABEL = {
    "xtick":      "XTick",
    "akshare":    "AKShare",
    "yahoo":      "Yahoo Finance",
    "eastmoney":  "东方财富",
    "cninfo":     "巨潮资讯",
    "cls":        "财联社",
    "tushare":    "Tushare",
    "alpaca":     "Alpaca",
    "sec":        "SEC EDGAR",
    "hkex":       "港交所",
    "truesource": "TrueSource",
    "internal":   "平台自建",
    # ↓ `_24` §8.2② 新增。这五个**没有任何官方源在用**,纯粹是给用户接自己的
    # (下拉里的选项)。`check_source_upstream.py` 会为它们各报一条 warn,
    # 和 cls/tushare 一样是预期内的,不是漏清理。
    "tencent":      "腾讯财经",
    "sina":         "新浪财经",
    "finnhub":      "Finnhub",
    "polygon":      "Polygon.io",
    "alphavantage": "Alpha Vantage",
    # 只给用户自定义源用 —— 没有任何官方源是 custom,所以它不进 UPSTREAM_ORDER。
    # 放在这里是因为 UI 显示中文名走的是这张表,漏了它用户会看到裸的 "custom"
    "custom":     "自定义接口",
}

# UI 排列顺序 —— 按条目数从多到少,大的在前。
# 用户扫一眼就知道"这个平台主要靠谁供数"。
#
# `cls`(财联社)与 `tushare` 现在没有任何官方源以它们为**主**上游 ——
# 财联社只是往 news_item 里补电报,Tushare 只覆盖 ETF/基金净值,
# 两者都被归到了主上游那一条。留在这里是因为 `_21` §4.1 的
# 「选来源」下拉要提供它们**给用户接自己的** —— 用户有 Tushare key 很常见。
# `check_source_upstream.py` 会为此报一条 warn,那是预期内的,不是漏清理。
UPSTREAM_ORDER = [
    "akshare", "yahoo", "xtick", "eastmoney", "cninfo",
    "alpaca", "sec", "tushare", "cls", "hkex", "truesource", "internal",
]


@dataclass
class DataSource:
    key: str                          # 稳定 ID · 也是 source_health 的主键
    name: str
    market: Market
    kind: DataKind
    provider: str                     # finance-data / yahoo / akshare / findata-db / kronos / truesource
    endpoint: str = ""                # 相对上游的路径 · 空 = 不是 HTTP 取数
    # ↑ endpoint 之前是第 6 个位置参数,所有条目都靠位置传。
    #   upstream 必须加在它**之后**,否则会被当成 endpoint 收走。
    upstream: str = ""                # 真实上游 · 见 UPSTREAM_LABEL · 空=未核实(check 会报错)
    owner: str = "official"           # official | user —— 解析链排序与 UI 区分用
    tier: SourceTier = SourceTier.OFFICIAL
    volume_hint: str = ""             # "960万条分钟线" —— UI 直接显示,让用户感知家底
    requires_key: bool = True
    available: bool = True            # False = 有数据但开源版走不到
    unavailable_reason: str = ""
    note: str = ""
    weight: float = 1.0               # 多源合并时的采信权重 · Step D 用
    used_by: list[str] = field(default_factory=list)   # 哪些工具/SKILL 依赖它
    # 仅对 owner="user" 有意义:这条源自己那把 key 存了没。
    # 官方源不看它(它们共用平台 key,由 _has_platform_key() 判定)。
    # 需要单独一个字段是因为 PATCH 允许把 key 清空而 requires_key 仍为真,
    # 那时它就该显示 need_key —— 光看 requires_key 判断不出来
    has_key: bool = True


# ══════════════════════════════════════════════════════════════
# A股 · finance-data 官方库,经 hunter 网关,一把 hunt_tools_ key 全通
# 端点取自 services/finance_data_client.py 的真实调用(不是照文档抄的)
# ══════════════════════════════════════════════════════════════
_A: list[DataSource] = [
    DataSource("a.quote", "实时行情", Market.A, DataKind.QUOTE, "finance-data",
               "/api/v1/quote/{symbol}", volume_hint="全市场 5000+ 只",
               note="含五档盘口", used_by=["watchlist_stock_quickview"],
               upstream="xtick",  # main.py http_client=XTickHTTP → quote_snapshot
           ),
    DataSource("a.kline", "K线", Market.A, DataKind.KLINE, "finance-data",
               "/api/v1/kline/{symbol}", note="1m/5m/1d · 前复权",
               upstream="xtick",  # main.py:164 stock→xtick get_kline(ETF/基金走 Tushare)
           ),
    DataSource("a.orderbook", "盘口", Market.A, DataKind.QUOTE, "finance-data",
               "/api/v1/orderbook/{symbol}",
               upstream="xtick",  # 同 a.quote · quote_snapshot
           ),
    DataSource("a.news", "个股新闻", Market.A, DataKind.NEWS, "finance-data",
               "/api/v1/news",
               upstream="eastmoney",  # news_eastmoney.py search-api-web.eastmoney.com(财联社 news_cls 同表补充)
           ),
    DataSource("a.news_articles", "新闻全文", Market.A, DataKind.NEWS, "finance-data",
               "/api/v1/news/articles", note="带正文 · 深度分析用",
               upstream="eastmoney",  # 同上 · news_item 表
           ),
    DataSource("a.announce", "巨潮公告", Market.A, DataKind.ANNOUNCE, "finance-data",
               "/api/v1/cninfo/announcements",
               upstream="cninfo",  # news_cninfo.py → cninfo_announcements · 法定披露平台权重0.95
           ),
    DataSource("a.financial", "财务报表", Market.A, DataKind.FINANCIAL, "finance-data",
               "/api/v1/financial/{symbol}",
               upstream="xtick",  # main.py:298 refresh_financial → http_client.get_financial
           ),
    DataSource("a.money_flow", "资金流向", Market.A, DataKind.CAPITAL, "finance-data",
               "/api/v1/money_flow/{symbol}",
               upstream="xtick",  # main.py:808 http_client.get_money_flow
           ),
    DataSource("a.lhb", "龙虎榜", Market.A, DataKind.CAPITAL, "finance-data",
               "/api/v1/lhb/{symbol}", volume_hint="17 只有记录",
               note="没上过榜的股票返回 200 + 空数组(不是 404)—— 上层分不清'没上榜'和'我们没数据',待改",
               upstream="akshare",  # seed_uzi_dims.py ak.stock_lhb_detail_em(东财口径)
           ),
    # 2026-08-15 跑了 seed_uzi_dims.py。通道一直是好的,现在也有数据了 ——
    # 但**覆盖极薄**:只有 2 只股票,其余仍 404。
    # 标 available=True 说的是"通道通",覆盖度写在 volume_hint 里别让人误会。
    # 覆盖上不去的原因:seed 用的 akshare 从新加坡打 push2.eastmoney.com
    # 大部分调用失败(20 只里只成了 2 只)。要提高覆盖得换数据源或走国内代理。
    DataSource("a.fund_holders", "十大流通股东", Market.A, DataKind.HOLDER, "finance-data",
               "/api/v1/fund_holders/{symbol}", volume_hint="5 只已入库",
               note="含持股数与占流通股比例 · 未入库返回 404(不是假装成功)。"
                    "覆盖低是因为东财这个接口成功率本身就低,已改走国内代理但仍有限",
               upstream="akshare",  # seed_uzi_dims.py ak.stock_gdfx_free_top_
           ),
    DataSource("a.governance", "公司治理", Market.A, DataKind.HOLDER, "finance-data",
               "/api/v1/governance/{symbol}", volume_hint="5 只已入库",
               note="大股东占比/top5/top10 · 从十大流通股东派生,覆盖跟着它走",
               upstream="akshare",  # 由 fund_holdings 派生,跟着它走
           ),
    DataSource("a.research", "券商研报", Market.A, DataKind.RESEARCH, "finance-data",
               "/api/v1/research/{symbol}", volume_hint="53 只 · 1,763 篇",
               note="无研报的股票返回 200 + count:0 · 同 a.lhb 的问题",
               upstream="akshare",  # seed_uzi_dims.py ak.stock_research_report_em
           ),
    # 2026-08-15 通了。原来 0 行,因为 seed 走的东财 stock_individual_info_em
    # **在新加坡和国内都是 RemoteDisconnected**(接口本身坏了,换代理也没用)。
    # 改用巨潮 stock_industry_change_cninfo 经国内 AK 代理拿三级分类,
    # 同业清单在我们自己库里按同 industry_l2 分组 —— 不再依赖外部接口。
    DataSource("a.peers", "同业对标", Market.A, DataKind.VALUATION, "finance-data",
               "/api/v1/peers/{symbol}", volume_hint="58 只已分类",
               note="巨潮三级行业分类 + 同业清单 · 未分类的股票返回 404",
               upstream="cninfo",  # stock_industry_change_cninfo 三级行业分类(经 AK 代理)
           ),
    # 免 key 兜底 —— 但要如实说明它的问题
    DataSource("a.akshare", "AKShare(免 key)", Market.A, DataKind.QUOTE, "akshare",
               tier=SourceTier.FREE_UNSTABLE, requires_key=False,
               note="设 DATA_SOURCE_PROVIDER=akshare 启用。容器内经常连不通,"
                    "且是全局切换(会一并影响港美股),仅作没有 key 时的兜底",
               upstream="akshare",  # 直接用 akshare 库取数,不经 finance-data(provider 也是 akshare)
           ),
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
               note="Yahoo 免费源 · 延迟约 15 分钟 · Redis 缓存 60s",
               upstream="yahoo",  # hk_data_daily.py:85 query1.finance.yahoo.com/v8/chart
           ),
    DataSource("hk.kline", "港股K线", Market.HK, DataKind.KLINE, "yahoo",
               "/api/gm/kline/hk/{code}", tier=SourceTier.FREE_STABLE,
               requires_key=False, note="同上 · 日K缓存 30min",
               upstream="yahoo",  # hk_data_daily.py:180 yahoo_chart(code,'5m') · 同 hk.quote 那个接口
           ),
    # 这两条 2026-08-15 通了 —— 同上,经网关。量级是当天实测值。
    DataSource("hk.master", "港股主表", Market.HK, DataKind.QUOTE, "finance-data",
               "/api/v1/hk/master", volume_hint="2,817 只",
               note="中文名/繁体名/每手股数 · 经 hunter 网关",
               upstream="internal",  # hk_stock_master 只被 SELECT,无采集器写 —— 是主数据表
           ),
    DataSource("hk.kline_db", "港股历史K线(库)", Market.HK, DataKind.KLINE, "finance-data",
               "/api/v1/hk/kline/{code}", volume_hint="日线 69.8万 · 5分钟 74.3万",
               note="比 Yahoo 那条覆盖更全(历史更长)· 经 hunter 网关",
               upstream="yahoo",  # hk_kline_1d/5m ← hk_data_daily yahoo_chart
           ),
    # 这 4 条 2026-08-15 第二批通了 —— finance-data 补了 /api/v1/hk/* 端点
    DataSource("hk.financial", "港股财报", Market.HK, DataKind.FINANCIAL, "finance-data",
               "/api/v1/hk/financial/{code}", volume_hint="1,425 条",
               note="EPS/BPS/营收/净利 按报告期倒序 · 经 hunter 网关",
               upstream="akshare",  # hk_data_daily mode_fin → _AK_BASE 139.199.221.232:8765
           ),
    DataSource("hk.filings", "港交所公告", Market.HK, DataKind.ANNOUNCE, "finance-data",
               "/api/v1/hk/filings/{code}", volume_hint="1,950 条",
               note="经 hunter 网关",
               upstream="hkex",  # hk_data_daily:237/259 www1.hkexnews.hk
           ),
    DataSource("hk.southbound", "南向资金", Market.HK, DataKind.CAPITAL, "finance-data",
               "/api/v1/hk/southbound", volume_hint="每交易日一行(2026-07-27 起)",
               note="采集每工作日 8:50 跑。表 7/27 才建所以行数少 —— "
                    "那是表龄不是故障,别再从行数少推断成采集坏了",
               upstream="akshare",  # hk_data_daily:352 mode_south → _AK_BASE
           ),
    DataSource("hk.ah_premium", "AH 溢价", Market.HK, DataKind.VALUATION, "finance-data",
               "/api/v1/hk/ah_premium", volume_hint="24 对/日",
               note="不带 date 取最新一天全部 · premium_pct>0 表示 A 股溢价",
               upstream="yahoo",  # hk_data_daily:378 HKDCNY=X 汇率 + hk_kline_1d 派生
           ),
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
    # 这两条原本 available=False(只走直连库)。接上港股同款 Yahoo chart 之后
    # **免 key 可用** —— 库优先、库空回落,私有部署配了 FINDATA_DB_URL 仍拿全量。
    DataSource("us.quote", "美股行情", Market.US, DataKind.QUOTE, "findata-db+yahoo",
               "/api/gm/quote/us/{code}", tier=SourceTier.FREE_STABLE,
               requires_key=False, volume_hint="13,019 只主表(库)· 全市场(Yahoo)",
               note="库优先 · 库拿不到回落 Yahoo chart(延迟约15分钟)",
               upstream="yahoo",  # 库优先回落 query1.finance.yahoo.com
           ),
    DataSource("us.kline", "美股K线", Market.US, DataKind.KLINE, "findata-db+yahoo",
               "/api/gm/kline/us/{code}", tier=SourceTier.FREE_STABLE, requires_key=False,
               volume_hint="库:分钟 960万 · 5分钟 870万 · 日线 288万",
               note="库优先 · 库空回落 Yahoo。Yahoo 分钟线只给近期,覆盖窄于库",
               upstream="alpaca",  # us_market.py:23 data.alpaca.markets 写 us_kline_1d(空则回落 Yahoo)
           ),
    # 下面三条 2026-08-15 通了 —— finance-data 新增 /api/v1/us/* 端点 + 网关放行,
    # community 侧 findata_db 改成"库优先 · 库不可用走网关"。量级是当天实测的真实值。
    DataSource("us.news", "美股新闻", Market.US, DataKind.NEWS, "finance-data",
               "/api/v1/us/news", volume_hint="25,486 条",
               note="经 hunter 网关 · 同一把 key",
               upstream="alpaca",  # us_data_daily.py:65 data.alpaca.markets → us_news
           ),
    DataSource("us.filings", "SEC 公告", Market.US, DataKind.ANNOUNCE, "finance-data",
               "/api/v1/us/filings/{symbol}", volume_hint="24,173 条",
               note="可按 form 过滤(10-K/10-Q/8-K…)· 经 hunter 网关",
               upstream="sec",  # us_data_daily.py:170 sec.gov/Archives/edgar daily-index
           ),
    DataSource("us.analyst", "分析师评级", Market.US, DataKind.RESEARCH, "finance-data",
               "/api/v1/us/analysts/{symbol}", volume_hint="8,997 条",
               note="含 firm / action / from→to grade · 经 hunter 网关",
               upstream="yahoo",  # us_data_daily.py:216 yf.Ticker → us_analyst_ratings
           ),
    DataSource("us.master", "美股主表", Market.US, DataKind.QUOTE, "finance-data",
               "/api/v1/us/master", volume_hint="13,019 只",
               note="代码/中英文名/交易所/ETF 标识 · 支持模糊搜 · 经 hunter 网关",
               upstream="sec",  # us_data_daily.py:150 sec.gov/files/company_tickers.json
           ),
    DataSource("us.yfinance", "yfinance(免 key)", Market.US, DataKind.QUOTE, "yfinance",
               tier=SourceTier.FREE_UNSTABLE, requires_key=False, available=False,
               unavailable_reason="实测容器内取不到数:yfinance 打的 Yahoo v7 接口返回非 JSON",
               note="不要因为'装了 yfinance'就以为美股能用 —— 实测 AAPL 返回 price=None",
               upstream="yahoo",  # yfinance 库 · 打的就是 Yahoo
           ),
]

# ══════════════════════════════════════════════════════════════
# 跨市场 · 平台自有能力(已在 Step 3 包成 MCP,模型可直接调)
# ══════════════════════════════════════════════════════════════
_GLOBAL: list[DataSource] = [
    DataSource("global.kronos", "Kronos K线预测", Market.GLOBAL, DataKind.FORECAST, "kronos",
               "/api/saas/kronos/predict", note="经 hunter 网关 · 同一把 key",
               used_by=["hunter_cap_kpred"],
               upstream="internal",  # 自建 Kronos 模型推理,无外部上游
           ),
    DataSource("global.truesource_brief", "情报简报", Market.GLOBAL, DataKind.INTEL, "truesource",
               "/api/saas/truesource/brief", note="经 hunter 网关 · 同一把 key",
               used_by=["hunter_cap_truesource_brief"],
               upstream="truesource",  # 自建 TrueSource 情报服务
           ),
    DataSource("global.truesource_scout", "主动情报采集", Market.GLOBAL, DataKind.INTEL, "truesource",
               "/api/saas/truesource/scout", note="经 hunter 网关 · 同一把 key",
               used_by=["hunter_cap_truesource_scout"],
               upstream="truesource",  # 自建 TrueSource 情报服务
           ),
    # provider 写 "local" 而不是 "finance-data" —— 它是 community 自己的路由,
    # 不经网关。写错会让 check_sources.py --probe 拿它去打上游然后误报 403。
    DataSource("global.geo", "地缘冲突数据", Market.GLOBAL, DataKind.GEO, "local",
               "/api/geo/overview", requires_key=False,
               note="community 本地聚合 · 不经网关 · 无外部依赖",
               upstream="internal",  # community 本地聚合 · 不经网关
           ),
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
    # 用户自接的源看**他自己那把 key**,不看平台 key。
    # 混用会得到荒谬结果:用户明明填好了 Tushare token,
    # 却因为没配我们的 HUNTER_API_KEY 被标成 need_key ——
    # 那恰好否定了"用户可以脱离我们"这件事
    if src.owner == "user":
        return src.has_key
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
    d["upstream_label"] = UPSTREAM_LABEL.get(src.upstream, src.upstream or "未核实")
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


def by_upstream(upstream: str) -> list[DataSource]:
    return [s for s in CATALOG if s.upstream == upstream]


def grouped_by_upstream(user_id: str | None = None) -> list[dict]:
    """**按真实来源分组** —— `_21` §2,数据源侧栏的新显示结构。

    为什么换掉按市场分组:按市场分是**我们的视角**("我们覆盖了哪些市场"),
    回答不了用户真正的问题 ——「我手上有 Tushare 的 key,能接进来吗」。
    按来源分才对得上,因为用户要替换的就是来源。

    市场没有删,降级成筛选条(`markets` 字段给前端做 chip)。
    它本身有用,只是不该当主分类。

    「你自己的」永远排第一组 —— 用户脱离我们的能力是这次改造的主题,
    把他自己配的源排在我们的下面,等于在 UI 上否认这件事。
    """
    out: list[dict] = []

    # ① 用户自己的 —— 即使一条没有也要显示,那个空组就是添加入口
    user_items = [to_dict(s) for s in _user_sources(user_id)] if user_id else []
    out.append({
        "upstream": "user",
        "label": "你自己的",
        "owner": "user",
        "total": len(user_items),
        "ready": len([i for i in user_items if i["status"] not in ("unavailable", "need_key")]),
        "markets": sorted({i["market"] for i in user_items}),
        "sources": user_items,
    })

    # ② 官方源 —— `_24` §3.1 **撤架,不再陈列**。
    #
    # 老板 2026-08-19:「让用户自己添加,不要我们这么多,
    #                  **这样会让用户觉得我们在推销我们自己的产品**」
    #
    # 33 条官方源里 26 条要我们的平台 key,打开页面就是一个"多数商品标着
    # 需付费解锁"的商品目录 —— 这跟开源项目该有的样子是反的。
    #
    # **CATALOG 本身没删,删的是陈列。**那 33 条现在的用途是:
    #   · source_templates 的预填知识(哪个 URL、什么参数)
    #   · source_mapping 的字段映射
    #   · check_source_upstream.py 的校验基准
    # 它们从"我们卖给你的货"变成"我们帮你填好的知识"(§2 推论 1)。
    if _SHELF:
        out.extend(_official_groups())
    return out


# 官方货架开关。开源版恒为关 —— 留这个常量不是为了让人打开它,
# 而是让"为什么这里空着"有个可查的落点(有人会以为是 bug)。
_SHELF = False


def _official_groups() -> list[dict]:
    """原来的官方源分组 —— 现在没有调用方(`_SHELF` 恒 False)。

    保留函数体是因为 SaaS 那条线的逻辑与此同源,删掉之后两边对不上;
    而且它是"我们曾经这样陈列"的唯一记录。
    """
    out: list[dict] = []
    seen = set()
    for up in UPSTREAM_ORDER + sorted({s.upstream for s in CATALOG} - set(UPSTREAM_ORDER)):
        if up in seen:
            continue
        seen.add(up)
        items = [to_dict(s) for s in by_upstream(up)]
        if not items:
            continue
        ready = [i for i in items if i["status"] not in ("unavailable", "need_key")]
        out.append({
            "upstream": up,
            "label": UPSTREAM_LABEL.get(up, up or "未核实来源"),
            "owner": "official",
            "total": len(items),
            "ready": len(ready),
            "markets": sorted({i["market"] for i in items}),
            "sources": items,
        })
    return out


def _user_sources(user_id: str) -> list[DataSource]:
    """用户自定义数据源 —— 读 `user_data_sources` 表(`_21` §7 步 2)。

    转成同一个 `DataSource` 结构,这样 UI 侧不用为"用户的"和"我们的"
    写两套渲染。区别只体现在 `owner` 字段上。

    **查库失败不抛异常**:这个函数在能力库页每次刷新时都会被调用,
    抛出去的话整个数据源页都白屏 —— 而用户自定义源为空是完全正常的状态。
    记日志,返回空,让页面照常显示我们的源。
    """
    if not user_id:
        return []
    try:
        from app.services.database import get_conn
    except Exception:
        return []
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, upstream, market, kind, endpoint, requires_key, "
            "       enabled, api_key_enc, last_err "
            "FROM user_data_sources WHERE user_id=%s ORDER BY created_at DESC",
            (user_id,),
        )
        rows = cur.fetchall()
    except Exception as e:                                   # noqa: BLE001
        from loguru import logger
        logger.warning("[source_catalog] 读用户数据源失败(按空处理): {}", e)
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    out: list[DataSource] = []
    for (sid, name, upstream, market, kind, endpoint,
         requires_key, enabled, key_enc, last_err) in rows:
        try:
            mk, kd = Market(market), DataKind(kind)
        except ValueError:
            # 库里存了注册表不认的市场/类型 —— 跳过而不是崩。
            # 会发生在我们改了枚举而老数据没迁的时候
            continue
        out.append(DataSource(
            key=f"user.{sid}",
            name=name,
            market=mk,
            kind=kd,
            provider="user",
            endpoint=endpoint,
            upstream=upstream,
            owner="user",
            tier=SourceTier.PREMIUM,
            requires_key=bool(requires_key),
            # 停用的源仍然列出来但标成不可用 —— 藏起来的话用户会以为被删了,
            # 然后再加一遍,撞上唯一索引又被拒,不知道发生了什么
            available=bool(enabled),
            unavailable_reason="" if enabled else "你把它停用了 · 可在详情里重新启用",
            has_key=bool(key_enc),
            note=(f"你自己接的 · 上游 {UPSTREAM_LABEL.get(upstream, upstream)}"
                  + (f" · 最近一次错误:{last_err[:80]}" if last_err else "")),
        ))
    return out


def grouped() -> list[dict]:
    """按市场分组 —— 旧结构,保留给还在用它的调用方(check_sources.py / 概览页)。

    UI 侧栏已换成 `grouped_by_upstream()`。这个函数不删,因为
    `scripts/check_sources.py` 和概览统计还在用,删了就是为了改 UI 顺手弄坏校验。
    """
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
