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
}


def has_builtin(upstream: str, kind: str) -> bool:
    return kind in BUILTIN.get(upstream, {})


def apply(upstream: str, kind: str, payload: Any, custom: dict | None = None) -> dict:
    """把上游返回转成我们的格式。取不到必需字段就抛 `MappingError`。

    `custom` 是用户自己填的映射(步 6),优先于内置 ——
    用户比我们更了解他自己那个接口。
    """
    spec = custom or BUILTIN.get(upstream, {}).get(kind)
    if not spec:
        raise MappingError(
            f"没有 {upstream}/{kind} 的内置映射 —— 这个组合我们还没核实过格式。"
            f"可以在这条源的详情里自己填一份字段映射"
        )

    shape = spec.get("_shape", "flat")
    if shape == "yahoo_chart":
        out = _yahoo_chart(payload)
    elif shape == "columnar":
        out = _columnar(payload, spec)
    elif shape == "list":
        out = _list(payload, spec)
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
