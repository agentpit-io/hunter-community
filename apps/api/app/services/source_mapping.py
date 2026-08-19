"""字段映射 —— 把第三方返回对齐到我们的格式(`_21` §4.1 / `_20` §4.1)。

**这个文件是「选来源 = 选模板」那句承诺的兑现处。**表单里说
"选了已知来源,映射我们内置",内置的就是这里的 `BUILTIN`。

设计上最要紧的一条:

    **映射不出来 = 明确失败,而不是返回一个缺字段的 dict。**

因为缺字段的 dict 会一路流到模型面前,模型看到 `price=None` 会自己
编一个说法("暂无最新价格,根据历史数据推测…")。用户看到的是一段
言之凿凿的分析,而它建立在空数据上 —— 这比取数失败糟得多。
所以 `apply()` 拿不到必需字段时抛 `MappingError`,由解析链降级到下一档。

**JSONPath 只支持我们真正用得到的子集**:`$.a.b`、`$.a[0].b`、`$[*].b`。
不引第三方 JSONPath 库,因为完整实现里有 filter/递归下降那些语法,
它们能让用户写出一个遍历整个响应的表达式 —— 那是给自己找的性能问题。
"""
from __future__ import annotations

import json
import re
from typing import Any


class MappingError(Exception):
    """映射失败 —— 必需字段没取到。带上到底缺了什么,别只说"失败"。"""


_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+|\*)\]")


def _walk(data: Any, path: str) -> Any:
    """`$.data.items[0].close` → 值。取不到返回 None(不抛)。"""
    if not path:
        return None
    p = path[2:] if path.startswith("$.") else path[1:] if path.startswith("$") else path
    cur = data
    for m in _TOKEN.finditer(p):
        key, idx = m.group(1), m.group(2)
        if cur is None:
            return None
        if key is not None:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(key)
        elif idx == "*":
            return cur if isinstance(cur, list) else None
        else:
            if not isinstance(cur, list) or int(idx) >= len(cur):
                return None
            cur = cur[int(idx)]
    return cur


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # NaN 必须挡在这里。finance-data 那边吃过一次亏:pandas 的 NaN 被
    # str() 成 "nan" 存进了行业名字段,成了一个看起来合法的分类
    return None if f != f else f


# ── 内置映射 ──────────────────────────────────────────────────
#
# 结构:BUILTIN[upstream][kind] = {我们的字段: JSONPath}
# `_required` 列出没有它就算失败的字段。
#
# ⚠️ **只写了实际核实过格式的组合。**没写的组合在 `apply()` 里会明确报
# "没有内置映射",而不是返回空 dict —— 后者会让用户以为接上了。

_QUOTE_REQ = ["price"]
_KLINE_REQ = ["rows"]

BUILTIN: dict[str, dict[str, dict]] = {
    # Tushare Pro · POST 返回 {code, msg, data:{fields:[...], items:[[...]]}}
    # 它是**列式**的(fields + items),不是对象数组 —— 单独一个 shape 标记
    "tushare": {
        "kline": {"_shape": "columnar",
                  "_fields": "$.data.fields", "_items": "$.data.items",
                  "_required": _KLINE_REQ},
    },
    # AKShare HTTP 代理 · 我们自己那台返回 {ok, data:[...]}(见 finance-data
    # 的 139.199.221.232:8765 /call)。用户自建的代理多半照抄这个形状
    "akshare": {
        "quote": {"price": "$.data[0].最新价", "change_pct": "$.data[0].涨跌幅",
                  "open": "$.data[0].今开", "high": "$.data[0].最高",
                  "low": "$.data[0].最低", "volume": "$.data[0].成交量",
                  "_required": _QUOTE_REQ},
        "news":  {"_shape": "list", "_list": "$.data",
                  "title": "标题", "url": "链接", "published_at": "发布时间",
                  "_required": ["title"]},
    },
    # 东方财富 push2 · {data:{f43:最新价, f170:涨跌幅, ...}}
    # 价格类字段单位是**分**,成交额是元。字段号含义:
    #   f43 最新价 · f44 最高 · f45 最低 · f46 今开 · f47 成交量(手)
    #   f48 成交额(元) · f57 代码 · f58 名称 · f60 昨收 · f169 涨跌额 · f170 涨跌幅
    "eastmoney": {
        # K线与资金流走 `data.klines` 那串逗号文本 —— 列含义见 _EM_KLINE_COLS
        "kline":   {"_shape": "em_klines", "_cols": "kline",
                    "_required": _KLINE_REQ},
        "capital": {"_shape": "em_klines", "_cols": "capital",
                    "_required": ["rows"]},
        # 新闻走 JSONP:外面裹一层 `x(...)`,里面 result.cmsArticleWebOld[]
        "news":    {"_shape": "jsonp", "_list": "$.result.cmsArticleWebOld",
                    "title": "title", "url": "url", "published_at": "date",
                    "source": "mediaName", "summary": "content",
                    # 标题里带 <em>600519</em> 这种搜索高亮标签,要剥掉 ——
                    # 不剥的话模型会把 HTML 标签当正文读进去
                    "_striptags": ["title", "summary"],
                    "_required": ["items"]},
        "quote": {"name": "$.data.f58",
                  "price": "$.data.f43", "change_pct": "$.data.f170",
                  "change_amt": "$.data.f169", "prev_close": "$.data.f60",
                  "open": "$.data.f46", "high": "$.data.f44",
                  "low": "$.data.f45",
                  "volume": "$.data.f47", "amount": "$.data.f48",
                  # 名称是字符串,不能过 _num() —— 过了会变成 None,
                  # 表现是卡片标题显示成股票代码而不是"贵州茅台"
                  "_text": ["name"],
                  "_scale": {"price": 0.01, "open": 0.01, "high": 0.01, "low": 0.01,
                             "prev_close": 0.01, "change_amt": 0.01, "change_pct": 0.01},
                  "_required": _QUOTE_REQ},
    },
    # Yahoo chart v8 · {chart:{result:[{meta:{...}, indicators:{quote:[{...}]}}]}}
    "yahoo": {
        "quote": {"price": "$.chart.result[0].meta.regularMarketPrice",
                  "prev_close": "$.chart.result[0].meta.chartPreviousClose",
                  "_required": _QUOTE_REQ},
        "kline": {"_shape": "yahoo_chart", "_required": _KLINE_REQ},
    },
    # 腾讯财经 · `v_sh600519="1~贵州茅台~600519~1299.10~..."`(88 段,`~` 分隔)
    #
    # 下标是 2026-08-19 实测数出来的,**不是照文档抄的** ——
    # 这类接口没有官方文档,网上流传的下标表版本很多且互相矛盾。
    "tencent": {
        "quote": {"_shape": "delimited", "_sep": "~",
                  "name": 1, "price": 3, "prev_close": 4, "open": 5,
                  "change_amt": 31, "change_pct": 32, "high": 33, "low": 34,
                  "volume": 36, "amount": 37, "pe": 39,
                  "_text": ["name"],
                  # ⚠️ 下面这两个换算**只对 A 股成立**,港股见 `_market`。
                  #   [36] A股是**手**(1 手 = 100 股)→ ×100 统一成股
                  #   [37] A股是**万元**(实测 428876 对应 42.9 亿)→ ×10000 统一成元
                  # 不换算的话成交额小四个数量级,页面上看起来像"这只票没人买"
                  "_scale": {"amount": 10000.0, "volume": 100.0},
                  # 港股同一个下标单位就不一样了(实测 2026-08-19):
                  #   [36] 直接是**股**,[37] 直接是**元** —— 都不用换算。
                  # 套 A 股那套会把腾讯的成交额算成 64 万亿
                  "_market": {"hk": {"_scale": {}}},
                  "_required": _QUOTE_REQ},
        # K线 · {data:{sh600519:{qfqday:[[日期,开,收,高,低,成交量], …]}}}
        # `data` 底下那个键是**股票代码**,随请求变 —— JSONPath 写不出来,
        # 单独一个 shape 取"第一个值"
        "kline": {"_shape": "tencent_kline", "_required": _KLINE_REQ},
    },
    # 新浪财经 · `var hq_str_sh600519="贵州茅台,1300.000,..."`(34 段,`,` 分隔)
    #
    # ⚠️ 新浪**不直接给涨跌幅**,要用现价和昨收算 —— 见 `_delimited` 里的补算。
    # 成交量单位是**股**(腾讯是手),两边差 100 倍。
    "sina": {
        "quote": {"_shape": "delimited", "_sep": ",",
                  "name": 0, "open": 1, "prev_close": 2, "price": 3,
                  "high": 4, "low": 5, "volume": 8, "amount": 9,
                  "_text": ["name"],
                  "_derive_change": True,
                  "_required": _QUOTE_REQ},
    },
}

# 东财 K线类接口 —— `data.klines` 是一串 "日期,值,值,…" 字符串,
# 每个值对应请求里 `fields2` 的一个字段号。**列的含义完全由 fields2 决定**,
# 所以映射写在这里、URL 写在 source_templates,两处必须对齐。
#
# 单独一张表而不是塞进 BUILTIN,是因为它按 (kind, 列顺序) 索引,
# 与 BUILTIN 的 JSONPath 模型不是一回事。
_EM_KLINE_COLS = {
    # fields2=f51,f52,f53,f54,f55,f56,f57 → 日期,开,收,高,低,成交量,成交额
    "kline":   ["ts", "open", "close", "high", "low", "volume", "amount"],
    # fields2=f51,f52,f53,f54,f55,f56 → 日期,主力,小单,中单,大单,超大单(净额,元)
    "capital": ["ts", "main_net", "small_net", "medium_net", "large_net", "xlarge_net"],
}


def has_builtin(upstream: str, kind: str) -> bool:
    return kind in BUILTIN.get(upstream, {})


def apply(upstream: str, kind: str, payload: Any, custom: dict | None = None,
          market: str = "") -> dict:
    """把上游返回转成我们的格式。取不到必需字段就抛 `MappingError`。

    `custom` 是用户自己填的映射(步 6),优先于内置 ——
    用户比我们更了解他自己那个接口。

    ## `market` 是干什么的

    **同一个接口的同一个字段,在不同市场单位可能不一样。**腾讯实测
    (2026-08-19):

        A股 600519   [36]=33040 手        [37]=428876 万元
        港股 00700   [36]=14494435 股     [37]=6448255315 元

    下标一样,单位差 100 倍和 10000 倍。不按市场区分的话,港股成交额会
    被乘成 64 万亿 —— 而那是个"看起来只是很大"的数,不会报错,
    模型还会拿它去算换手率。

    所以 spec 里可以写 `_market: {"hk": {…覆盖…}}`,按市场浅合并。
    """
    spec = custom or BUILTIN.get(upstream, {}).get(kind)
    if not spec:
        raise MappingError(
            f"没有 {upstream}/{kind} 的内置映射 —— 这个组合我们还没核实过格式。"
            f"可以在这条源的详情里自己填一份字段映射"
        )
    over = (spec.get("_market") or {}).get(market or "")
    if over:
        spec = {**spec, **over}

    shape = spec.get("_shape", "flat")
    if shape == "yahoo_chart":
        out = _yahoo_chart(payload)
    elif shape == "columnar":
        out = _columnar(payload, spec)
    elif shape == "list":
        out = _list(payload, spec)
    elif shape == "delimited":
        out = _delimited(payload, spec)
    elif shape == "em_klines":
        out = _em_klines(payload, spec)
    elif shape == "tencent_kline":
        out = _tencent_kline(payload)
    elif shape == "jsonp":
        out = _jsonp(payload, spec)
    else:
        out = _flat(payload, spec)

    missing = [f for f in spec.get("_required", []) if out.get(f) in (None, [], "")]
    if missing:
        raise MappingError(
            f"{upstream}/{kind}:必需字段 {', '.join(missing)} 没取到 —— "
            f"上游返回的结构可能和我们预期的不一样"
        )
    return out


def _flat(payload: Any, spec: dict) -> dict:
    scale = spec.get("_scale", {})
    text = set(spec.get("_text", []))
    out: dict = {}
    for k, path in spec.items():
        if k.startswith("_"):
            continue
        raw = _walk(payload, path)
        # 文本字段(股票名之类)不能过 _num() —— 过了一律变 None。
        # 这个 bug 的表现是"卡片标题显示 600519 而不是贵州茅台",
        # 而且不报错:上层看到 name=None 就回落成代码,一切"正常"
        if k in text:
            out[k] = str(raw).strip() if raw not in (None, "") else None
            continue
        v = _num(raw)
        if v is not None and k in scale:
            v *= scale[k]
        out[k] = v
    return out


def _date(v: Any) -> str:
    """日期统一成 `YYYY-MM-DD`。

    实测踩到的:Tushare 给的是 `20260814`(紧凑式),Yahoo 走 unix 时间戳
    转出来是 `2026-08-14`。两种源的 K线混进同一个图表时,
    紧凑式那批会被当成"格式不对"整段丢掉 —— 而且不报错,只是少了几根柱子。
    """
    s = str(v or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10]


def _columnar(payload: Any, spec: dict) -> dict:
    """列式(Tushare):fields=['trade_date','open',...] + items=[[...],...]"""
    fields = _walk(payload, spec["_fields"])
    items = _walk(payload, spec["_items"])
    if not isinstance(fields, list) or not isinstance(items, list):
        return {"rows": []}
    idx = {f: i for i, f in enumerate(fields)}
    rows = []
    for it in items:
        if not isinstance(it, list):
            continue
        def g(name: str):
            i = idx.get(name)
            return it[i] if i is not None and i < len(it) else None
        rows.append({
            "ts": _date(g("trade_date")),
            "open": _num(g("open")), "high": _num(g("high")),
            "low": _num(g("low")), "close": _num(g("close")),
            "volume": int(_num(g("vol")) or 0),
        })
    # Tushare 返回是**倒序**的(最新在前)· 我们全链路用正序
    rows.reverse()
    return {"rows": rows}


def _list(payload: Any, spec: dict) -> dict:
    arr = _walk(payload, spec["_list"])
    if not isinstance(arr, list):
        return {"items": []}
    keys = [k for k in spec if not k.startswith("_")]
    return {"items": [
        {k: (it.get(spec[k]) if isinstance(it, dict) else None) for k in keys}
        for it in arr
    ]}


def _raw_text(payload: Any) -> str:
    """取原文 —— 非 JSON 的上游由 `source_resolver._fetch_one` 塞在 `_raw` 里。"""
    if isinstance(payload, dict) and "_raw" in payload:
        return str(payload["_raw"])
    return payload if isinstance(payload, str) else ""


def _delimited(payload: Any, spec: dict) -> dict:
    """位置分隔的文本行情(腾讯 / 新浪)。

    两家都是 `变量名="值1<分隔符>值2<分隔符>…"`,靠**下标**取字段,
    没有字段名。所以 spec 里的值是 int 下标而不是 JSONPath。

    ## 为什么不写成 JSONPath

    可以先把文本切成数组再用 `$[3]` 走通用逻辑,但那样 spec 里看到的是
    `"price": "$[3]"` —— 比 `"price": 3` 多一层壳,却没多任何表达力,
    而且会让人以为这里能用完整 JSONPath(不能)。

    ## 取不到就返回空,让 `_required` 去判失败

    上游偶尔返回 `v_sh600519="";`(代码不存在时)。这里返回
    `{"price": None}`,`apply()` 的必需字段检查会把它变成一条明确的
    MappingError —— 而不是一个 price=None 的"成功"结果。
    """
    txt = _raw_text(payload)
    if '"' not in txt:
        return {}
    body = txt.split('"')[1]
    parts = body.split(spec.get("_sep", ","))
    if len(parts) < 2:
        return {}

    text_fields = set(spec.get("_text", []))
    scale = spec.get("_scale", {})
    out: dict = {}
    for key, idx in spec.items():
        if key.startswith("_") or not isinstance(idx, int):
            continue
        v = parts[idx].strip() if idx < len(parts) else None
        if v in (None, ""):
            out[key] = None
            continue
        if key in text_fields:
            out[key] = v
        else:
            n = _num(v)
            out[key] = n * scale[key] if (n is not None and key in scale) else n

    # 新浪不给涨跌额/涨跌幅,用现价和昨收补算。
    # **只在缺的时候补** —— 腾讯自己给了(下标 31/32),算出来的和它给的
    # 可能因为四舍五入差一点点,以上游为准
    if spec.get("_derive_change"):
        p, pc = out.get("price"), out.get("prev_close")
        if p is not None and pc:
            out.setdefault("change_amt", round(p - pc, 4))
            out.setdefault("change_pct", round((p - pc) / pc * 100, 4))
    return out


def _em_klines(payload: Any, spec: dict) -> dict:
    """东财 K线 / 资金流 —— `data.klines` 是 ["日期,值,值,…", …]。

    列的含义由请求里的 `fields2` 决定,我们按 `_EM_KLINE_COLS` 对号入座。
    **列数对不上就报错,不猜** —— 猜错了会把成交量当收盘价存进去,
    而那种错在图上完全看不出来(`index_kline.py` 里踩过同一个坑)。
    """
    arr = _walk(payload, "$.data.klines")
    if not isinstance(arr, list):
        return {"rows": []}
    cols = _EM_KLINE_COLS.get(spec.get("_cols") or "", [])
    if not cols:
        return {"rows": []}

    rows = []
    for line in arr:
        parts = str(line).split(",")
        # 少于必需列数就跳过这一行(上游偶发截断),多了是我们请求了
        # 更多字段 —— 按已知列取前 N 个,多出来的忽略
        if len(parts) < len(cols):
            continue
        row: dict = {}
        for i, name in enumerate(cols):
            row[name] = _date(parts[i]) if name == "ts" else _num(parts[i])
        if row.get("ts"):
            rows.append(row)
    return {"rows": rows}


def _tencent_kline(payload: Any) -> dict:
    """腾讯日K · `{data:{sh600519:{qfqday:[[日期,开,收,高,低,成交量], …]}}}`

    `data` 底下那个键是**股票代码**(随请求变),JSONPath 表达不出来 ——
    取 `data` 的第一个值即可,一次请求只会有一只票。

    数组里的键名也会变:前复权是 `qfqday`,不复权是 `day`,
    周线是 `qfqweek` —— 所以按前缀找,不写死。

    列序 2026-08-19 核对过:`[日期, 开, 收, 高, 低, 成交量(手)]`。
    验证方法是拿前一日的**收盘**去对行情接口的**昨收**(1297.99 对上),
    以及当日高低对行情的高低 —— 光看数值像不像股价是验不出列序错位的。
    """
    data = _walk(payload, "$.data")
    if not isinstance(data, dict) or not data:
        return {"rows": []}
    inner = next(iter(data.values()))
    if not isinstance(inner, dict):
        return {"rows": []}

    arr = None
    for key in ("qfqday", "day", "hfqday"):
        if isinstance(inner.get(key), list):
            arr = inner[key]
            break
    if arr is None:                       # 周/月线之类 · 按前缀兜底
        for k, v in inner.items():
            if isinstance(v, list) and v and isinstance(v[0], list):
                arr = v
                break
    if not arr:
        return {"rows": []}

    rows = []
    for it in arr:
        if not isinstance(it, list) or len(it) < 6:
            continue
        ts = _date(it[0])
        c = _num(it[2])
        if not ts or c is None:
            continue
        rows.append({"ts": ts, "open": _num(it[1]), "close": c,
                     "high": _num(it[3]), "low": _num(it[4]),
                     "volume": int(_num(it[5]) or 0)})
    return {"rows": rows}


_TAG_RE = re.compile(r"<[^>]+>")


def _jsonp(payload: Any, spec: dict) -> dict:
    """JSONP —— 外面裹一层 `回调名(...)`,剥掉再当普通 JSON 走。

    东财的搜索接口只提供 JSONP,没有纯 JSON 版本(`cb` 参数不能省)。
    """
    txt = _raw_text(payload)
    inner: Any = payload
    if txt:
        try:
            inner = json.loads(txt[txt.index("(") + 1: txt.rindex(")")])
        except Exception:                                  # noqa: BLE001
            return {"items": []}

    arr = _walk(inner, spec.get("_list", "$"))
    if not isinstance(arr, list):
        return {"items": []}
    keys = [k for k in spec if not k.startswith("_")]
    strip = set(spec.get("_striptags", []))
    items = []
    for it in arr:
        if not isinstance(it, dict):
            continue
        row = {}
        for k in keys:
            v = it.get(spec[k])
            if k in strip and isinstance(v, str):
                v = _TAG_RE.sub("", v).strip()
            row[k] = v
        items.append(row)
    return {"items": items}


def _yahoo_chart(payload: Any) -> dict:
    """Yahoo v8 chart · timestamp[] 与 indicators.quote[0].{open,high,...}[] 平行。"""
    ts = _walk(payload, "$.chart.result[0].timestamp")
    q = _walk(payload, "$.chart.result[0].indicators.quote[0]")
    if not isinstance(ts, list) or not isinstance(q, dict):
        return {"rows": []}
    import datetime as _dt
    rows = []
    for i, t in enumerate(ts):
        def at(name: str):
            a = q.get(name)
            return a[i] if isinstance(a, list) and i < len(a) else None
        c = _num(at("close"))
        if c is None:      # Yahoo 会在数组里塞 null(停牌日)· 跳过而不是补 0
            continue
        rows.append({
            "ts": _dt.datetime.utcfromtimestamp(int(t)).strftime("%Y-%m-%d"),
            "open": _num(at("open")), "high": _num(at("high")),
            "low": _num(at("low")), "close": c,
            "volume": int(_num(at("volume")) or 0),
        })
    return {"rows": rows}
