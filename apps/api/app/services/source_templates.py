"""数据源「来源模板」—— `_21` §4.1 起,`_24` §3.2/§3.3 重做。

## 这个文件解决什么

`_20` §4.1:第三方 API 的返回格式各不相同,用户填完地址还得填一层 JSONPath
映射,否则我们不知道 `1341.99` 这个数在返回体的哪个位置。那一步最劝退。

但它只对**完全自定义**的接口成立。接的是我们已经知道格式的来源,映射内置就行。
所以「选来源」不是给数据打标签,是**选模板**。

## `_24` 改了什么:一个来源不是一个地址,是一组接口

老板 2026-08-19 点名的问题:「一个源拉 K线、新闻,接口不一样,你看看怎么处理」。

原来一个 `SourceTemplate` 只有一个 `endpoint_hint`,用户选「东方财富」填完行情,
想再加新闻得**从头再走一遍**:再点添加、再选东财、再填一次 key。四个接口四遍。

现在一个模板带 `endpoints: list[Endpoint]`,每条是一个 `(market, kind, url)`。
表单变两步:选来源 → 勾接口(地址已预填)→ 一次提交写 N 行。
需要 key 的**只填一次**,写库时复制进选中的每一行。

数据库那边一行仍然 = 一个 `(market, kind, endpoint)` —— 表结构没动,
唯一索引 `(user_id, market, kind, upstream)` 正好承载这个模型(`_24` §3.2)。

## 为什么这次写死 url,而 `_21` 当时特意不写死

`_21` 的理由是「同一个来源用户可能自建了代理,写死等于假设所有人用官方地址」。
那个顾虑只对 AKShare 成立(它是 Python 库,必须自己架代理),而 AKShare
已经按 `_24` §8.2④ 移出数据源、改做「能力」了。

剩下的来源都是公网直连的固定地址,不预填等于把 §3.3 那批坑原样丢给用户 ——
**每条 `url` 仍然可以在表单里点开改**,预填是默认值不是枷锁。

## ⚠️ 每条 url 都必须实测过才能写进来

上一版这里写的是 `push2.eastmoney.com`,而那个域**对所有请求返回 502**,
用户照着填必然连不上。**给一个打不通的地址比不给提示更糟。**

所以每条带 `verified` 标记与实测日期。`verified=False` 的在 UI 上显示
「未实测」,不假装我们验过。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.services.source_catalog import DataKind, KIND_LABEL, UPSTREAM_LABEL


@dataclass
class Endpoint:
    """来源套餐里的一条接口 = 用户勾一下就写一行 `user_data_sources`。"""

    market: str                        # a / hk / us / global
    kind: str                          # DataKind 的值
    url: str                           # 预填地址 · 占位符见 source_resolver.expand()
    label: str = ""                    # 空 = 用 KIND_LABEL
    method: str = "GET"
    # 这条接口独有的坑 —— 直接显示在勾选项下面。
    # §3.3 那四条「HTTP 200 但静默返回空」的全靠这里说清楚
    note: str = ""
    # 附加 header(不含 auth)。SEC 的 UA、新浪的 Referer 走这里
    headers: dict = field(default_factory=dict)
    # 实测过吗 · 见文件头。False 会在 UI 上标「未实测」
    verified: bool = False
    verified_at: str = ""              # "2026-08-19"
    # 默认勾不勾。冷门接口默认不勾,免得一次写进去一堆用不上的
    default_on: bool = True


@dataclass
class SourceTemplate:
    upstream: str
    requires_key: bool                 # 表单里那个勾的**初值**,用户可以改
    endpoints: list[Endpoint] = field(default_factory=list)
    key_in: str = "header"             # header / query / body
    key_name: str = "Authorization"
    key_prefix: str = ""               # "Bearer " 之类
    note: str = ""
    apply_url: str = ""                # 「去申请 →」指向哪
    # 我们内置了这个来源的字段映射吗?False 的话用户必须自己填(步 6)
    builtin_map: bool = True
    # 商业授权 · 普通用户申请不到。UI 上排最后并注明,
    # **不藏起来** —— 明说「这是商业授权」比让用户白试一场诚实
    commercial: bool = False


_UA = {"User-Agent": "Mozilla/5.0"}
_TODAY = "2026-08-19"


# ══════════════════════════════════════════════════════════════
# 顺序 = 表单下拉的顺序。
#
# 排序口径不是「谁家大」,是**用户点下去多快能看到数据**:
#   1. 腾讯   —— 零 header 零配置,点一下就出数(§8.2②,老板要的第一印象)
#   2. 新浪   —— 只多一个 Referer
#   3. 东财   —— 一次给四个接口,覆盖最全
#   ... 需要 key 的排后面,商业授权的排最后
# ══════════════════════════════════════════════════════════════
TEMPLATES: list[SourceTemplate] = [
    # ── 🟢 免 key ──────────────────────────────────────────────
    SourceTemplate(
        "tencent", False,
        note="腾讯财经 · **不需要任何 header,也不需要注册** —— "
             "所有来源里最省事的一个,建议第一次就选它",
        endpoints=[
            Endpoint("a", "quote", "https://qt.gtimg.cn/q={sina}",
                     label="A股实时行情", verified=True, verified_at=_TODAY,
                     note="返回的不是 JSON,是 v_sh600519=\"~分隔\" 的文本,我们内置了解析"),
            Endpoint("a", "kline",
                     "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
                     "?param={sina},day,,,320,qfq",
                     label="A股日K线(前复权)", verified=True, verified_at=_TODAY,
                     note="⭐ **免费 A 股 K 线首选** —— 实测 4/4 稳定。"
                          "东财那条虽然数据更长,但它的分片会轮换(见东财 K线的说明)"),
            Endpoint("hk", "quote", "https://qt.gtimg.cn/q=hk{code5}",
                     label="港股实时行情", verified=False,
                     note="港股代码补足 5 位,如 00700 → hk00700"),
        ],
    ),
    SourceTemplate(
        "sina", False,
        note="新浪财经 · 免 key,但**必须带 Referer**,否则 403",
        endpoints=[
            Endpoint("a", "quote", "https://hq.sinajs.cn/list={sina}",
                     label="A股实时行情", verified=True, verified_at=_TODAY,
                     headers={"Referer": "https://finance.sina.com.cn"},
                     note="返回 var hq_str_sh600519=\"逗号分隔\" 文本 · 我们内置了解析"),
        ],
    ),
    SourceTemplate(
        # ⚠️ 用 push2delay 不用 push2:2026-08-17 实测 push2.eastmoney.com
        # 对所有请求返回 **502(它自己的 nginx)**。2026-08-19 复测仍然如此。
        "eastmoney", False,
        note="东方财富 · 免 key · 一次能接四个接口,是免费源里覆盖最全的",
        endpoints=[
            Endpoint(
                "a", "quote",
                "https://push2delay.eastmoney.com/api/qt/stock/get"
                "?secid={secid}&fields=f43,f44,f45,f46,f47,f48,f57,f58,f60,f169,f170",
                label="实时行情", verified=True, verified_at=_TODAY, headers=_UA,
                note="用 push2delay,**不要 push2**(全域 502)。"
                     "fields 要和内置映射对齐:f48 成交额、f60 昨收、f58 名称",
            ),
            Endpoint(
                "a", "kline",
                # ⚠️ 这条**默认不勾,且标未实测** —— 见下面的 note。
                # 数据本身是好的(1600+ 行,列序核对无误),问题在可达性。
                "https://82.push2his.eastmoney.com/api/qt/stock/kline/get"
                "?secid={secid}&klt=101&fqt=1&beg=20200101&end=20500101"
                "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57",
                label="日K线", verified=False, headers=_UA, default_on=False,
                note="⚠️ **不稳定,建议改用腾讯的 A股日K线**。"
                     "东财 K线走 N.push2his 分片,而**能通的分片会轮换** —— "
                     "同一分钟内 82. 是 5/5,十分钟后变 0/4,同时 7. 变成 4/4。"
                     "写死任何一个分片都会时灵时不灵。"
                     "另外:必须带 beg/end(不带返回 rc:102),"
                     "且行情能用的 push2delay 对 K线只返回空数组",
            ),
            Endpoint(
                "a", "capital",
                "https://82.push2his.eastmoney.com/api/qt/stock/fflow/kline/get"
                "?secid={secid}&lmt=60&klt=101&fields1=f1,f2,f3"
                "&fields2=f51,f52,f53,f54,f55,f56",
                label="资金流向", verified=True, verified_at=_TODAY, headers=_UA,
            ),
            Endpoint(
                "a", "news",
                "https://search-api-web.eastmoney.com/search/jsonp?cb=x&param="
                "%7B%22uid%22%3A%22%22%2C%22keyword%22%3A%22{symbol}%22%2C%22type%22%3A"
                "%5B%22cmsArticleWebOld%22%5D%2C%22client%22%3A%22web%22%2C%22clientVersion%22"
                "%3A%22curr%22%2C%22param%22%3A%7B%22cmsArticleWebOld%22%3A%7B%22searchScope%22"
                "%3A%22default%22%2C%22sort%22%3A%22default%22%2C%22pageIndex%22%3A1%2C"
                "%22pageSize%22%3A20%7D%7D%7D",
                label="个股新闻", verified=True, verified_at=_TODAY, headers=_UA,
                note="返回 JSONP(外面包一层 `x(...)`),我们内置了剥壳",
            ),
        ],
    ),
    SourceTemplate(
        "yahoo", False,
        note="Yahoo Finance · 免 key · **一个接口同时给行情和 K线**。"
             "港股代码写成 0700.HK,美股直接写 AAPL",
        endpoints=[
            Endpoint("us", "quote", "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                     "?range=1d&interval=1m",
                     label="美股行情", verified=True, verified_at=_TODAY, headers=_UA),
            Endpoint("us", "kline", "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                     "?range=1y&interval=1d",
                     label="美股K线", verified=True, verified_at=_TODAY, headers=_UA),
            Endpoint("hk", "quote", "https://query1.finance.yahoo.com/v8/finance/chart/{yahoo}"
                     "?range=1d&interval=1m",
                     label="港股行情", verified=False, headers=_UA),
            Endpoint("hk", "kline", "https://query1.finance.yahoo.com/v8/finance/chart/{yahoo}"
                     "?range=1y&interval=1d",
                     label="港股K线", verified=False, headers=_UA),
        ],
    ),
    SourceTemplate(
        "sec", False,
        note="SEC EDGAR · 免 key,但 **User-Agent 必须含联系邮箱**(SEC 明文要求),"
             "否则 403。下面预填的是占位邮箱,**请改成你自己的**",
        endpoints=[
            Endpoint("us", "announce", "https://data.sec.gov/submissions/CIK{cik10}.json",
                     label="提交历史(10-K/10-Q/8-K)", verified=True, verified_at=_TODAY,
                     headers={"User-Agent": "YourName your@email.com"},
                     note="CIK 要补足 10 位,如 AAPL → CIK0000320193"),
            Endpoint("us", "financial",
                     "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik10}/us-gaap/Revenues.json",
                     label="XBRL 财务", verified=True, verified_at=_TODAY,
                     headers={"User-Agent": "YourName your@email.com"}),
            Endpoint("us", "valuation", "https://www.sec.gov/files/company_tickers.json",
                     label="公司代码主表", verified=True, verified_at=_TODAY,
                     headers={"User-Agent": "YourName your@email.com"},
                     default_on=False, note="全量代码对照表,不常用,默认不勾"),
        ],
    ),
    SourceTemplate(
        "cninfo", False,
        note="巨潮资讯 · A股法定信息披露渠道 · 免 key",
        endpoints=[
            Endpoint("a", "announce", "http://www.cninfo.com.cn/new/hisAnnouncement/query",
                     label="公司公告", method="POST", verified=True, verified_at=_TODAY,
                     headers=_UA,
                     note="⚠️ 参数 stock 要**复合格式** `600519,gssh0600519`(代码+orgId)· "
                          "只填 600519 会返回 totalRecordNum:0 而**不报错**"),
        ],
    ),
    SourceTemplate(
        "hkex", False,
        note="港交所披露易 · 免 key,但**它返回的是网页不是 API**,需要解析 HTML —— "
             "内置映射覆盖不到,建议配合自定义映射使用",
        builtin_map=False,
        endpoints=[
            Endpoint("hk", "announce", "https://www1.hkexnews.hk/search/titlesearch.xhtml",
                     label="披露易公告", verified=False, headers=_UA,
                     note="返回 HTML,不是 JSON"),
        ],
    ),

    # ── 🔑 需要 key ────────────────────────────────────────────
    #
    # ⚠️ 下面这批**地址全部照文档写,一条都没实测过**(没有 key 就跑不了)。
    # `_24` §8.2③ 拍板:逐个申请免费档 key 实测,跑通才把 verified 改 True。
    # 在那之前 UI 上一律显示「未实测」。
    SourceTemplate(
        "tushare", True,
        key_in="body", key_name="token",
        apply_url="https://tushare.pro/register",
        note="Tushare Pro · token 走 **POST body 的 token 字段**(不是 header)· "
             "免费额度按积分算",
        endpoints=[
            Endpoint("a", "kline", "https://api.tushare.pro", label="日线行情", method="POST"),
            Endpoint("a", "financial", "https://api.tushare.pro", label="财务报表", method="POST"),
            Endpoint("a", "valuation", "https://api.tushare.pro", label="估值指标", method="POST"),
            Endpoint("a", "capital", "https://api.tushare.pro", label="资金流向",
                     method="POST", default_on=False),
        ],
    ),
    SourceTemplate(
        "alpaca", True,
        key_in="header", key_name="APCA-API-KEY-ID",
        apply_url="https://alpaca.markets/",
        note="Alpaca 美股 · **还需要第二把 key** `APCA-API-SECRET-KEY`,"
             "填在附加 header 里 · 有免费档",
        endpoints=[
            Endpoint("us", "quote", "https://data.alpaca.markets/v2/stocks/{symbol}/quotes/latest",
                     label="美股行情"),
            Endpoint("us", "kline", "https://data.alpaca.markets/v2/stocks/{symbol}/bars"
                     "?timeframe=1Day&limit=120", label="美股K线"),
            Endpoint("us", "news", "https://data.alpaca.markets/v1beta1/news?symbols={symbol}",
                     label="美股新闻"),
        ],
    ),
    SourceTemplate(
        "finnhub", True,
        key_in="query", key_name="token",
        apply_url="https://finnhub.io/register",
        note="Finnhub · 免费档 60 次/分",
        endpoints=[
            Endpoint("us", "quote", "https://finnhub.io/api/v1/quote?symbol={symbol}",
                     label="美股行情"),
            Endpoint("us", "news", "https://finnhub.io/api/v1/company-news?symbol={symbol}",
                     label="公司新闻"),
            Endpoint("us", "financial",
                     "https://finnhub.io/api/v1/stock/financials-reported?symbol={symbol}",
                     label="财务报表"),
        ],
    ),
    SourceTemplate(
        "polygon", True,
        key_in="query", key_name="apiKey",
        apply_url="https://polygon.io/dashboard/signup",
        note="Polygon.io · 免费档 5 次/分",
        endpoints=[
            Endpoint("us", "quote",
                     "https://api.polygon.io/v2/aggs/ticker/{symbol}/prev", label="美股行情"),
            Endpoint("us", "kline",
                     "https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/day/2024-01-01/2026-12-31",
                     label="美股K线"),
        ],
    ),
    SourceTemplate(
        "alphavantage", True,
        key_in="query", key_name="apikey",
        apply_url="https://www.alphavantage.co/support/#api-key",
        note="Alpha Vantage · 免费档 **25 次/天**,额度很紧,适合做备用源",
        endpoints=[
            Endpoint("us", "quote",
                     "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}",
                     label="美股行情"),
            Endpoint("us", "kline",
                     "https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol={symbol}",
                     label="美股K线"),
        ],
    ),

    # ── 商业授权 · 普通用户申请不到,但明说比藏起来诚实 ──────────
    SourceTemplate(
        "xtick", True, commercial=True,
        key_in="query", key_name="token",
        note="XTick · **商业授权**,个人用户申请不到 —— 列在这里是为了说清楚,"
             "不是推荐你用",
        endpoints=[
            Endpoint("a", "quote", "https://your-xtick-host/quote/{symbol}", label="实时行情"),
            Endpoint("a", "kline", "https://your-xtick-host/kline/{symbol}", label="K线"),
            Endpoint("a", "financial", "https://your-xtick-host/financial/{symbol}",
                     label="财务报表"),
        ],
    ),
    SourceTemplate(
        "cls", True, commercial=True,
        note="财联社 · **商业授权** · 电报时效性强",
        endpoints=[
            Endpoint("a", "news", "https://www.cls.cn/api/telegraph", label="电报"),
        ],
    ),

    # ── 兜底 · 完全自定义 ──────────────────────────────────────
    SourceTemplate(
        "custom", True, builtin_map=False,
        note="完全自定义的接口。因为我们不知道你的返回格式,需要你填一层字段映射"
             "把它对齐 —— 如果只是想快速用起来,建议先选上面任意一个已知来源",
        endpoints=[
            Endpoint("a", "quote", "https://你的接口/path/{symbol}", label="自定义接口"),
        ],
    ),
]

_BY_UPSTREAM = {t.upstream: t for t in TEMPLATES}


def get(upstream: str) -> SourceTemplate | None:
    return _BY_UPSTREAM.get(upstream)


def is_known(upstream: str) -> bool:
    return upstream in _BY_UPSTREAM


def endpoint_of(upstream: str, market: str, kind: str) -> Endpoint | None:
    """取某个来源下某条具体接口 —— 批量添加时按 (market, kind) 回查预填值。"""
    t = get(upstream)
    if not t:
        return None
    for e in t.endpoints:
        if e.market == market and e.kind == kind:
            return e
    return None


def to_dict(t: SourceTemplate) -> dict:
    d = asdict(t)
    # label 从 source_catalog 取,**不在这里再写一份中文名** ——
    # 两处各写一份就是下一次清单漂移的开始(`_13` §3.1)
    d["label"] = UPSTREAM_LABEL.get(t.upstream, t.upstream)
    d["free"] = not t.requires_key
    # 每条接口补上 kind 的中文名(前端不该自己再维护一份 KIND_LABEL)
    for e, raw in zip(t.endpoints, d["endpoints"]):
        raw["kind_label"] = _kind_label(e.kind)
        raw["label"] = e.label or _kind_label(e.kind)
    # 这个来源一共覆盖哪些 kind —— 侧栏/卡片上显示「行情·K线·新闻·资金流」
    d["kinds"] = sorted({e.kind for e in t.endpoints})
    d["verified_count"] = sum(1 for e in t.endpoints if e.verified)
    return d


def _kind_label(kind: str) -> str:
    for k in DataKind:
        if k.value == kind:
            return KIND_LABEL[k]
    return kind


def all_templates() -> list[dict]:
    """下拉用。免 key 的在前,商业授权的在最后,custom 垫底。"""
    return [to_dict(t) for t in TEMPLATES]
