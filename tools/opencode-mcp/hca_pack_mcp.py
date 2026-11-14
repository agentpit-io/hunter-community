#!/usr/bin/env python3
"""hcapack · 组合工具 MCP —— 一次调用拿齐一道投研题要的整包数据。

## 为什么要有这一层（I2）

M2 的 A/B 实测摆出一个事实：同一道题，HCA 要 7～22 次工具调用，社区版只要 2～3 次。
差距不在引擎慢，在**取一份数据要来回好几趟**：

    akshare_search("财务指标") → akshare_signature(...) → akshare_call(...)

三次调用才拿到一张表，而每一次调用都意味着**再跑一轮模型**（整段上下文重发一遍）。
q2 那道题因此跑出 22 次调用 / 106 秒 / 67 万 token。

这个 server 把「一道题要的东西」打成一个包：

  · `stock_snapshot`  个股基本面快照 —— 价格+数据时点+营收/归母净利同比+毛利率+ROE
  · `stocks_intel`    多只股票近 N 日情报汇总 —— 公告 + 新闻 + 一手信号，按票分组
  · `thesis_evidence` 持仓论点取证包 —— 论点原文 + 持仓 + 行情 + 财务 + 分红 + 公告 + 新闻

## 数据都是真取的，取不到就说取不到

每一块都带 `source`（哪个接口取的）和 `as_of`（数据时点）。子调用失败时那一块写
`{"error": ...}` 并保留其余块 —— **绝不用别的数据顶上，也绝不留空让模型去猜**。
这是总控红线 1 在组合工具上的落法：包是合起来的，出处是分开的。

## 数据来源

  · 行情 / 新闻：本发行版后端 `${HERMES_API_URL}/api/internal/*`，与 `watchlist` MCP
    同一个接口、同一份数据（两边评测比的是步数，不是数据源）。
  · 财务：AKShare `stock_financial_abstract`（新浪源）。一次就能拿到营业总收入、
    归母净利润、各自的增长率、毛利率、净资产收益率(ROE)，按报告期排列。
    不用东方财富系 `*_em` 接口 —— 本机房实测拉不通（返回空体）。
  · 分红：AKShare `stock_dividend_cninfo`（巨潮源），取不到就明说。
  · 公告：AKShare `stock_individual_notice_report`（东方财富-个股公告），按票并行取。
"""
from __future__ import annotations

import concurrent.futures
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import warnings

warnings.filterwarnings("ignore")

from mcp.server.mcpserver import MCPServer          # noqa: E402

try:
    from hca_news_date import _fill_news_dates      # noqa: E402
except ImportError:                                  # pragma: no cover
    print("[hca] ⚠️ 没找到 hca_news_date，stocks_intel 的新闻日期补洞**未生效**",
          file=sys.stderr, flush=True)

    def _fill_news_dates(text):                     # type: ignore[misc]
        return text

try:
    from hca_size_guard import fit as _fit          # noqa: E402
except ImportError:                                  # pragma: no cover
    print("[hca] ⚠️ 没找到 hca_size_guard，hcapack 的大小闸**未生效**", file=sys.stderr, flush=True)

    def _fit(text, tool="", max_bytes=None):        # type: ignore[misc]
        return text

mcp = MCPServer("hcapack")

HERMES_API = (os.getenv("HERMES_API_URL") or "http://api:8000").rstrip("/")


def _resolve_internal_key(default: str = "") -> str:
    """读取内部鉴权密钥：优先环境变量 HUNTER_INTERNAL_KEY，为空时回退读取共享密钥卷。"""
    val = (os.getenv("HUNTER_INTERNAL_KEY") or "").strip()
    if val:
        return val
    key_file = (os.getenv("HUNTER_INTERNAL_KEY_FILE") or "/opt/hunter-secrets/internal_key").strip()
    try:
        if os.path.isfile(key_file):
            with open(key_file, encoding="utf-8") as f:
                v = f.read().strip().strip("'\"")
                if v:
                    return v
    except OSError:
        pass
    sec_file = (os.getenv("HUNTER_SECRETS_FILE") or "/opt/hunter-secrets/secrets.env").strip()
    try:
        if os.path.isfile(sec_file):
            with open(sec_file, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.startswith("HUNTER_INTERNAL_KEY="):
                        v = line.split("=", 1)[1].strip().strip("'\"")
                        if v:
                            return v
    except OSError:
        pass
    return default


INTERNAL_KEY = _resolve_internal_key("")
USER_ID = os.getenv("HUNTER_USER_ID") or ""
WORKSPACE = os.path.normpath(os.getenv("HCA_WORKSPACE") or "/workspace")
HTTP_TIMEOUT = float(os.getenv("HCA_PACK_TIMEOUT", "45"))
# 财务表只回最近这么多个报告期 —— 再多模型也用不上，白占预算
PERIODS = int(os.getenv("HCA_PACK_PERIODS", "5"))


# ── 后端调用 ────────────────────────────────────────────────────────────────
def _api(tool: str, body: dict, uid: str = "") -> dict:
    """打后端的内部工具接口。失败返回 {"error": ...}，**不抛异常**。

    `uid` 是 guard hook 注入进来的**这个会话真正的用户**（P0-5）。拿不到才退回
    容器级 `HUNTER_USER_ID` —— 网页多用户形态下退回去就是串户，所以顺序不能反。
    """
    who = (uid or "").strip() or USER_ID
    # 身份只走 header —— 后端对请求体里的未知字段会 422（watchlist_mcp 也是这么做的：
    # 它把 `_` 开头的字段从 body 里剔掉再发）。这里干脆一开始就不往 body 里放。
    data = json.dumps({k: v for k, v in body.items() if not k.startswith("_")}).encode()
    headers = {"Content-Type": "application/json"}
    key = INTERNAL_KEY or _resolve_internal_key("")
    if key:
        headers["X-Hunter-Internal-Key"] = key
    if who:
        headers["X-Hunter-User-Id"] = who
    req = urllib.request.Request(f"{HERMES_API}/api/internal/watchlist/{tool}",
                                 data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            raw = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return {"error": f"后端 {tool} 返回 HTTP {e.code}",
                "body": e.read().decode("utf-8", "replace")[:200]}
    except Exception as e:                                       # noqa: BLE001
        return {"error": f"后端 {tool} 调用失败：{type(e).__name__}: {str(e)[:200]}"}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": f"后端 {tool} 返回的不是 JSON", "head": raw[:200]}


# ── 财务 ────────────────────────────────────────────────────────────────────
# `stock_financial_abstract` 的「指标」名 → 我们对外的字段名。
# 只取这几行，其余 70 多行不进上下文。
_FIN_ROWS = {
    "营业总收入": "营业总收入_元",
    "营业总收入增长率": "营业总收入同比_%",
    "归母净利润": "归母净利润_元",
    "归属母公司净利润增长率": "归母净利润同比_%",
    "毛利率": "毛利率_%",
    "净资产收益率(ROE)": "净资产收益率ROE_%",
    "扣非净利润": "扣非净利润_元",
    "资产负债率": "资产负债率_%",
}


def _financials(code: str) -> dict:
    """最近几个报告期的关键财务指标。一次 AKShare 调用。"""
    try:
        import akshare as ak                                     # noqa: PLC0415
        df = ak.stock_financial_abstract(symbol=code)
    except Exception as e:                                       # noqa: BLE001
        return {"error": f"AKShare stock_financial_abstract 失败：{type(e).__name__}: {str(e)[:200]}"}
    try:
        periods = [c for c in df.columns if c not in ("选项", "指标")][:PERIODS]
        out: dict = {"source": "AKShare stock_financial_abstract（新浪财经）",
                     "报告期": periods, "指标": {}}
        seen = set()
        for _, row in df.iterrows():
            name = str(row.get("指标") or "")
            field = _FIN_ROWS.get(name)
            if not field or field in seen:
                continue                      # 同名指标在多个「选项」下重复出现，取第一份
            seen.add(field)
            vals = {}
            for p in periods:
                v = row.get(p)
                # NaN / None 一律写 null —— 不补 0，也不拿上一期顶替
                vals[p] = None if v is None or v != v else (
                    round(float(v), 4) if isinstance(v, (int, float)) else str(v))
            out["指标"][field] = vals
        missing = [f for f in _FIN_ROWS.values() if f not in out["指标"]]
        if missing:
            out["未取到的指标"] = missing
        return out
    except Exception as e:                                       # noqa: BLE001
        return {"error": f"解析财务摘要失败：{type(e).__name__}: {str(e)[:200]}"}


def _dividend(code: str) -> dict:
    try:
        import akshare as ak                                     # noqa: PLC0415
        df = ak.stock_dividend_cninfo(symbol=code)
    except Exception as e:                                       # noqa: BLE001
        return {"error": f"AKShare stock_dividend_cninfo 失败：{type(e).__name__}: {str(e)[:160]}"}
    try:
        cols = [c for c in ("实施方案公告日期", "报告时间", "分红年度", "派息比例",
                            "每股派息", "股权登记日", "派息股息率", "方案文字") if c in df.columns]
        recs = df[cols].tail(6).to_dict("records") if cols else df.tail(4).to_dict("records")
        return {"source": "AKShare stock_dividend_cninfo（巨潮资讯）",
                "最近几次分红": json.loads(json.dumps(recs, ensure_ascii=False, default=str))}
    except Exception as e:                                       # noqa: BLE001
        return {"error": f"解析分红数据失败：{type(e).__name__}: {str(e)[:160]}"}


def _read_workspace(rel: str) -> dict:
    path = os.path.normpath(os.path.join(WORKSPACE, rel))
    if not path.startswith(WORKSPACE + os.sep):
        return {"error": f"{rel} 在工作区外，拒绝读取"}
    if not os.path.isfile(path):
        return {"error": f"工作区里没有 {rel}"}
    try:
        return {"path": rel, "text": open(path, encoding="utf-8").read()}
    except Exception as e:                                       # noqa: BLE001
        return {"error": f"读 {rel} 失败：{type(e).__name__}"}


def _news(code: str, limit: int, uid: str = "") -> dict:
    """单只票的新闻。**必须走 `_fill_news_dates`** —— 上游把 date 返回成空串
    （待办池 P1-17），而题面要求每条都带日期。补不出来就留空，不编。"""
    d = _api("stock_news", {"code": code, "limit": limit}, uid)
    if "error" in d:
        return d
    try:
        return json.loads(_fill_news_dates(json.dumps(d, ensure_ascii=False)))
    except Exception:                                            # noqa: BLE001
        return d


# ── 公告 ────────────────────────────────────────────────────────────────────
# 为什么 `stocks_intel` 必须带公告：I2 的 opt-b 第 1 轮实测，q5（"公告、新闻、行业数据
# 或异动信号都算"）里模型先调了一次 `stocks_intel`，发现**包里只有新闻没有公告**，
# 于是自己去走 akshare 三连补公告 —— 一道题因此跑成 12 次调用 / 89.7 秒，
# 其中一次 `stock_notice_report`（全市场当日公告）就等了 39.5 秒。
# 这不是模型不听话，是包缺了题面点名要的那一块。补上之后那几步才有理由不发生。
_NOTICE_COLS = ("代码", "公告标题", "公告类型", "公告日期")


def _notices(code: str, days: int) -> dict:
    """单只票近 N 天的公告。一次 AKShare 调用（`stock_individual_notice_report`，
    东方财富-个股公告）。

    **空列表就是「这只票这段时间确实没有公告」**，不是取数失败 —— 题面要求
    "某只票没有就直接说没有，不要凑"，所以这两种情形必须在返回里分得开：
    取不到写 `{"error": ...}`，没有写 `"公告": []`。

    df 里没有 `代码` 这一列的情形是真实存在的（同一次实测里 600519 就是这样，
    akshare MCP 那边因此抛了 `KeyError: '代码'`）—— 上游在没有记录时回的是一张
    空表 / 列不全的表。所以列一律按「有就取」处理，不按名字硬索引。
    """
    tz = datetime.timezone(datetime.timedelta(hours=8))
    today = datetime.datetime.now(tz).date()
    begin = (today - datetime.timedelta(days=max(1, days))).strftime("%Y%m%d")
    base = {"source": "AKShare stock_individual_notice_report（东方财富-个股公告）",
            "查询区间": f"{begin} ~ {today.strftime('%Y%m%d')}"}
    try:
        import akshare as ak                                     # noqa: PLC0415
        df = ak.stock_individual_notice_report(
            security=code, symbol="全部", begin_date=begin, end_date=today.strftime("%Y%m%d"))
    except KeyError as e:
        # **`KeyError: '代码'` 就是「这段时间一条公告都没有」**，不是取数失败。
        # 读过 akshare 的实现与上游接口才敢这么判（两条证据）：
        #   · `_stock_notice_report` 先算 `total_page = ceil(total_hits / 100)`，
        #     为 0 时那个 for 一次都不进，`big_df` 保持空表，接着去 rename/取列 → KeyError；
        #   · 直接打上游 `np-anotice-stock.eastmoney.com/api/security/ann`：
        #     600519 在 20260916~20260923 区间 `total_hits = 0`（601088 是 3）。
        # 这一条必须分清楚：q5 / q2 的题面都要求「没有就直接说没有，不要凑」，
        # 把它报成 error 会让模型以为取数失败、再换个工具去找一遍
        # —— 那正是 §2.8.1 要消掉的那几步。
        if str(e).strip("'\"") in _NOTICE_COLS:
            return {**base, "公告": [],
                    "说明": "上游返回 0 条公告（AKShare 在空结果上会抛 "
                            f"KeyError: {e} —— 这是它的实现问题，不是取数失败）"}
        return {**base, "error": f"AKShare stock_individual_notice_report 失败："
                                 f"KeyError: {str(e)[:200]}"}
    except Exception as e:                                       # noqa: BLE001
        return {**base, "error": f"AKShare stock_individual_notice_report 失败："
                                 f"{type(e).__name__}: {str(e)[:200]}"}
    try:
        if df is None or len(df) == 0:
            return {**base, "公告": []}
        cols = [c for c in _NOTICE_COLS if c in df.columns] or list(df.columns)[:4]
        recs = json.loads(json.dumps(df[cols].head(20).to_dict("records"),
                                     ensure_ascii=False, default=str))
        return {**base, "公告": recs}
    except Exception as e:                                       # noqa: BLE001
        return {**base, "error": f"解析公告失败：{type(e).__name__}: {str(e)[:200]}"}


# ── 一手信号（TrueSource SaaS，直连，不经本发行版的 api）────────────────────
TRUESOURCE_URL = (os.getenv("TRUESOURCE_URL")
                  or "https://hunter.agentpit.io/api/saas/truesource").rstrip("/")
HUNTER_API_KEY = (os.getenv("HUNTER_API_KEY") or "").strip()


def _truesource_brief(syms: str) -> dict:
    """一手信号日报。与 `truesource` MCP 打的是同一个接口、同一把 key。"""
    if not HUNTER_API_KEY:
        return {"error": "没有配置 HUNTER_API_KEY，拿不到一手信号",
                "how_to_fix": "在 deploy/.env 里填 HUNTER_API_KEY（申请："
                              "https://hunter.agentpit.io/dev/api-keys）"}
    url = f"{TRUESOURCE_URL}/api/hunter/daily-brief?symbols={urllib.parse.quote(syms)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {HUNTER_API_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"TrueSource 返回 HTTP {e.code}",
                "body": e.read().decode("utf-8", "replace")[:200]}
    except Exception as e:                                       # noqa: BLE001
        return {"error": f"TrueSource 调用失败：{type(e).__name__}: {str(e)[:200]}"}


def _now_sh() -> str:
    """本次取数时刻（上海时间）。容器里 TZ 通常没设，UTC + 8 就是上海（中国不用夏令时）。"""
    tz = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")


# 行情那一块的时点说明。**必须写清楚这是「我们打接口的时刻」而不是行情时间戳** ——
# 上游 stock_quickview 的返回里没有任何时间字段（实测），含糊带过就等于让模型
# 把取数时刻当成行情时刻写进报告。
_QUOTE_ASOF_NOTE = ("本次取数时刻（上海时间）。⚠️ 上游 stock_quickview 的返回里"
                    "**没有行情时间戳**，所以这不是交易所的行情时刻；"
                    "引用时请说明「截至本次取数」而不是「截至某时某分的盘口」。")


# 包里**没有**的东西，要在包里自己说出来。
#
# 为什么加这一条：`opt-fork-b` 的 q2 第 1 轮里，模型为了算分红率用了
# **总股本 198.7 亿股** —— 这个数不在任何一次工具返回里，是它从记忆里拿的
# （`audit_numbers.py` 报 ✗，人工复核确认）。数值恰好是对的，但 A1 的口径是
# 「每个数字都来自本次工具返回」，凭记忆填就是违规，而且下一次未必还对。
#
# 根因是数据缺口：上游 `stock_quickview` 的 `valuation` 段**实测是空的 `{}`**，
# 而这台机器拉不通东方财富系 `*_em` 接口（`stock_individual_info_em` 直接
# JSONDecodeError），巨潮的 `stock_profile_cninfo` 里也没有总股本。
# 换句话说**本部署现在确实拿不到总股本**（待办池 P2-13 的同一族）。
#
# 拿不到就要说拿不到 —— 这是总控红线 1。所以包里明写一行，模型看见了才不会
# 「顺手补一个」。真正修好要等 api 侧把 valuation 填上。
_NOT_IN_PACK = ("本包**没有**总股本 / 总市值 / PE / PB / 股息率："
                "上游 stock_quickview 的 valuation 段实测为空，且本机房拉不通东方财富 "
                "*_em 接口。**需要这些数时请直接说取不到**，不要用记忆里的数补 —— "
                "也就不要去算需要总股本的派生指标（如按股本折算的分红总额与分红率）。")


def _parallel(jobs: dict) -> dict:
    """并行跑几个取数子调用。

    组合工具的意义是「一次调用拿齐」，但一次调用里面**仍然是几个独立的取数**：
    行情打后端、财务打 AKShare，互不依赖。串着跑实测 11.3 秒，并行就是最慢那个。
    每个 job 自己 catch 过异常返回 {"error": ...}，这里再兜一层。
    """
    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(jobs) or 1)) as ex:
        futs = {ex.submit(fn): k for k, fn in jobs.items()}
        for fut in concurrent.futures.as_completed(futs):
            k = futs[fut]
            try:
                out[k] = fut.result()
            except Exception as e:                               # noqa: BLE001
                out[k] = {"error": f"{k} 取数失败：{type(e).__name__}: {str(e)[:200]}"}
    return {k: out[k] for k in jobs}          # 保持声明顺序，返回才读得顺


def _codes(codes: str) -> list:
    return [c.strip() for c in str(codes or "").replace("，", ",").split(",") if c.strip()][:10]


# ── 工具 ────────────────────────────────────────────────────────────────────
@mcp.tool()
def stock_snapshot(code: str, hermes_user_id: str = "") -> str:
    """个股基本面快照 · 一次拿齐：最新价与数据时点、营业总收入与归母净利润的同比、
    毛利率、ROE、资产负债率（最近 5 个报告期）。问「基本面怎么样 / 财务指标」用这个，
    不要再走 akshare 的 search→signature→call 三连。取不到的字段写 null 并说明。
    返回里带「取数时刻」与各块的 source，引用数字时按它们标注口径。"""
    out = {"code": code, "取数时刻": _now_sh(), "取数时刻说明": _QUOTE_ASOF_NOTE,
           "本包未提供": _NOT_IN_PACK}
    out.update(_parallel({"行情": lambda: _api("stock_quickview", {"code": code}, hermes_user_id),
                          "财务": lambda: _financials(code)}))
    return _fit(json.dumps(out, ensure_ascii=False), tool="hcapack")


@mcp.tool()
def stocks_intel(codes: str, limit: int = 5, days: int = 7, hermes_user_id: str = "") -> str:
    """多只股票的情报汇总 · 一次拿齐：每只票的**近期公告**（东方财富，带标题/类型/日期）
    + 近期新闻（带来源与日期）+ 一手信号简报。
    codes 用逗号分隔（如 "600519,601088,300750"，最多 10 只）；`days` 是公告回溯天数。
    问「最近有什么消息 / 公告 / 动态 / 情报」用这个，**一次就够** ——
    不要每只票单独调一次，也不要再去 akshare 补公告。
    某只票没有内容就返回空列表 —— **空列表就是「确实没有」，不要替它补**；
    真的取不到时那一块是 `{"error": ...}`，两种情形在返回里是分开的。"""
    cs = _codes(codes)
    if not cs:
        return json.dumps({"error": "codes 不能为空"}, ensure_ascii=False)
    jobs = {f"news:{c}": (lambda c=c: _news(c, limit, hermes_user_id)) for c in cs}
    jobs.update({f"notice:{c}": (lambda c=c: _notices(c, days)) for c in cs})
    jobs["brief"] = lambda: _truesource_brief(",".join(cs))
    got = _parallel(jobs)
    out = {"codes": cs, "取数时刻": _now_sh(), "公告回溯天数": days,
           "按票分组的公告": {c: got[f"notice:{c}"] for c in cs},
           "按票分组的新闻": {c: got[f"news:{c}"] for c in cs},
           "一手信号简报": got["brief"]}
    return _fit(json.dumps(out, ensure_ascii=False), tool="hcapack")


@mcp.tool()
def thesis_evidence(code: str, limit: int = 5, days: int = 7,
                    hermes_user_id: str = "") -> str:
    """持仓论点取证包 · 一次拿齐：我写的论点原文（theses/<code>.md）、持仓账本
    （holdings/positions.md）、最新行情、最近 5 期关键财务指标、最近几次分红、
    **近期公告与新闻**（证伪条件常常要靠它们才判得了）。
    问「复核我的论点 / 证伪条件触发了吗」用这个，**一次就够** ——
    不要再逐个 read_file，也不要为了查消息再调一次 `stocks_intel`。"""
    out = {"code": code, "取数时刻": _now_sh(), "取数时刻说明": _QUOTE_ASOF_NOTE,
           "本包未提供": _NOT_IN_PACK,
           "论点原文": _read_workspace(f"theses/{code}.md"),
           "持仓账本": _read_workspace("holdings/positions.md")}
    # 公告与新闻也进这个包：I2 的 opt-b 实测，q2 三次运行**每一次**都是
    # thesis_evidence → stocks_intel 两连（论点里的证伪条件是「长协价跌破 X」
    # 这类要看最新消息才判得了的事），于是每次都多花一整轮模型。
    # 包里带上之后那一步才有理由不发生 —— 和 §2.8.1 q5 缺公告是同一类错。
    out.update(_parallel({"行情": lambda: _api("stock_quickview", {"code": code}, hermes_user_id),
                          "财务": lambda: _financials(code),
                          "分红": lambda: _dividend(code),
                          "公告": lambda: _notices(code, days),
                          "新闻": lambda: _news(code, limit, hermes_user_id)}))
    return _fit(json.dumps(out, ensure_ascii=False), tool="hcapack")


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
