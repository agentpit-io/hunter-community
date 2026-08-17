"""数据源「来源模板」—— `_21` §4.1 的落点。

**这个文件解决的是"加数据源为什么比加工具难"这个问题。**

`_20` §4.1 说过:第三方 API 的返回格式各不相同,所以用户填完地址还得填一层
JSONPath 字段映射,否则我们不知道 `1341.99` 这个数在返回体的哪个位置。
那一步是整件事里最劝退的。

但它其实**只对完全自定义的接口成立**。如果用户接的是 Tushare、AKShare、
东方财富这些**我们已经知道格式**的来源,映射我们内置就行 ——
用户只要填地址和 key,难度立刻降到和加一个 MCP 工具一样。

所以「选来源」这一步不是给数据打标签,是**选模板**:

    已知来源 → 只填地址 + key,映射内置
    其他     → 地址 + key + 自己填 JSONPath 映射(步 6)

模板里**不写死 endpoint**,只给 `endpoint_hint` 当占位符提示。
因为同一个来源用户可能自建了代理(比如 AKShare 走自己的国内跳板机 ——
我们自己就是这么干的,见 finance-data 的 139.199.221.232:8765),
写死等于假设所有人都用官方地址。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.services.source_catalog import DataKind, KIND_LABEL, UPSTREAM_LABEL


@dataclass
class SourceTemplate:
    upstream: str
    endpoint_hint: str                 # 占位符 · 不是默认值
    requires_key: bool                 # 表单里那个勾的**初值**,用户可以改
    key_in: str = "header"             # header / query / body
    key_name: str = "Authorization"
    key_prefix: str = ""               # "Bearer " 之类
    # 空 = 支持全部类型。列了就只允许这几种 —— 让用户在
    # 「Tushare + 地缘数据」这种不存在的组合上省下一次试错
    kinds: list[str] = field(default_factory=list)
    note: str = ""
    # 我们内置了这个来源的字段映射吗?False 的话用户必须自己填(步 6)
    builtin_map: bool = True


# 顺序 = 表单下拉的顺序。常见的排前面。
TEMPLATES: list[SourceTemplate] = [
    SourceTemplate(
        "tushare", "https://api.tushare.pro", True,
        key_in="body", key_name="token",
        kinds=["quote", "kline", "financial", "valuation"],
        note="Tushare Pro · token 走 POST body 的 token 字段(不是 header)· "
             "地址里可用 `{ts_code}` 展开成 600519.SH",
    ),
    SourceTemplate(
        "akshare", "http://你的AKShare代理:8765", False,
        kinds=["quote", "kline", "news", "capital", "holder", "research", "valuation"],
        note="AKShare 是 Python 库不是 HTTP 服务 —— 这里填的是你自己包的 HTTP 代理。"
             "我们自己也是这么用的(容器里直连 AKShare 经常打不通,走了国内跳板机)",
    ),
    SourceTemplate(
        # ⚠️ 用 push2delay 不用 push2:2026-08-17 实测 push2.eastmoney.com
        # 对所有请求返回 **502(它自己的 nginx)**,而 push2delay 正常出数。
        # 之前这里写的是 push2,用户照着填必然连不上 —— 提示里给一个
        # 打不通的地址,比不给提示更糟。
        "eastmoney",
        # fields 要**和 source_mapping 里那份映射对齐**:少请求一个字段,
        # 表现是卡片上某一栏静默显示 0 或回落成代码,不报错。
        # f48 成交额 与 f60 昨收 原来漏了,f58 名称请求了却没映射。
        "https://push2delay.eastmoney.com/api/qt/stock/get"
        "?secid={secid}&fields=f43,f44,f45,f46,f47,f48,f57,f58,f60,f169,f170",
        False,
        kinds=["quote", "kline", "news", "capital", "holder", "research"],
        note="东方财富 · 免 key。`{secid}` 会自动展开成 1.600519(沪)/ 0.000001(深)—— "
             "那个前缀跟交易所走,按股票变,没法写死。"
             "push2 主域近期 502,提示里给的是 push2delay",
    ),
    SourceTemplate(
        "xtick", "http://你的XTick地址/api", True,
        key_in="query", key_name="token",
        kinds=["quote", "kline", "financial", "capital"],
        note="XTick 至尊版 · REST 返回 ZIP 内含 data.json",
    ),
    SourceTemplate(
        "yahoo", "https://query1.finance.yahoo.com/v8/finance/chart/{yahoo}", False,
        kinds=["quote", "kline", "news"],
        note="Yahoo chart · 免 key · 延迟约 15 分钟 · "
             "`{yahoo}` 展开成 600519.SS / 0700.HK。"
             "注意 Yahoo 对数据中心 IP 限流较凶,可能 429",
    ),
    SourceTemplate(
        "cninfo", "http://www.cninfo.com.cn/new/hisAnnouncement/query", False,
        kinds=["announce", "valuation"],
        note="巨潮资讯 · A股法定信息披露平台",
    ),
    SourceTemplate(
        "cls", "https://www.cls.cn/api", False,
        kinds=["news"], note="财联社电报 · 时效性强",
    ),
    SourceTemplate(
        "alpaca", "https://data.alpaca.markets/v2", True,
        key_in="header", key_name="APCA-API-KEY-ID",
        kinds=["quote", "kline", "news"],
        note="Alpaca 美股 · 还需要 APCA-API-SECRET-KEY,填在附加 header 里",
    ),
    SourceTemplate(
        "sec", "https://data.sec.gov", False,
        kinds=["announce", "financial"],
        note="SEC EDGAR · 免 key,但必须带 User-Agent(填在附加 header 里),否则 403",
    ),
    SourceTemplate(
        "hkex", "https://www1.hkexnews.hk", False,
        kinds=["announce"], note="港交所披露易",
    ),
    # 兜底 —— 完全自定义的接口。这条**没有**内置映射
    SourceTemplate(
        "custom", "https://你的接口/path/{symbol}", True,
        builtin_map=False,
        note="完全自定义的接口。因为我们不知道你的返回格式,"
             "需要你填一层字段映射把它对齐 —— 那部分还在做(步 6),"
             "在那之前建议先选一个上面的已知来源,或者把你的服务包成 MCP 工具接进来",
    ),
]

_BY_UPSTREAM = {t.upstream: t for t in TEMPLATES}


def get(upstream: str) -> SourceTemplate | None:
    return _BY_UPSTREAM.get(upstream)


def is_known(upstream: str) -> bool:
    return upstream in _BY_UPSTREAM


def to_dict(t: SourceTemplate) -> dict:
    d = asdict(t)
    # label 从 source_catalog 取,**不在这里再写一份中文名** ——
    # 两处各写一份就是下一次清单漂移的开始(`_13` §3.1)
    d["label"] = UPSTREAM_LABEL.get(t.upstream, t.upstream)
    d["kind_options"] = [
        {"value": k.value, "label": KIND_LABEL[k]}
        for k in DataKind
        if not t.kinds or k.value in t.kinds
    ]
    return d


def all_templates() -> list[dict]:
    return [to_dict(t) for t in TEMPLATES]
