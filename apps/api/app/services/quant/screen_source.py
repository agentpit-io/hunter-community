"""全市场扫描 —— 取数通道 + 护栏。

对接上游扫描服务的内部端点(地址见 _BASE,那是它网页版筛选器自己调的接口)。
免 key、免登录,国内 IP 直连可用(2026-09-10 本机实测:中位 583ms,
连打 30 次全 200)。

## 定位:探索性初筛工具,不是数据源

**它不进 `factor_value` 表、不进回测、不进推送。** 三条理由:

1. **没有历史序列。** 返回的是当前时点的横截面快照。`close|1M` 不是"一个月前的
   收盘价",而是"月线周期上最新那根的收盘",实测 NVDA 的 close / close|1W /
   close|1M 三个值完全相同(223.67)。拿它算动量会得到恒等于 0 的因子。
   要涨跌幅得用 `Perf.W` / `Perf.1M` / `Perf.Y` 这类预算好的字段。
2. **不是官方 API,没有 SLA。** 反向工程来的内部端点,上游的服务条款
   禁止自动化访问。随时可能改字段或封 IP,不能让生产链路依赖它。
3. **口径与站内数据源不一致。** 见下面 MARKET_CAP_WARN。

## 必须下推的三个过滤 —— 这是正确性前提,不是优化

不加 `type=stock` + `is_primary`,结果里会混进 ETF、优先股份额、权证。
实测第一次拉美股就拿到 `NASDAQ:GOOGM` / `GOOGN`(Alphabet 的可转换优先股
存托份额):**市值字段直接继承母公司的 4.12 万亿,PE 却是 2.38**。
按市值排序时它们会插在 GOOG 前面。

加上过滤后字段填充率也跟着好转(A 股实测:市值 70%→100%,ROE 68%→97%)。

**但前两条挡不住普通优先股。** `NYSE:NEE/PW` 这类 type=stock、is_primary=True
全都满足,只有 `typespecs=["preferred"]` 能把它和普通股的 `["common"]` 区分开。
所以第三条 `typespecs has common` 同样是必须的 —— 少了它,2026-09-10 一次
真实扫描的前两名就是两只市值和 PE 全为 null 的优先股。
实测影响:美股 7486 → 7405(剔 81 只),A 股 / 港股本来就没有,不受影响。

## 时效与口径 —— 每次返回都要带上,不能只写在文档里

* **全市场都是延迟 15 分钟**(`update_mode = delayed_streaming_900`,
  美股/港股/A 股无一例外)。免订阅拿不到实时,盘中信号别用它。
* **财报字段按上市地货币计价**,港股是 HKD(腾讯营收 TTM 给的是 8814 亿 **HKD**)。
  跨市场混合排序不换汇就是错的,所以 `currency` 永远在返回列里。
* **市值字段口径与国内源不一致**,见 MARKET_CAP_WARN。
"""
from __future__ import annotations

import logging
import re
import threading
import time

import httpx

from app.services.quant import screen_dsl, screen_rs, vcp
from app.services.quant.screen_dsl import Compiled, ScreenError

log = logging.getLogger(__name__)


class NeedsAI(ScreenError):
    """本地两条路(脚本 / 关键词)都不通 —— 可以问用户要不要花 token 叫 AI。

    单独一个类型是为了让路由能把「可以试 AI」这个信号带给前端。
    用普通 ScreenError 的话前端只能靠匹配报错文本来猜,那种耦合迟早断。
    """


# ⚠️ 下面三个值是**上游要求的**,不是可配项:换掉 Origin / Referer 会被直接拒。
# 对外文案、报错、注释一律不再点名上游是谁(2026-09-10 产品要求),
# 但这三行藏不住 —— 本仓是公开仓,谁都读得到。真要隐藏得把整个通道
# 做成可插拔的私有实现,那是另一件事。
_BASE = "https://scanner.tradingview.com"
_TIMEOUT = 25.0
_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
}

# 一页拉多少 —— 实测单请求拿全美股 7487 只只要 ~3s,分页纯粹是防上游哪天加上限。
_PAGE = 2000
_MAX_ROWS = 20000

# 翻页**必须**带一个稳定排序 —— 这不是优化,是正确性。
#
# 2026-09-10 实测:不带 sort 分四页拉美股(每页 2000),累计 8000 条里只有
# 4914 只唯一,重叠 3086 —— 也就是说约 2500 只票**一次都没被扫到**。
# 上游默认顺序在请求之间不稳定,页与页会互相重叠+错漏。
# 症状极隐蔽:结果看起来正常(有命中、有数据),只是悄悄少了三分之一的池子。
# 加上 sortBy=name 后 7487/7487 零重叠。
_SORT = {"sortBy": "name", "sortOrder": "asc"}


# ═══════════════════════════════════════════════════════════════
# 市场
# ═══════════════════════════════════════════════════════════════

class MarketDef:
    def __init__(self, key: str, tv: str, label: str, currency: str, note: str = ""):
        self.key, self.tv, self.label, self.currency, self.note = key, tv, label, currency, note


# 只开 A 股 / 港股 / 美股(2026-09-10 用户指定)。
#
# 上游那边日/韩/印/英股同样能扫(实测都是 200,覆盖 4386 / 4302 / 8659 / 9447 只),
# 但站内没有任何配套能力去接:代码归一化(market_source.market_of)只认
# A/港/美三种形态,自选、K线、财报、深度分析全都不支持别的市场。
# 扫得出来却什么也做不了,只会让人以为站内支持这些市场。
# 真要开的时候,先补 market_of 与下游链路,再往这里加。
MARKETS: dict[str, MarketDef] = {
    "a":  MarketDef("a", "china", "A股", "CNY",
                    "站内 A 股已有腾讯 / AKShare 直连通道,数据更贴国内口径。"
                    "这里主要用于快速初筛,不要用它替代站内数据。"),
    "hk": MarketDef("hk", "hongkong", "港股", "HKD"),
    "us": MarketDef("us", "america", "美股", "USD"),
}

# 顺序与 source_catalog.MARKET_ORDER 一致(A 股在最前)
MARKET_ORDER = ["a", "hk", "us"]

# 永远下推的过滤 —— 见模块 docstring
BASE_FILTER = [
    {"left": "type", "operation": "equal", "right": "stock"},
    {"left": "is_primary", "operation": "equal", "right": True},
    # typespecs 这条不能省。`type=stock` + `is_primary=True` **挡不住优先股** ——
    # 2026-09-10 一次真实扫描的结果里出现了 NYSE:NEE/PW 和 OAK/PA,
    # 实测它们 type=stock、is_primary=True,只有 typespecs=["preferred"] 能区分
    # (普通股是 ["common"])。它们的市值/PE 全是 null,混在选股结果里毫无意义。
    # 实测影响:美股 7486 → 7405(剔 81 只),A 股和港股本来就没有,不受影响。
    {"left": "typespecs", "operation": "has", "right": ["common"]},
]

# 永远带回来的列(不管脚本用不用)。currency 是给跨市场比较兜底的,
# description 是股票名 —— 只给代码的结果没法看。
ALWAYS_COLS = ["name", "description", "close", "currency", "volume"]

# ⚠️ 这两条的**开头几个字**被前端 screener.html 的 DROP_PREFIX 用来过滤显示
# (产品要求这两条不出现在筛选器页面上)。改文案要同步改那里,
# 否则它们会悄悄冒回页面。MCP 与 API 响应仍然带着它们 —— 模型需要知道数据是延迟的。
DELAY_WARN = "免订阅通道数据延迟 15 分钟,盘中信号请勿依赖。"

MARKET_CAP_WARN = (
    "market_cap_basic 是扫描源口径,与站内国内源实测有系统性差异:"
    "A 股 10 只抽样里中芯国际差 -37%、比亚迪 -8%、格力 -7%(A+H 两地上市股尤其大);"
    "且它与 total_shares_outstanding_current 自身对不上(close×股本 / 市值 = 0.36~0.63)。"
    "可以用来排序和粗筛,不要当作市值真值,更不要写进因子。"
)


# ═══════════════════════════════════════════════════════════════
# metainfo 缓存 —— 字段白名单跟着上游走,不在代码里维护会过期的副本
# ═══════════════════════════════════════════════════════════════

class _Meta:
    def __init__(self, names: set[str], sma: list[int], ema: list[int], rsi: list[int]):
        self.names, self.sma, self.ema, self.rsi = names, sma, ema, rsi


_META_TTL = 6 * 3600
_meta_cache: dict[str, tuple[float, _Meta]] = {}
_meta_lock = threading.Lock()

_PERIOD_RE = {
    "SMA": re.compile(r"^SMA(\d+)$"),
    "EMA": re.compile(r"^EMA(\d+)$"),
    "RSI": re.compile(r"^RSI(\d+)$"),
}


def get_meta(market_key: str) -> _Meta:
    md = _market(market_key)
    now = time.time()
    with _meta_lock:
        hit = _meta_cache.get(md.tv)
        if hit and now - hit[0] < _META_TTL:
            return hit[1]
    try:
        with httpx.Client(timeout=_TIMEOUT, headers=_UA) as cli:
            r = cli.get(f"{_BASE}/{md.tv}/metainfo")
            r.raise_for_status()
            raw = r.json().get("fields") or []
    except Exception as e:                                        # noqa: BLE001
        raise ScreenError(f"拉扫描源字段表失败:{type(e).__name__} · {e}") from e

    names = {f.get("n") for f in raw if f.get("n")}
    # name / description 不在 metainfo 里但实际可用(实测能取到值),补进白名单,
    # 否则脚本里写 description 会被判成"不认识的字段"
    names.update(ALWAYS_COLS)
    # RS 相对强度是我们在全市场快照上自己算的(screen_rs),扫描源没有这两个字段。
    # 放进白名单才能在脚本里写、在「可用字段」里搜到。
    names.update(screen_rs.RS_FIELDS)
    # VCP 字段同理:每晚用全市场日线算好(rs_history + vcp.py),扫描源没有。
    # vcp_depths 是展示用的文字,不进白名单 —— 写进条件里拿文字比大小没有意义
    names.update(vcp.FIELDS)

    def periods(prefix: str) -> list[int]:
        rx = _PERIOD_RE[prefix]
        return sorted(int(rx.match(n).group(1)) for n in names
                      if isinstance(n, str) and rx.match(n))

    meta = _Meta(names, periods("SMA"), periods("EMA"), periods("RSI"))
    with _meta_lock:
        _meta_cache[md.tv] = (now, meta)
    return meta


def _market(key: str) -> MarketDef:
    md = MARKETS.get((key or "").lower())
    if md is None:
        raise ScreenError(
            f"不支持的市场 {key!r}。可选:"
            + " · ".join(f"{k}({MARKETS[k].label})" for k in MARKET_ORDER))
    return md


# ═══════════════════════════════════════════════════════════════
# 取数
# ═══════════════════════════════════════════════════════════════

def _normalize_code(tv_symbol: str, market_key: str, name: str) -> str:
    """上游符号 → 站内代码格式。

    站内格式见 market_source.market_of:6 位纯数字 = A 股,5 位 = 港股,
    含字母 = 美股。港股上游给的是 `HKEX:700`,站内要 `00700` ——
    **必须补零到 5 位**,否则 market_of('700') 会判成 A 股然后去深交所找。
    """
    bare = tv_symbol.split(":", 1)[-1] if ":" in tv_symbol else tv_symbol
    bare = (name or bare or "").strip() or bare
    if market_key == "hk" and bare.isdigit():
        return bare.zfill(5)
    return bare


def fetch_rows(market_key: str, columns: list[str], limit_scan: int = _MAX_ROWS,
               extra_filter: list | None = None) -> tuple[list[dict], int]:
    """拉全市场。→ (行, 上游 totalCount)

    行是 dict:上游字段名 → 值,外加 `_symbol` / `_code`。
    """
    md = _market(market_key)
    cols: list[str] = []
    for c in list(ALWAYS_COLS) + list(columns):
        if c not in cols:
            cols.append(c)

    rows: list[dict] = []
    seen: set[str] = set()
    total = 0
    offset = 0
    with httpx.Client(timeout=_TIMEOUT, headers=_UA) as cli:
        while offset < limit_scan:
            body = {
                "filter": list(BASE_FILTER) + list(extra_filter or []),
                "options": {"lang": "en"},   # zh_CN 实测也只回英文名,没有中文名可拿
                "markets": [md.tv],
                "symbols": {"query": {"types": []}, "tickers": []},
                "columns": cols,
                "sort": _SORT,
                "range": [offset, min(offset + _PAGE, limit_scan)],
            }
            try:
                r = cli.post(f"{_BASE}/{md.tv}/scan", json=body)
            except Exception as e:                                # noqa: BLE001
                raise ScreenError(
                    f"连扫描源失败:{type(e).__name__} · {e}。"
                    f"这是免费的非官方通道,没有 SLA —— 稍后重试,或改用站内数据源。") from e
            if r.status_code != 200:
                raise ScreenError(
                    f"扫描源返回 HTTP {r.status_code}。"
                    + ("被限流了,等一会儿再试。" if r.status_code == 429 else
                       f"响应片段:{r.text[:200]}"))
            data = r.json()
            total = data.get("totalCount") or total
            batch = data.get("data") or []
            if not batch:
                break
            for item in batch:
                sym = item.get("s") or ""
                # 去重是兜底 —— _SORT 已经让翻页不再重叠,但上游一旦改行为,
                # 宁可少算也不要把同一只票重复计进命中数
                if sym in seen:
                    continue
                seen.add(sym)
                d = dict(zip(cols, item.get("d") or []))
                d["_symbol"] = sym
                d["_code"] = _normalize_code(sym, market_key, d.get("name") or "")
                rows.append(d)
            if len(batch) < (body["range"][1] - body["range"][0]):
                break
            offset += _PAGE
    return rows, total


# ═══════════════════════════════════════════════════════════════
# 跑脚本
# ═══════════════════════════════════════════════════════════════

# 结果表里额外展示的列(有就带,没有不报错)—— 让命中结果不用再点进去看
_DISPLAY_EXTRA = ["market_cap_basic", "price_earnings_ttm", "RSI", "change",
                  "sector", "average_volume_30d_calc"]


def run_script(script: str, market_key: str = "us", limit: int = 100,
               sort_by: str | None = None, descending: bool = True) -> dict:
    """编译 → 拉数 → 本地求值。返回体结构见 docs-hunter / 前端 screener.html。"""
    md = _market(market_key)
    if not (script or "").strip():
        raise ScreenError("脚本是空的。至少要有一句 `plot scan = <条件>;`")
    if len(script) > 20000:
        raise ScreenError("脚本太长(上限 20000 字符)")
    limit = max(1, min(int(limit or 100), 500))

    meta = get_meta(market_key)

    def has_field(n: str) -> bool:
        return n in meta.names

    c: Compiled = screen_dsl.compile_script(
        script, has_field, meta.sma, meta.ema, meta.rsi)
    cache = screen_dsl.build_resolver_cache(
        c, has_field, meta.sma, meta.ema, meta.rsi)

    want = list(c.fields)
    for extra in _DISPLAY_EXTRA:
        if extra not in want and has_field(extra):
            want.append(extra)

    # RS 字段**不能**原样发给扫描源(它没有这两列,会整批报错),
    # 换成算 RS 需要的四个来源列,拉回来之后在全市场上算好再补进每一行。
    uses_rs = any(f in screen_rs.RS_FIELDS for f in c.fields)
    vcp_used = [f for f in c.fields if f in vcp.FIELDS]
    uses_vcp = bool(vcp_used)
    if uses_vcp and vcp.DISPLAY not in want:
        want.append(vcp.DISPLAY)      # 结果表里顺带显示「25.7→13.0→6.0」,一眼看出每次多深
    ours = set(screen_rs.RS_FIELDS) | set(vcp.FIELDS) | {vcp.DISPLAY}
    req_cols = [f for f in want if f not in ours]
    if uses_rs:
        for col in screen_rs.RS_SOURCE_COLS:
            if col not in req_cols:
                req_cols.append(col)

    t0 = time.time()
    rows, total = fetch_rows(market_key, req_cols)
    fetch_ms = (time.time() - t0) * 1000

    rs_stat = None
    vcp_stat = None
    hist = None
    if uses_rs or uses_vcp:
        # 每晚落库的全市场日线统计(RS 线上涨天数、精确 RS Raw、VCP)。读的是一张
        # 每市场几千行的小表,不是逐日明细;表还没建 / 读失败 → 空
        from app.services.quant import rs_history
        hist, _ = rs_history.load_stats(md.key)
    if uses_rs:
        # **在求值之前**、对全市场算 —— 评级的分母是全市场,不是命中结果
        rs_stat = screen_rs.inject(rows, md.key, hist)
    if uses_vcp:
        # 与 RS 线同一套新鲜度规则:超过 HIST_STALE_DAYS 天没更新就整批给空,
        # 拿一周前的形态判断「现在是不是在收缩」会给错答案
        from datetime import date as _date
        v_as_of = max((v["as_of"] for v in (hist or {}).values()), default=None)
        v_stale = v_as_of is None or (_date.today() - v_as_of).days > screen_rs.HIST_STALE_DAYS
        fresh_n = sum(1 for v in (hist or {}).values() if v["as_of"] == v_as_of)
        vcp_stat = {"as_of": v_as_of, "stale": v_stale, "fresh": fresh_n,
                    "n": vcp.inject(rows, hist, v_stale, vcp_used)}

    t1 = time.time()
    hits, skipped, missing = screen_dsl.evaluate_detail(c, rows, cache)
    eval_ms = (time.time() - t1) * 1000

    if sort_by:
        if sort_by not in want and sort_by not in screen_dsl._PRICE:
            raise ScreenError(f"排序字段 {sort_by!r} 不在这次请求的列里")
        # 空值不论升序降序都排最后 —— 原来用 (is None, 值) 当 key 再 reverse,
        # 降序时空值整批跑到最前面,用户点「降序」看到的第一屏全是 —
        has = [r for r in hits if r.get(sort_by) is not None]
        has.sort(key=lambda r: r.get(sort_by), reverse=descending)
        hits = has + [r for r in hits if r.get(sort_by) is None]

    warnings = [DELAY_WARN]
    if md.note:
        warnings.append(md.note)
    if rs_stat is not None:
        uses_rating = any(f in ("rs_rating", "rs_raw") for f in c.fields)
        uses_line = "rs_line_up_days" in c.fields
        as_of = rs_stat["hist_as_of"]
        if uses_line:
            if as_of is None:
                warnings.append(
                    f"{md.label}的全市场日线还没建好,「RS线上涨天数」这次全部为空"
                    f"(算不出,不是不满足)。日线由每晚的定时任务拉取。")
            elif rs_stat["hist_stale"]:
                warnings.append(
                    f"{md.label}的日线停在 {as_of},已超过 {screen_rs.HIST_STALE_DAYS} 天没更新"
                    f"(每晚的定时任务可能坏了)—— 用过期的数据判断「连续上涨多少天」会给错答案,"
                    f"所以这次「RS线上涨天数」全部为空。")
            else:
                warnings.append(screen_rs.line_note(md.key, as_of))
        if uses_rating:
            warnings.append(screen_rs.METHOD_NOTE_EXACT.format(as_of=as_of)
                            if rs_stat["method"] == "exact" else screen_rs.METHOD_NOTE)
        if uses_rating and rs_stat["gated"]:
            warnings.append(
                f"本次全市场只有 {rs_stat['coverage']:.0%} 的股票能算出 RS,"
                f"低于 {screen_rs.RS_UNIVERSE_THRESHOLD:.0%} 的门槛 —— 在残缺的股票池里"
                f"排出来的 1–99 没有意义,所以这次 RS 评级全部不给。稍后重试。")
        else:
            pool_desc = ("交易所上市(不含 OTC 场外)、市值 ≥5000 万美元" if md.key == "us"
                         else "市值约 5000 万美元以上")
            msg = (f"RS 排名池:{pool_desc}的 {rs_stat['universe']} 只"
                   f"(与原项目口径一致,剔除 {rs_stat['excluded']} 只微盘股"
                   + ("与 OTC" if md.key == "us" else "") + ")")
            if uses_rating and rs_stat["young"]:
                msg += (f";其中 {rs_stat['young']} 只上市不足 250 个交易日的次新股"
                        f"没有评级(没有真正的 12 个月涨幅,和别人不可比)")
            warnings.append(msg + "。")
    if vcp_stat is not None:
        a = vcp_stat["as_of"]
        if a is None:
            warnings.append(f"{md.label}的全市场日线还没建好,VCP 字段这次全部为空"
                            f"(算不出,不是不满足)。日线由每晚的定时任务拉取。")
        elif vcp_stat["stale"]:
            warnings.append(f"{md.label}的日线停在 {a},已超过 {screen_rs.HIST_STALE_DAYS} 天没更新"
                            f"(每晚的定时任务可能坏了)—— 用过期的形态判断「现在是不是在收缩」"
                            f"会给错答案,所以这次 VCP 字段全部为空。")
        elif vcp_stat["n"] < vcp_stat["fresh"] * 0.5:
            # 2026-09-11 上线当天就是这种情况:老日线只存了收盘价,没有最高/最低/成交量
            warnings.append(f"VCP 与量价字段要用日线里的最高价、最低价和成交量。这次只有 {vcp_stat['n']} 只"
                            f"算得出(日线里带着这三项的),其余 {vcp_stat['fresh'] - vcp_stat['n']} 只"
                            f"要等下一轮每晚定时任务整窗重拉之后才有 —— 它们是「算不出」,不是「不满足」。")
        else:
            warnings.append(vcp.NOTE.format(as_of=a))
    if any("market_cap" in f for f in want):
        warnings.append(MARKET_CAP_WARN)
    # 算不出的票具体缺哪个字段 —— 只说一个总数的话,用户没法判断
    # 该改哪条条件(2026-09-11 用户问:「2547 只具体缺少哪个字段?」)。
    missing_list = [
        {"field": f, "label": screen_dsl.field_label_cn(f), "count": n,
         "reason": screen_dsl.missing_reason(f)}
        for f, n in sorted(missing.items(), key=lambda kv: -kv[1])
    ]
    if skipped:
        parts = []
        for m in missing_list[:4]:
            nm = m["label"] or m["field"]
            parts.append(f"缺「{nm}」{m['count']} 只" +
                         (f"({m['reason']})" if m["reason"] else ""))
        warnings.append(
            f"{skipped} 只满足了其余条件,但缺数据无法判断,已排除在结果之外"
            f"(是「算不出」,不是「不满足」):" + ";".join(parts) + "。"
            + "如果某条条件缺得特别多,可以考虑去掉或换一个覆盖更全的字段。")

    picks = []
    for r in hits[:limit]:
        picks.append({
            "code": r.get("_code"),
            "symbol": r.get("_symbol"),
            "name": r.get("description") or r.get("name"),
            "close": r.get("close"),
            "currency": r.get("currency") or md.currency,
            "fields": {k: v for k, v in r.items() if not k.startswith("_")},
        })

    return {
        "market": md.key,
        "market_label": md.label,
        "universe_total": total,
        "scanned": len(rows),
        "matched": len(hits),
        "skipped_incomplete": skipped,
        "missing_fields": missing_list,
        "rs": rs_stat,
        "returned": len(picks),
        "picks": picks,
        "columns": want,
        "notes": c.notes,
        "warnings": warnings,
        "source": "全市场扫描源(非官方接口 · 延迟 15 分钟)",
        "timing_ms": {"fetch": round(fetch_ms), "evaluate": round(eval_ms)},
    }


def parse_script(script: str, market_key: str = "us", allow_ai: bool = False,
                 user_id: str | None = None) -> dict:
    """只解析、不拉数 —— 界面上点「生成」走这条,把脚本变成可视化条件行。

    和 run_script 共用同一个编译器,所以**界面上看到的条件就是真正会跑的条件**。
    另起一套解析会立刻漂移(前端认为的条件和后端跑的不是一回事),那种 bug
    极难发现,因为两边单独看都"对"。
    """
    md = _market(market_key)
    if not (script or "").strip():
        raise ScreenError("脚本是空的。至少要有一句 `plot scan = <条件>;`")
    if len(script) > 20000:
        raise ScreenError("脚本太长(上限 20000 字符)")
    meta = get_meta(market_key)

    def has_field(n: str) -> bool:
        return n in meta.names

    def _compile(src: str) -> Compiled:
        return screen_dsl.compile_script(src, has_field, meta.sma, meta.ema, meta.rsi)

    ai = None
    kw = None
    original_text = script
    try:
        c: Compiled = _compile(script)
    except ScreenError:
        # 解析不了 —— 按"最省"的顺序往下试。
        #
        # ① 写着 def/plot 却解析不过 = 他的脚本有错,把真实报错还给他。
        #    让翻译器去"猜他想写什么"再悄悄改成别的,是调脚本时最坏的体验。
        # ② 本地关键词匹配 —— **零 token**,覆盖「字段+比较符+数字」这类规整描述。
        # ③ 都不行才轮到 AI,而且**必须 allow_ai=True**(前端弹按钮、用户点了才传)。
        #    默认不花钱是这条链路的设计目标。
        from app.services.quant import screen_kw, screen_nl
        if screen_nl.looks_like_script(script):
            raise
        from app.services.quant import screen_learned
        try:
            # names:字段原名不分大小写(perf.y → Perf.Y)。learned:之前 AI 识别学来的对照表,
            # 只补规则认不出的句子。表读不到时是空 dict,本地规则照常工作。
            k = screen_kw.translate(script, has_field, meta.sma, meta.ema, meta.rsi,
                                    names=meta.names,
                                    learned=screen_learned.table_for(screen_kw.candidate_keys(script)))
            script = k["script"]
            # 对照表里的表达式可能已经过时(字段下线、周期不支持)—— 这一步编译不过
            # 就整体落回 AI,AI 的新结果会覆盖掉那条旧的
            c = _compile(script)
            kw = {"matched": k["matched"], "notes": k.get("notes") or [],
                  "source_text": original_text,
                  "script": script}
            hit_ids = sorted({m["learned"]["id"] for m in k["matched"]
                              if m.get("learned") and m["learned"].get("id")})
            if hit_ids:
                screen_learned.record_hits(hit_ids)
        except ScreenError as kw_err:
            if not allow_ai:
                # 不抛普通 ScreenError —— 路由要据此告诉前端"可以试试 AI"
                raise NeedsAI(str(kw_err)) from kw_err
            translated = screen_nl.translate(
                script, md.label, meta.sma, meta.ema, meta.rsi, validate=_compile)
            script = translated["script"]
            c = _compile(script)         # translate 里已经 validate 过,这里必成功
            ai = {k2: translated[k2] for k2 in
                  ("model", "attempts", "tokens_in", "tokens_out")}
            ai["source_text"] = original_text
            ai["script"] = script

    d = screen_dsl.decompose(script, c, has_field, meta.sma, meta.ema, meta.rsi)

    warnings = [DELAY_WARN]
    if md.note:
        warnings.append(md.note)
    if any("market_cap" in f for f in c.fields):
        warnings.append(MARKET_CAP_WARN)

    d["market"] = md.key
    d["market_label"] = md.label
    d["fields"] = c.fields
    d["warnings"] = warnings
    if kw:
        # 本地关键词匹配出来的 —— 同样要可核对:哪一句变成了哪个表达式。
        # 规则匹配不会像模型那样瞎编,但会**理解偏**(比如把"量"当成成交量而不是量比),
        # 所以逐句对照必须摆出来。
        d["kw"] = kw
        # 有损近似(上穿按"当前在上方"处理)要单独亮出来 —— 混在条件里用户看不出来
        for n in kw.get("notes") or []:
            warnings.append(n)
        n_learned = sum(1 for m in kw.get("matched") or [] if m.get("learned"))
        if n_learned:
            warnings.append(
                f"其中 {n_learned} 句来自之前的 AI 识别(已记在对照表里,这次没花 token)。"
                "对照表是 AI 学来的,**请核对**;不对的话点那句旁边的「忘掉它」。")
        warnings.append(
            "以上条件由本地关键词匹配得出(未使用 AI,零成本),"
            "已通过语法与字段校验。逐句对照见上方折叠区,不对的话可直接改或改用 AI 识别。")
    if ai:
        # ── 学:把这次 AI 识别记进对照表(2026-09-11 用户要求)──────────
        # 下次同样的说法(数字可以不同)直接本地识别、零 token。
        # 学失败只记日志,绝不影响这次的结果 —— 用户要的是条件,不是对照表。
        ai["learned"] = 0
        try:
            exprs = screen_kw.inline_conditions(
                d.get("conditions") or [],
                d.get("plot_refs") or [] if d.get("combine") == "all" else [])
            if exprs:
                # 每条都得能**单独**编译 —— 下次是拆开、换了数字再用的
                for e in exprs:
                    _compile(f"def c_ = {e};\nplot scan = c_;")
                def _rule_ok(cl: str) -> bool:
                    return screen_kw.rule_match(cl, has_field, meta.sma, meta.ema, meta.rsi,
                                                meta.names) is not None
                entries = screen_kw.learn_entries(original_text, exprs, _rule_ok)
                ai["learned"] = screen_learned.learn(
                    entries, original_text, ai.get("model"), md.key, user_id)
        except Exception as e:                                    # noqa: BLE001
            # 本模块用标准库 logging(第 54 行 log),不是 loguru —— 写成 logger 的话
            # except 里会再抛 NameError,把这次 AI 识别的结果整个打成 500
            log.warning("[screen_learned] 这次 AI 识别没记进对照表: %s", e)

        # 让前端能明确标出"这几条是 AI 翻的",并且把生成的脚本亮出来给人核对。
        # AI 产出的东西必须可审计 —— 用户至少要能看见它到底写了什么才敢用。
        d["ai"] = ai
        warnings.append(
            f"以上条件由 {ai['model']} 根据你的描述自动翻译,已通过语法与字段校验,"
            f"但**是否符合你的本意需要你自己确认**。跑扫描前请逐条核对。")
    return d


# ═══════════════════════════════════════════════════════════════
# 预置脚本 —— 前端「示例」与 MCP 的 preset 参数共用一份
# ═══════════════════════════════════════════════════════════════

PRESETS = [
    {
        "key": "uptrend",
        "name": "上升趋势",
        "market": "us",
        "desc": "均线多头排列 + 逼近 52 周高点 + 有量。thinkorswim 经典 Stock Hacker 脚本。",
        "script": """# ===== 上升趋势 =====
# 均量条件(90 日均量 > 100 万股)
# 注:扫描源只有 10/30/60/90 天均量,原脚本的 Average(volume,100) 映射不了
def avgVol90 = Average(volume, 90);
def cond_avgVol = avgVol90 > 1000000;

# 当前成交量 > 100 万股
def cond_curVol = volume > 1000000;

# 股价 > 20
def cond_price = close > 20;

# 距 52 周高点 10% 以内
def high52 = Highest(high, 252);
def cond_52week = (high52 - close) / high52 <= 0.10;

# 均线多头排列
def sma20 = Average(close, 20);
def sma50 = Average(close, 50);
def sma200 = Average(close, 200);
def cond_sma20_50 = sma20 > sma50;
def cond_sma50_200 = sma50 > sma200;
def cond_price_sma50 = close > sma50;

plot scan = cond_avgVol
        and cond_curVol
        and cond_price
        and cond_52week
        and cond_sma20_50
        and cond_sma50_200
        and cond_price_sma50;
""",
    },
    {
        "key": "value_oversold",
        "name": "低估值超卖",
        "market": "a",
        "desc": "PE 与 PB 双低 + RSI 超卖 + 盈利能力为正。用来找左侧候选票。",
        "script": """# ===== 低估值 + 超卖 =====
def cond_pe  = price_earnings_ttm > 0 and price_earnings_ttm < 15;
def cond_pb  = price_book_fq < 2;
def cond_roe = return_on_equity > 8;
def cond_rsi = RSI() < 35;
def cond_liq = Average(volume, 30) > 5000000;

plot scan = cond_pe and cond_pb and cond_roe and cond_rsi and cond_liq;
""",
    },
    {
        "key": "breakout_volume",
        "name": "放量突破",
        "market": "us",
        "desc": "今日相对成交量放大 + 站上 20/50 日线 + 距 3 月高点 3% 以内。",
        "script": """# ===== 放量突破 =====
def cond_rvol  = relative_volume_10d_calc > 2;
def cond_ma    = close > Average(close, 20) and close > Average(close, 50);
def high3m     = Highest(high, 63);
def cond_near  = (high3m - close) / high3m <= 0.03;
def cond_price = close > 10;

plot scan = cond_rvol and cond_ma and cond_near and cond_price;
""",
    },
    {
        "key": "hk_dividend",
        "name": "港股高股息",
        "market": "hk",
        "desc": "股息率 > 5% + 低负债 + 盈利为正。注意金额字段是 HKD 计价。",
        "script": """# ===== 高股息 + 低负债 =====
def cond_div  = dividends_yield_current > 5;
def cond_debt = debt_to_equity < 1;
def cond_roe  = return_on_equity > 5;
def cond_liq  = Average(volume, 30) > 1000000;

plot scan = cond_div and cond_debt and cond_roe and cond_liq;
""",
    },
]


PRESETS.append({
    "key": "rs_leaders",
    "name": "强势股 RS≥80",
    "market": "us",
    "desc": "IBD 口径 RS 相对强度评级 ≥80(跑赢全市场 80% 的股票)+ 站上 50 日线 + 均线多头。",
    "script": """# ===== 强势股:RS 相对强度 + 趋势 =====
# RS() 是 IBD 口径的相对强度评级(1–99),在全市场里排名
def cond_rs    = RS() >= 80;
def cond_trend = close > Average(close, 50);
def cond_ma    = Average(close, 50) > Average(close, 200);
def cond_liq   = Average(volume, 30) > 500000;

plot scan = cond_rs and cond_trend and cond_ma and cond_liq;
""",
})

PRESETS.append({
    "key": "rs_line_up",
    "name": "RS线持续向上",
    "market": "us",
    "desc": "RS 评级 ≥80,且 RS 线(个股 ÷ 标普500)已连续 50 个交易日以上站在自身 21 日均线之上。",
    "script": """# ===== 强势且持续跑赢大盘 =====
# RSLineUpDays():RS 线(收盘 ÷ 基准指数)连续站在自身 21 日均线之上的交易日数
def cond_rs   = RS() >= 80;
def cond_line = RSLineUpDays() > 50;
def cond_liq  = Average(volume, 30) > 500000;

plot scan = cond_rs and cond_line and cond_liq;
""",
})

# 2026-09-11 用户给的五条 VCP 规则,逐条对应。字段口径见 services/quant/vcp.py。
# 阈值里只有「最低点量比 ≤ 0.6」是我们定的(用户原话「相对非常小」没给数),脚本里写明了。
# 「收缩到低点时成交量减小」**不要**写成「最低点量比 < 最后一次收缩量比」:最后一次收缩常常只有
# 四五天,低点那 3 天几乎就是整次收缩,两个数差不多,比出来是抛硬币(实测 STT 整次 0.53、
# 低点 0.56,量能明显枯竭却被卡掉)。用「最后一次收缩量比 < 1」。
PRESETS.append({
    "key": "vcp",
    "name": "VCP 波动收缩",
    "market": "us",
    "desc": "Minervini VCP:首次收缩 ≤50%、至少 3 次逐次变浅、末次 ≤10% 且低点缩量、"
            "近 4 周上涨放量下跌缩量、底部 3~12 个月。字段来自每晚的全市场日线。",
    "script": """# ===== VCP 波动收缩(Minervini)=====
# 字段每晚由全市场日线算出。一次收缩 = 摆动高点(比前后各 5 个交易日都高)到其后的低点;
# 往前数时前一次要明显更深、各次高点大致持平,才算连续收缩。

# 1. 第一次收缩在 50% 以内
def cond_first = vcp_first_depth <= 50;

# 2. 后一次比前一次浅,至少 3 次(逐次变浅已包含在收缩次数的数法里)
def cond_count = vcp_contractions >= 3;

# 3. 最后一次收缩在 10% 以内;这次收缩期间在缩量(日均量低于收缩前 50 天);
#    低点(当天及前 2 天)的日均量不到收缩前 50 天日均量的 6 成(「相对非常小」,可按需调)
def cond_last    = vcp_last_depth <= 10;
def cond_dryup   = vcp_last_vol_ratio < 1;
def cond_low_vol = vcp_low_vol_ratio <= 0.6;

# 4. 近 20 个交易日:上涨日的日均量大于下跌日,上涨天数多于下跌天数
def cond_ud_vol  = ud_vol_ratio_20d > 1;
def cond_ud_days = up_days_20d > down_days_20d;

# 5. 底部 3~12 个月(按交易日算:63~252 天)
def cond_base = vcp_base_days >= 63 and vcp_base_days <= 252;

# 6. 在枢轴附近:最后一次收缩的高点下方 5% 以内,或刚突破不超过 3%
#    (低点逐次抬高不用另写 —— 收缩次数的数法已经保证了)
def cond_pivot = vcp_pivot_dist >= -3 and vcp_pivot_dist <= 5;

plot scan = cond_first and cond_count and cond_last and cond_dryup
        and cond_low_vol and cond_ud_vol and cond_ud_days and cond_base and cond_pivot;
""",
})

# 2026-09-11 用户对快照版 VCP 脚本(git log --grep=VCP 那份)提的四点,改好的版本。
# 1) 扫描源的 High.5D / High.3M 实测是 4 / 61 根,不是 5 / 63 —— 换成自家日线的精确窗口;
# 2) 区间分母用最高价(= 回撤深度,和 vcp_*_depth 同一口径,最大 100%)。
#    用户建议用收盘价,理由是「低价股会失真」—— 比值与股价高低无关,这条理由不成立;
#    真正的问题是除以最低价在大回调时会放大(跌 50% 显示成 100%)。除以收盘价会让同一个区间
#    随今天收在区间哪里而变,所以取最高价;
# 3) 加低点抬高;4) 枢轴用 vcp_pivot_dist —— 拿近 1 月最高当枢轴,「≤ 枢轴 × 1.03」恒成立。
PRESETS.append({
    "key": "vcp_range",
    "name": "VCP 区间收缩",
    "market": "us",
    "desc": "3 个月 → 1 个月 → 5 天价格区间逐级收紧 + 低点抬高 + 在枢轴附近。"
            "窗口严格按最近 63 / 21 / 5 个交易日(自家日线),趋势模板与流动性来自扫描源。",
    "script": """# ===== VCP 区间收缩 · 精确交易日窗口 =====
# high_/low_Nd 严格是最近 N 根日线(扫描源的 High.5D / High.3M 实测只有 4 / 61 根)
# 区间 = (最高 - 最低) ÷ 最高 = 从高点回撤的深度,和 VCP 收缩深度同一口径
def rng3m = (high_63d - low_63d) / high_63d;
def rng1m = (high_21d - low_21d) / high_21d;
def rng5d = (high_5d - low_5d) / high_5d;

# 一、流动性与趋势模板
def c_price = close > 10;
def c_liq   = average_volume_30d_calc > 500000;
def c_trend = close > SMA50 and SMA50 > SMA150 and SMA150 > SMA200;
def c_rs    = rs_rating >= 70;

# 二、波动逐级收紧:前面有过像样回调 → 近 1 月收紧(但不是被收购锁价)→ 近 5 天更紧
def c_depth   = rng3m >= 0.15;
def c_tight1m = rng1m <= 0.10 and rng1m >= 0.02;
def c_shrink  = rng1m <= rng3m * 0.5;
def c_tight5d = rng5d <= rng1m * 0.6;

# 三、低点抬高:3 个月的最低点在一个月以前,最近一个月的低点至少高 2%
def c_higher_low = low_21d > low_63d * 1.02;

# 四、在枢轴附近:最后一次收缩的高点下方 5% 以内,或刚突破不超过 3%
def c_pivot = vcp_pivot_dist >= -3 and vcp_pivot_dist <= 5;

# 五、量能萎缩
def c_vdry = average_volume_10d_calc < average_volume_90d_calc;

plot scan = c_price and c_liq and c_trend and c_rs
        and c_depth and c_tight1m and c_shrink and c_tight5d
        and c_higher_low and c_pivot and c_vdry;
""",
})


def preset(key: str) -> dict | None:
    for p in PRESETS:
        if p["key"] == key:
            return p
    return None


def field_search(market_key: str, q: str, limit: int = 50) -> list[dict]:
    """字段搜索 —— 前端「可用字段」用。3777 个字段不可能列全,只能搜。

    返回 [{name, label}]:
      · name  字段名(英文)。**脚本里要写的就是它**,点击插入的也是它。
      · label 中文名,拿不准时为 None —— 界面上就显示英文原名。
              半吊子翻译比不翻更误导,详见 screen_dsl.field_label_cn。

    中文名也参与搜索:用户搜「成交量」应该能找到 volume。
    """
    meta = get_meta(market_key)
    q = (q or "").strip().lower()
    base = sorted(n for n in meta.names
                  if isinstance(n, str) and "|" not in n and "[" not in n)
    labels = {n: screen_dsl.field_label_cn(n) for n in base}

    def pack(names):
        return _collapse([{"name": n, "label": labels.get(n)} for n in names], limit)

    if not q:
        return pack(base)
    exact = [n for n in base if n.lower() == q]
    prefix = [n for n in base if n.lower().startswith(q) and n.lower() != q]
    sub = [n for n in base if q in n.lower() and not n.lower().startswith(q)]
    hit = exact + prefix + sub
    # 中文命中排在英文子串命中之后 —— 搜英文时不希望被中文结果挤掉
    seen = set(hit)
    cn = [n for n in base if n not in seen and (labels.get(n) or "").lower().find(q) >= 0]
    return pack(hit + cn)


# 同族折叠 —— 只有**数字**不同的字段算一族(EMA10/EMA12/…/EMA300 共 31 个)。
#
# 不折叠的话搜一个 "e" 就被 31 个 EMA 刷满整屏,别的字段一个都看不见。
#
# **判据只看数字**,这一点是刻意的:`return_on_equity_fq / _fy / _ttm` 差的是
# 报告期字母,它们是三个**真正不同**的字段(最近季 / 最近年 / 滚动12个月),
# 折叠掉就没法选了。而 EMA10 与 EMA20 只是同一个指标的参数不同,
# 收起来让用户点开再挑周期,信息一点没少。
_FAMILY_MIN = 3


def _collapse(items: list[dict], limit: int) -> list[dict]:
    import re as _re
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for it in items:
        key = _re.sub(r"\d+", "#", it["name"])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(it)

    out: list[dict] = []
    for key in order:
        g = groups[key]
        if len(g) < _FAMILY_MIN:
            out.extend(g)
            continue
        # 族标签:拿成员标签把数字换成 N(10日EMA → N日EMA)。
        # 成员没有中文名时退回族键(EMA# → EMA#),照旧是英文。
        first = g[0]
        fam_label = None
        if first.get("label"):
            fam_label = _re.sub(r"\d+", "N", first["label"])
        out.append({
            "name": first["name"],          # 代表项 · 前端不会直接插它
            "label": fam_label,
            "family": key.replace("#", "N"),
            "count": len(g),
            "members": g,
        })
    return out[:limit]
