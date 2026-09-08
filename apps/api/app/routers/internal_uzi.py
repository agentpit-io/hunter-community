"""hunter-UZI-Skill 集成 · Phase 1 MVP · chat 深度分析 tool 桥接端点

用户在 hunter.agentpit.io/chat 里说"深度分析 601899"或"帮我深度看看紫金矿业"时：
  opencode LLM → uzi_mcp.stock_deep_analysis → POST 本端点 → finance-data 7 数据 → Gemini 合成 markdown

Phase 1（本文件）不调 SG UZI worker · 直接在 hermes-api 里拉数据 + LLM 合成 · 秒级返回。
Phase 2（Sprint 3 P2 后续）会加 /uzi/full_analysis 走 SG 完整 22 dim pipeline · 后台任务 + poll。

对应文档：
- hermes-1/doc/codex/自定义MCP/UZI/Sprint-1-2-完成报告.md §四 P2
- hermes-1/doc/codex/自定义MCP/UZI/04-UZI-Skill集成到hunter-chat方案.md
"""
from __future__ import annotations
import asyncio
import os
import re
import time
from datetime import datetime
from typing import Any, Awaitable

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from app.services import finance_data_client as fd
from app.services.online_analysis.llm_client import get_client

router = APIRouter(prefix="/internal", tags=["mcp-bridge"])

_INTERNAL_KEY = os.getenv("HUNTER_INTERNAL_KEY", "")
# AGENT_SUB_UZI_MODEL 是内部部署里指定 Gemini 变体用的,开源版跟随 .env 的 LLM_DEFAULT_MODEL,
# 否则用户配了 DeepSeek 却在这里请求 gemini-3.5-flash 会直接 502 UnknownModel。
_MODEL = os.getenv("AGENT_SUB_UZI_MODEL") or os.getenv("LLM_DEFAULT_MODEL", "gemini-3.5-flash")

# ── 三段时间预算 ───────────────────────────────────────────────
# 2026-09-07 事故(茅台 600519):同一用户两次深度分析,第一次主拉数 47s 正常出报告;
# 第二次主拉数卡了 **137 秒**(8 路里某一路 akshare / 用户源没有超时,上游抖一下就挂住),
# 整个端点超过 uzi_mcp 的 120s → httpx ReadTimeout → 工具给模型的是 {"error": ...} →
# 前端富卡片拿不到 markdown 显示"已跑 2062 秒仍未出内容",而 chat 模型转头凭记忆
# 自己写了一篇"分析",里面的"营收增速放缓至 1.30%"是编的。
#
# 根因不是哪一路慢,是**没有总预算**:finance-data 每路各自 10s,但 akshare、用户源
# 这些路一个超时都没有,gather 会陪最慢那路等到天荒地老。
# 所以不追各路的超时,直接给每个阶段一个硬预算:到点收工,拿到多少算多少,
# 没拿到的进 dims_missing(空的比假的好,也比永远等下去好)。
# 三段加起来 40+30+60=130s,压在 uzi_mcp 的 170s 与 opencode MCP 的 180s 之下。
_FETCH_BUDGET_S    = float(os.getenv("UZI_FETCH_BUDGET_S", "40"))
_FALLBACK_BUDGET_S = float(os.getenv("UZI_FALLBACK_BUDGET_S", "30"))
_LLM_TIMEOUT_S     = float(os.getenv("UZI_LLM_TIMEOUT_S", "60"))


async def _timed(coro: Awaitable[Any]) -> tuple[Any, float]:
    t = time.perf_counter()
    return await coro, time.perf_counter() - t


async def _gather_budget(named: dict[str, Awaitable[Any]], budget_s: float, tag: str) -> dict[str, Any]:
    """并发跑一批取数协程,总时限 budget_s。返回 {name: result | None}。

    超预算的路记 None 并打 warning(带每路耗时),下次出事一眼看出是哪路慢 ——
    这次事故之所以只能推断"某一路卡了 137s",就是因为原来没有分路计时。
    `asyncio.to_thread` 起的线程取消不了,放弃后它会在后台自己跑完然后被丢掉,可接受。
    """
    t0 = time.perf_counter()
    tasks = {name: asyncio.ensure_future(_timed(coro)) for name, coro in named.items()}
    _done, pending = await asyncio.wait(tasks.values(), timeout=budget_s)
    out: dict[str, Any] = {}
    cost: dict[str, str] = {}
    slow: list[str] = []
    for name, task in tasks.items():
        if task in pending:
            task.cancel()
            out[name] = None
            slow.append(name)
            cost[name] = f">{budget_s:.0f}s"
            continue
        exc = task.exception()
        if exc is not None:
            logger.warning("[uzi] {} {} 失败: {}: {}", tag, name, type(exc).__name__, exc)
            out[name] = None
            cost[name] = "err"
            continue
        result, dt = task.result()
        out[name] = result
        cost[name] = f"{dt:.1f}s"
    elapsed = time.perf_counter() - t0
    if slow:
        logger.warning("[uzi] {} 超过预算 {:.0f}s · 放弃 {} · 各路耗时 {}", tag, budget_s, slow, cost)
    else:
        logger.info("[uzi] {} 完成 {:.1f}s · 各路耗时 {}", tag, elapsed, cost)
    return out


# ── akshare A 股兜底 ─────────────────────────────────────────────
# 开源版 finance-data 只 seed 了 quote 一维,其余 7 项(kline/财务/龙虎榜/十大股东/
# 治理/新闻/研报)都空,LLM 拿到 1/8 数据合成的报告没法看。
# 生产 SaaS 用的是订阅制 finance-data,拉哪个股哪个股全维 —— 社区版没这条件,
# 补 akshare 兜底,任意 A 股都能出 kline/financials/news/lhb/research 5 项,
# 覆盖率从 12% 提到 ~75%,报告质量陡升 · 无需付费数据源。


def _ak_market_prefix(bare: str) -> str:
    """A 股代码 → 交易所前缀,给 akshare 腾讯通道用。"""
    b = bare.lstrip("0")[:1] if bare.startswith("00") else bare[:1]
    if bare.startswith(("60", "68", "69")): return "sh"
    if bare.startswith(("00", "30", "20")): return "sz"
    if bare.startswith(("8", "43", "83", "87", "88")): return "bj"
    return "sh"  # 兜底


def _akshare_kline(bare: str, days: int = 30) -> list[dict]:
    """akshare 日线 K 线 · 返 list 兼容 _fmt_kline_summary 期望的 {ts,open,high,low,close,volume}。

    双通道:优先腾讯(响应快 · 少限流),失败回退东财(数据更全但连接常抖)。
    生产 finance-data 稳定时不会走这里 —— 只在开源版无订阅时兜底。
    """
    # 走共享 agents/data_sources/akshare_kline.fetch_kline · 双通道逻辑一处维护
    # market_analyst 也用它 · 以后加网易/新浪也统一在 shared 模块加。
    from agents.data_sources.akshare_kline import fetch_kline
    return fetch_kline(bare, days=days)


def _akshare_financials(bare: str) -> dict | None:
    """akshare 财务摘要 · 返 dict 兼容 _fmt_financials 期望的字段名。

    2026-08-20 · 收敛到共享层 · 双通道(同花顺 → 东财)统一维护
    · 与 watchlist_rank_agent 走同一份实现 · 消除 §9 铁律 3 违反。
    """
    from agents.data_sources.akshare_financials import latest as _latest_financials
    return _latest_financials(bare)


def _akshare_news(bare: str, limit: int = 8) -> list[dict]:
    """akshare 新闻 · 返 list 兼容 _fmt_news 期望的 {title, publish_date}。"""
    try:
        import akshare as ak
        df = ak.stock_news_em(symbol=bare)
    except Exception as e:
        logger.warning("[uzi] akshare news fallback failed code={} err={}", bare, e)
        return []
    if df is None or df.empty:
        return []
    rows = df.head(limit).to_dict(orient="records")
    return [{
        "title":        r.get("新闻标题") or "",
        "publish_date": str(r.get("发布时间") or "")[:10],
        "source":       r.get("文章来源") or "",
        "url":          r.get("新闻链接") or "",
    } for r in rows]


def _akshare_research(bare: str, limit: int = 10) -> list[dict]:
    """akshare 券商研报 · 东财 stock_research_report_em · 返 list。"""
    try:
        import akshare as ak
        df = ak.stock_research_report_em(symbol=bare)
    except Exception as e:
        logger.warning("[uzi] akshare research fallback failed code={} err={}", bare, e)
        return []
    if df is None or df.empty:
        return []
    rows = df.head(limit).to_dict(orient="records")
    out = []
    for r in rows:
        out.append({
            "title":        r.get("报告名称") or r.get("研报标题") or "",
            "org":          r.get("机构") or r.get("研究机构") or "",
            "rating":       r.get("最新评级") or r.get("评级") or "",
            "publish_date": str(r.get("日期") or r.get("发布日期") or "")[:10],
        })
    return out


def _akshare_lhb(bare: str, days: int = 30) -> list[dict]:
    """akshare 龙虎榜 · 东财 stock_lhb_detail_em · 只挑近 days 天含本股的记录。"""
    try:
        import akshare as ak
        from datetime import datetime, timedelta
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        df = ak.stock_lhb_detail_em(start_date=start, end_date=end)
    except Exception as e:
        logger.warning("[uzi] akshare lhb fallback failed code={} err={}", bare, e)
        return []
    if df is None or df.empty:
        return []
    # 只留本股
    code_col = "代码" if "代码" in df.columns else ("股票代码" if "股票代码" in df.columns else None)
    if code_col:
        df = df[df[code_col].astype(str).str.contains(bare, na=False)]
    if df.empty:
        return []
    rows = df.head(20).to_dict(orient="records")
    return [{
        "trade_date": str(r.get("上榜日") or r.get("交易日期") or "")[:10],
        "reason":     r.get("上榜原因") or "",
        "net_buy":    r.get("龙虎榜净买额") or r.get("净买额") or 0,
    } for r in rows]


# ═══════════════════════════════════════════════════════════════
# 港美股专属取数 —— A 股那套通道对它们基本全是空
# ═══════════════════════════════════════════════════════════════
# 2026-09-08:GOOG 的「66 位大佬评审团」前三节全是「暂无数据」。查下来
# **不是数据源没有,是这个 endpoint 从来只走 A 股八路**:
#   · lhb / fund_holders / governance / research 是 A 股专属,给美股白拉 0.4s,
#     还会在提示词里渲染成"缺失",模型照实写成"暂无数据"
#   · 美股 SEC 公告、东财美股财报、港股披露易公告 —— 仓里都有现成的源,一路都没接
#
# 实测(community 服务器 · 国内 IP · 2026-09-08):
#   gm.filings.us_filings(GOOG)     178ms  10 条 SEC 公告
#   gm.filings.hk_filings(00700)    5.3s   10 条披露易公告
#   东财美股财报(GOOG)               699ms  2025 年报 营收 4028 亿美元
#   东财港股财报(00700)              347ms  2025 年报 营业额 7437 亿港元
# 数据一直都在,只是没人去取。
#
# 反过来,以下源实测**不合格,故意不接**(空的比假的好):
#   · gm.news_src.hk_news(00700) → 8 条 Yahoo 英文新闻,内容与腾讯毫无关系
#   · findata_db.* → 开源部署的库里没有 us_*/hk_* 表,全部报 relation does not exist

# 东财财报的科目名两边不一样(美股 ITEM_NAME / 港股 STD_ITEM_NAME),
# 而且同一个概念有多种叫法,用别名表匹配。
_FIN_ITEM_ALIASES = {
    "revenue":      ("营业收入", "主营收入", "营业额", "营运收入", "总收入"),
    "gross_profit": ("毛利", "毛利润"),
    "net_profit":   ("归属于母公司股东净利润", "归属于普通股股东净利润", "净利润",
                     "股东应占溢利", "本公司拥有人应占溢利", "持续经营净利润"),
    "eps":          ("基本每股收益-普通股", "基本每股收益", "每股基本盈利"),
}


def _pick_fin_item(name_to_amount: dict, key: str):
    for alias in _FIN_ITEM_ALIASES[key]:
        v = name_to_amount.get(alias)
        if v is not None:
            return v
    return None


def _em_financials(bare: str, market: str) -> dict | None:
    """港美股财务摘要 · 东财年报(akshare)。取最近两期算同比。

    取不到就返 None —— 不许拿空 dict 或 0 冒充(§铁律:空的比假的好)。
    """
    try:
        import akshare as ak
        if market == "US":
            df = ak.stock_financial_us_report_em(
                stock=bare.upper(), symbol="综合损益表", indicator="年报")
        else:
            df = ak.stock_financial_hk_report_em(
                stock=bare.zfill(5), symbol="利润表", indicator="年度")
    except Exception as e:
        logger.warning("[uzi] 东财财报失败 code={} market={} err={}", bare, market, e)
        return None
    if df is None or getattr(df, "empty", True):
        return None
    name_col = "ITEM_NAME" if "ITEM_NAME" in df.columns else "STD_ITEM_NAME"
    if name_col not in df.columns or "REPORT_DATE" not in df.columns:
        logger.warning("[uzi] 东财财报字段异常 code={} cols={}", bare, list(df.columns))
        return None

    periods = sorted({str(x) for x in df["REPORT_DATE"].dropna().unique()})
    if not periods:
        return None

    def _snapshot(period: str) -> dict:
        sub = df[df["REPORT_DATE"].astype(str) == period]
        m = {}
        for _, row in sub.iterrows():
            nm = str(row.get(name_col) or "").strip()
            if nm and nm not in m:
                try:
                    m[nm] = float(row.get("AMOUNT"))
                except (TypeError, ValueError):
                    continue
        return m

    cur = _snapshot(periods[-1])
    prev = _snapshot(periods[-2]) if len(periods) > 1 else {}

    out = {"period": periods[-1][:10], "market": market}
    for key in ("revenue", "gross_profit", "net_profit", "eps"):
        out[key] = _pick_fin_item(cur, key)

    for key, yoy_key in (("revenue", "revenue_yoy"), ("net_profit", "net_profit_yoy")):
        now, was = out.get(key), _pick_fin_item(prev, key)
        if now is not None and was:
            out[yoy_key] = round((now - was) / abs(was) * 100, 2)

    if out.get("revenue") and out.get("gross_profit"):
        out["gross_margin"] = round(out["gross_profit"] / out["revenue"] * 100, 2)

    # 一个关键科目都没匹配上 = 这份表对我们没用,别拿一个只有 period 的壳去糊模型
    if not any(out.get(k) is not None for k in ("revenue", "net_profit", "eps")):
        logger.warning("[uzi] 东财财报科目未匹配 code={} 期={} 科目样例={}",
                       bare, out["period"], list(cur)[:6])
        return None
    return out


def _overseas_filings(bare: str, market: str) -> list[dict]:
    """美股 SEC / 港股披露易公告 · 统一 [{form,title,date,url}]。"""
    try:
        from app.services.gm import filings as _filings
        if market == "US":
            return _filings.us_filings(bare.upper(), 10) or []
        return _filings.hk_filings(bare.zfill(5), 10) or []
    except Exception as e:
        logger.warning("[uzi] 公告拉取失败 code={} market={} err={}", bare, market, e)
        return []


def _auth(request: Request) -> str:
    key = request.headers.get("X-Hunter-Internal-Key", "")
    if key != _INTERNAL_KEY:
        raise HTTPException(401, "internal auth failed")
    user_id = request.headers.get("X-Hunter-User-Id", "").strip()
    logger.info("[uzi] path={} user_id={}", request.url.path, user_id or "(missing)")
    return user_id


class DeepAnalysisIn(BaseModel):
    code: str
    depth: str = "lite"  # 保留字段 · Phase 1 只支持 lite
    # 报告的小标题结构 · 由 chat 模型按当前 SKILL 的方法论填。
    #
    # 2026-09-08:不同 SKILL 出来的报告一模一样(initiating-coverage、
    # stock-analysis、"写深度投研报告" 三次请求得到同一个「多空/技术/基本面/
    # 资金/催化风险/结论」六段)。原因是这个 tool 压根没有能传分析框架的入参 ——
    # 模型读了 SKILL 也使不上劲,SKILL 只能影响卡片之后那两三句。
    #
    # ⚠️ outline 是模型生成的文本,会进 prompt。它**只允许决定小标题结构**:
    # 合规硬约束(不给买卖评级 / 只用给定数据 / 不编数字)由 _build_prompt
    # 放在 outline 之前并再声明一次优先级,写进 outline 也覆盖不掉。
    outline: str = ""


# 币种跟着市场走 —— 美股写"元"会被模型当人民币,进而拿去和 A 股比估值。
_CURRENCY_UNIT = {"A": "元", "HK": "港元", "US": "美元"}


def _fmt_price_block(quote: dict | None, market: str = "A") -> str:
    if not quote:
        return "行情：数据缺失"
    unit = _CURRENCY_UNIT.get(market, "元")
    parts = [f"现价 {quote.get('price')} {unit}"]
    if quote.get("change_pct") is not None:
        parts.append(f"涨跌 {quote.get('change_pct')}%")
    amount = quote.get("amount")
    # 成交额缺失就不写这一项。写成 "0.00 亿" 的话模型会照着推理
    # 「成交额为 0 → 资金停滞」—— 缺数据被读成了一个信号。
    if amount:
        parts.append(f"成交额 {amount / 1e8:.2f} 亿{unit}")
    parts.append(f"截止 {quote.get('ts', '?')}")
    return " · ".join(parts)


def _fmt_overseas_financials(fin: dict | None) -> str:
    """港美股财务(东财年报)渲染 · 金额统一换算成亿。"""
    if not fin:
        return "财务：数据缺失"
    unit = _CURRENCY_UNIT.get(fin.get("market", "US"), "元")
    parts = [f"报告期={fin.get('period')}（年报）"]
    for key, label in (("revenue", "营业收入"), ("gross_profit", "毛利"),
                       ("net_profit", "净利润")):
        v = fin.get(key)
        if v is not None:
            parts.append(f"{label}={v / 1e8:.2f} 亿{unit}")
    if fin.get("gross_margin") is not None:
        parts.append(f"毛利率={fin['gross_margin']}%")
    if fin.get("revenue_yoy") is not None:
        parts.append(f"营收同比={fin['revenue_yoy']}%")
    if fin.get("net_profit_yoy") is not None:
        parts.append(f"净利同比={fin['net_profit_yoy']}%")
    if fin.get("eps") is not None:
        parts.append(f"每股收益={fin['eps']} {unit}")
    return "、".join(parts)


def _fmt_filings(items: list[dict]) -> str:
    """SEC / 披露易公告列表。"""
    if not items:
        return "公告：数据缺失"
    lines = []
    for it in items[:8]:
        title = (it.get("title") or it.get("form") or "").strip()
        lines.append(f"- {it.get('date', '?')} [{it.get('form', '?')}] {title}")
    return NL.join(lines)


def _fmt_kline_summary(kline: list[dict]) -> str:
    if not kline:
        return "K 线：数据缺失"
    if len(kline) < 2:
        return f"K 线：仅 {len(kline)} 根"
    first, last = kline[0], kline[-1]
    change = ((last.get("close", 0) - first.get("close", 0)) / first.get("close", 1)) * 100
    highs = [b.get("high", 0) for b in kline]
    lows = [b.get("low", 0) for b in kline if b.get("low")]
    return (
        f"近 {len(kline)} 根日线 · "
        f"从 {first.get('ts', first.get('date', '?'))[:10]} {first.get('close')} → "
        f"{last.get('ts', last.get('date', '?'))[:10]} {last.get('close')}（{change:+.2f}%）· "
        f"区间 {min(lows) if lows else '?'} - {max(highs)}"
    )


def _fmt_financials(fin) -> str:
    """finance-data /financial 返回 list of 25 季度报 · 最后一项是最新。"""
    if not fin:
        return "财务：数据缺失"
    if isinstance(fin, list):
        if not fin:
            return "财务：空列表"
        fin = fin[-1]  # 取最新季度
    if not isinstance(fin, dict):
        return "财务：格式异常"
    lines = []
    period = fin.get("m_timetag")
    if period:
        lines.append(f"报告期={period}")
    for key, label in [
        ("s_fa_eps_basic", "EPS"),
        ("s_fa_bps", "BPS"),
        ("du_return_on_equity", "ROE(%)"),
        ("sales_gross_profit", "毛利率(%)"),
        ("inc_revenue_rate", "营收同比(%)"),
        ("inc_net_profit_rate", "净利同比(%)"),
    ]:
        v = fin.get(key)
        if v is not None:
            lines.append(f"{label}={v}")
    return "、".join(lines) if lines else "财务：关键字段缺失"


def _fmt_lhb(lhb: list[dict]) -> str:
    if not lhb:
        return "龙虎榜：近 30 日无上榜"
    lines = [f"共 {len(lhb)} 次上榜"]
    for record in lhb[:5]:
        net = record.get("net_buy_amount") or 0
        try:
            net_str = f"{float(net) / 1e8:+.2f} 亿"
        except (TypeError, ValueError):
            net_str = "?"
        lines.append(
            f"  · {record.get('trade_date')} · 原因={record.get('reason')} · "
            f"净买入={net_str} · 涨跌={record.get('change_pct')}%"
        )
    return "\n".join(lines)


def _fmt_research(reports: list[dict]) -> str:
    """近期研报（评级 / 目标价 / 标题）· 显示前 5 篇 · 帮 LLM 感知机构一致预期。"""
    if not reports:
        return "研报：近期暂无收录"
    lines = [f"共 {len(reports)} 篇研报（显示最新 5 篇）"]
    for r in reports[:5]:
        date = str(r.get("publish_date") or "?")[:10]
        org = r.get("org_name") or "?"
        rating = r.get("rating") or ""
        tp = r.get("target_price")
        tp_str = f" · 目标价={tp}" if tp else ""
        lines.append(f"  · [{date}] {org} {rating}{tp_str} · {r.get('title', '')[:60]}")
    return "\n".join(lines)


def _fmt_governance(g) -> str:
    if not g:
        return "治理：暂无数据"
    if isinstance(g, list):
        if not g: return "治理：暂无数据"
        g = g[0]
    if not isinstance(g, dict):
        return "治理：格式异常"
    parts = []
    for k, label in [
        ("top_shareholder_pct", "第一大股东"),
        ("top5_pct", "前五合计"),
        ("top10_pct", "前十合计"),
        ("pledge_pct", "质押率"),
    ]:
        v = g.get(k)
        if v is not None:
            parts.append(f"{label}={v}%")
    return "、".join(parts) if parts else "治理：字段缺失"


def _fmt_fund_holders(holders: list[dict]) -> str:
    if not holders:
        return "十大股东：暂无数据"
    top3 = holders[:3]
    lines = [f"前三大：" + " / ".join(
        f"{h.get('holder_name', '?')}({h.get('shares_pct')}%)" for h in top3
    )]
    return "\n".join(lines)


def _fmt_news(news: list[dict], limit: int = 5) -> str:
    if not news:
        return "近期新闻：0 条"
    lines = []
    for n in news[:limit]:
        title = n.get("title", "?")
        date = (n.get("publish_date") or n.get("published") or "?")[:10]
        lines.append(f"  · [{date}] {title}")
    return "\n".join(lines)


def _market_of(code: str) -> str:
    """A / HK / US —— 决定哪些数据段**根本不该出现在提示词里**。

    ## 为什么必须分市场

    龙虎榜、十大流通股东、股权质押率、东财研报,全部是 **A 股专属**:
    · 龙虎榜   沪深交易所的每日异动榜,港美股没有这个东西
    · 十大流通股东  A 股的"流通股"概念,美股对应的是 13F,口径完全不同
    · 质押率   A 股大股东股权质押,美股不适用

    原来不分市场,给 NVDA 也拼上这几段。上游当然查不到,于是模型照实写:

        「近30日龙虎榜数据未 seed,十大流通股东最新持股变动数据未 seed」

    两个问题叠在一起:
      ① 拿 A 股的工具去查美股 —— 本来就不该查
      ② "未 seed" 是我们内部的说法,直接糊到了用户脸上

    用户看到的是"这个系统连英伟达的基本面都拿不到",而真相是
    "我们拿 A 股的龙虎榜去查美股,当然查不到"。

    ## 判定本身不在这里实现

    2026-09-08:这里原来自己写了一套(6 位→A / 5 位→HK / 其余→US),
    而 `market_source.market_of` 早就有一套带实测论证的(还处理了 .HK/.US
    后缀和 BRK.B 这类**本身带点**的 ticker)。同一件事两处实现,迟早打架
    —— 仓里已经吃过一次亏(见 CLAUDE.md「多处写死会打架」)。
    这里只负责把小写结果转成本文件用的大写键。
    """
    from app.services.market_source import market_of as _market_source_of
    return _market_source_of(code).upper()


# 每个市场**有意义**的数据段。不在表里的直接不拼进提示词 ——
# 拼进去再让模型说"没有",等于让它替我们的选型错误背书。
_SECTIONS_BY_MARKET = {
    "A":  ["quote", "kline", "financials", "lhb", "fund_holders",
           "governance", "news", "research"],
    # 港美股的"公告"是 SEC filings / 披露易,和 A 股的东财新闻不是一回事,
    # 单开一段。2026-09-08 之前这两个市场只有 4 段,其中 financials 还没人去取,
    # 于是实际只剩行情+K线+新闻 —— 大佬评审团里凡是要看基本面的流派全写"暂无数据"。
    "HK": ["quote", "kline", "financials", "news", "filings"],
    "US": ["quote", "kline", "financials", "news", "filings"],
}


# outline 是模型生成的文本,进 prompt 前要限长:太长会挤掉数据段,
# 也给 prompt 注入更大的空间。几行小标题够用了。
_MAX_OUTLINE = 1200
# 拼 prompt 用。写成常量而不是字面量,是因为这些字符串要经 heredoc/脚本
# 多层转义落盘,反斜杠很容易被吃掉一层(本文件改动时踩过两次)。
NL = chr(10)
NL2 = NL + NL


def _sanitize_outline(outline: str) -> str:
    """把模型传来的 outline 收拾成"只是一份小标题清单"。

    它是**不可信输入**(模型生成 · 而模型读过第三方 SKILL 正文,
    SKILL 里完全可能写着"给出买入评级"这类与我们合规约束冲突的要求)。
    这里只做两件事:限长、剥掉围栏代码块;真正的防线是
    `_build_llm_context` 把合规约束放在 outline **之前**并再声明一次优先级 ——
    位置和显式优先级比过滤关键词可靠,后者永远列不全。
    """
    if not outline:
        return ""
    out = re.sub(r"^```.*?^```", "", outline, flags=re.M | re.S).strip()
    if len(out) > _MAX_OUTLINE:
        out = out[:_MAX_OUTLINE].rstrip() + "\n…（结构过长已截断）"
    return out


def _build_llm_context(code: str, bundle: dict, outline: str = "") -> str:
    """把数据组织成给 Gemini 的上下文 · 尽量密集不冗余。

    **按市场裁剪**:龙虎榜/十大流通股东/治理/研报是 A 股专属,
    港美股连拼都不拼进来 —— 见 _market_of 的说明。
    """
    mk = _market_of(code)
    allow = set(_SECTIONS_BY_MARKET.get(mk, _SECTIONS_BY_MARKET["US"]))

    blocks: list[str] = []
    n = 0

    def _add(key: str, title: str, body: str) -> None:
        nonlocal n
        if key not in allow:
            return
        n += 1
        blocks.append(f"## {n}. {title}\n{body}")

    _add("quote", "实时行情", _fmt_price_block(bundle.get("quote"), mk))
    _add("kline", "K 线（近 30 日）", _fmt_kline_summary(bundle.get("kline") or []))
    # 财务的来源和口径按市场分：A 股是 finance-data / 东财 A 股季报（TTM 口径），
    # 港美股是东财年报（营收/毛利/净利/EPS + 同比）。两种 dict 结构不一样，
    # 渲染函数不能共用 —— 混用的表现是财务段整段变成"关键字段缺失"。
    if mk == "A":
        _add("financials", "财务（TTM · 最新季）", _fmt_financials(bundle.get("financials")))
    else:
        _add("financials", "财务（最新年报）",
             _fmt_overseas_financials(bundle.get("financials")))
    _add("lhb", "龙虎榜（近 30 日）", _fmt_lhb(bundle.get("lhb") or []))
    _add("fund_holders", "十大流通股东（最新季度）",
         _fmt_fund_holders(bundle.get("fund_holders") or []))
    _add("governance", "治理指标", _fmt_governance(bundle.get("governance")))
    _add("news", "近期公告 / 新闻", _fmt_news(bundle.get("news") or []))
    _add("research", "近期研报（券商一致预期）", _fmt_research(bundle.get("research") or []))
    _add("filings", "近期公告（美股 SEC / 港股披露易 · 官方源）",
         _fmt_filings(bundle.get("filings") or []))

    market_label = {"A": "A 股", "HK": "港股", "US": "美股"}[mk]

    # 「资金/情绪」那一段该看什么,随市场变。
    # A 股看龙虎榜和十大股东;港美股这两样根本不存在,只能看新闻主线 ——
    # 硬要求它"结合龙虎榜"的话,它只会写一句"龙虎榜数据缺失"。
    if mk == "A":
        senti_hint = "（结合龙虎榜 · 十大股东 · 新闻主线 · 研报评级）"
        fund_hint = "（结合财务 · ROE · 增速 · 治理）"
        extra_rule = (
            "   - **匿名化游资/席位名**：龙虎榜里如出现\"章盟主 / 赵老哥 / 佛山无影脚\""
            "等游资/席位真名 · 改用\"A 席位 / B 席位\"或\"活跃席位\"泛化描述\n"
        )
    else:
        # ⚠️ 提示里**不要出现**"龙虎榜""十大流通股东"这些词,哪怕是在否定句里。
        # 写成「本市场没有龙虎榜,不要提」的话,这几个字仍然进了上下文,
        # 模型很容易顺手就在输出里复述一句"本市场无龙虎榜数据" ——
        # 用户看到的还是一句莫名其妙的 A 股术语。
        # 正确做法是只说该看什么,不说不该看什么。
        senti_hint = "（结合新闻主线与成交量变化 · 只用上面给出的数据段）"
        # 港美股财务走的是东财年报,里面没有 ROE —— 提示词里点名一个数据段里
        # 根本没有的指标,模型要么写"缺失",要么自己算一个出来。
        fund_hint = "（结合营收 / 净利同比 · 毛利率 · 每股收益 · 公告披露的事项）"
        extra_rule = ""

    # ── 报告结构 ──────────────────────────────────────────────
    #
    # 调用方(chat 模型)按当前 SKILL 的方法论传 outline,不传就用默认六段。
    #
    # 2026-09-08 之前这里是**写死的**六段,于是 initiating-coverage、
    # stock-analysis、"写份深度投研报告" 三种请求得到一模一样的报告 ——
    # SKILL 的方法论从来没进过报告生成。
    #
    # outline 只决定小标题;上面那段"合规硬约束"在它**之前**,
    # 并且下面再显式声明一次优先级 —— SKILL 是第三方写的,里面完全可能
    # 要求"给出买入/卖出评级""给目标价",那类要求必须被挡住。
    # 开头那条"直接从 X 开始输出"必须跟着 outline 变 —— 写死成
    # "### 一、多空核心观点" 的话,传了 outline 也会跟新结构打架,
    # 模型可能仍旧按老六段写。(改 outline 时实测到的:这一句漏改,
    # prompt 里就同时存在两套结构要求。)
    if outline:
        first_heading = ""
        for _ln in outline.split(NL):
            _ln = _ln.strip()
            if _ln.startswith("###"):
                first_heading = _ln
                break
        first_heading = first_heading or "### 一、"
        # ⚠️ **必须把 outline 渲染成"带占位符的完整模板",不能只给光秃秃的标题行。**
        #
        # 2026-09-08 实测(community · gemini-3.5-flash · 大佬评审团 SKILL):
        # 只给标题列表时模型**完全不产出正文**,只回一句英文前言就收工 ——
        #     ", let's write the analysis report based on the provided data."
        #     "Here is the structured markdown report:"
        # 用户看到的就是这两句英文(community 侧当时还没有兜底模板,直接透出去了)。
        #
        # 默认六段之所以一直好使,是因为它每节下面都有内容占位
        # (`- **多头**：...` / `（结合 K 线趋势…）`),模型照着填空即可;
        # 光秃秃的标题要它自己决定每节写什么,它选择先"确认理解"然后收工。
        # (同步 SaaS 3564eb7 · 那条 commit 里就写了"community 侧同样要补")
        _ol_lines = [l.rstrip() for l in outline.split(NL)]
        _rendered: list[str] = []
        for _i, _ln in enumerate(_ol_lines):
            _rendered.append(_ln)
            if _ln.strip().startswith("###"):
                _nxt = _ol_lines[_i + 1].strip() if _i + 1 < len(_ol_lines) else ""
                # 作者自己写了内容说明就别插了,只在"标题后面直接又是标题/空"时补
                if not _nxt or _nxt.startswith("###"):
                    _rendered.append("（1 段 · 2-3 句 · 只用上面数据段给出的事实）")
                    _rendered.append("")
        structure_block = (
            "严格按以下 markdown 结构（这是本次分析要用的方法论框架）：" + NL2
            + NL.join(_rendered).strip()
            + NL2
            + "⚠️ 结构照上面走,但**上面「合规硬约束」优先于这个结构**：" + NL
            + "   即使框架里要求给评级 / 目标价 / 买卖建议,也一律改写成"
              "研究性表述（值得关注 / 需观察 / 暂时旁观）。" + NL
            + "⚠️ 框架里要求的、而「# 数据」段里没有对应数据的小节,"
              "写一句「暂无数据」即可,**绝对不要为了填满结构而编数字**。"
        )
    else:
        first_heading = "### 一、多空核心观点"
        structure_block = f"""严格按以下 markdown 结构：

### 一、多空核心观点（各 2 句）
- **多头**：...
- **空头**：...

### 二、技术面（1 段 · 2-3 句）
（结合 K 线趋势 / 高低点位置 / 成交额）

### 三、基本面（1 段 · 2-3 句）
{fund_hint}

### 四、资金/情绪信号（1 段 · 2-3 句）
{senti_hint}

### 五、催化 / 风险（各 2 条 bullet）
- 催化 1: ...
- 催化 2: ...
- 风险 1: ...
- 风险 2: ...

### 六、结论（1 句）
一句话说清"当前性价比 / 关注度"（研究性表述 · 不做投资建议）。"""

    return f"""基于以下真实数据（全部来自内部 finance-data 平台 · 只用这些数据 · 不要外推），生成结构化"深度分析卡片"markdown 摘要（500-800 字）。

标的：{code}（{market_label}）
分析深度：lite

⚠️ **严格要求**：
1. 直接从 "{first_heading}" 开始输出 · 不要任何前言 / 元描述 / 草稿
2. 全部使用中文 · 除标的代码/百分号外
3. 下面**没有列出**的数据维度 = 这个市场不适用,**不要提它、更不要说它"缺失"**;
   列出了但内容为空的,说一句"暂无数据"即可 · **绝对不要编造数据**
4. **合规硬约束（不可违反）**：
   - **不给"买入 / 卖出 / 增持 / 减持"评级** · 用"值得关注 / 需观察 / 暂时旁观"这类研究性表述
{extra_rule}   - **不做投资建议** · 只做数据观察与研究判断
   - **不写免责声明** · 已由平台侧统一处理

# 数据

{chr(10).join(blocks)}

# 输出要求

{structure_block}

⚠️ 上面没给的数据不要编 · 也不要罗列"缺了什么"。
"""


# 模型在正文之前爱说的那些话。命中就往下再看一行。
_META_PREFIX = (
    "here's", "here is", "sure", "okay", "ok,", "alright",
    "let's", "let me", "i'll", "i will", "based on the provided",
    "below is", "certainly", "of course", "好的", "以下是", "根据以上",
)


def _strip_leading_meta(md: str) -> str:
    """剥掉正文之前的元话唠 —— 给没有固定锚点的模板兜底。

    判定"正文开始了"的标志:markdown 标题、加粗行、列表项,
    或者一行以中文开头且不在元话唠清单里的内容。

    保守起见:全篇都被判成元话唠时(说明判断错了)原样返回。
    """
    lines = md.split("\n")
    for i, raw in enumerate(lines):
        ln = raw.strip()
        if not ln:
            continue
        low = ln.lower().lstrip(",.、,。 ")
        if any(low.startswith(p) for p in _META_PREFIX):
            continue
        # 一行以标点开头(", here's ...")是被截断的元话唠残渣
        if ln[0] in ",.,。;;:":
            continue
        return "\n".join(lines[i:]).strip()
    return md.strip()


def _outline_anchor(outline: str) -> str:
    """从 outline 里取出第一个小标题,给 _clean_llm_markdown 当正文起点锚点。

    传了 outline 之后正文就不再以 "### 一、" 开头了,原来那个写死的锚点会失配。
    只取前 8 个字符:模型复述标题时常改标点(顿号→点、全角→半角),
    整行比对反而更容易失配。取不到就返回空,让调用方退回默认锚点。

    ⚠️ **必须先砍掉括号里的说明**。outline 常写成
        ### 一、大佬评审团投票分布 (牛/熊/中性比例及综合评分)
    而模型输出时只写「### 一、大佬评审团投票分布」—— 括号里那句是给它的
    指示,不是标题的一部分。不砍的话 anchor 变成「一、大佬评审团投」后面
    还带半个括号,直接失配,正文起点找不到 → 整篇被当成元话唠。
    (同步 SaaS c056549)
    """
    for line in (outline or "").split(NL):
        line = line.strip()
        if line.startswith("###"):
            head = line.lstrip("#").strip()
            for br in ("(", "（"):
                if br in head:
                    head = head.split(br)[0].strip()
            if head:
                return head[:8]
    return ""


def _clean_llm_markdown(md: str, anchor: str = "一、") -> str:
    """剥 Gemini 的 draft / review 元话唠 · 保守策略：
    1. 找**最后一次** '### 一、多空核心观点' 出现的行作为正文起点（跳过前面的 outline plan）
    2. 从 start 往后扫 · 只在遇到明确的英文元话唠时截断（Let's / Reviewing / Checking / Note:）
       · 不因 '- ' bullet 截断 · 结论段可能就是列表
    3. 或遇到第二次 '### 一、多空核心观点'（LLM 重开一遍）时截断
    """
    if not md:
        return md
    lines = md.split("\n")
    # 找最后一次 (line 内容真的以 "### 一、" 开头 · 无缩进无 bullet marker)
    start_idx = None
    for i, line in enumerate(lines):
        if line.startswith("###") and anchor in line:
            start_idx = i
    if start_idx is None:
        # 找不到 '### 一、' —— **不能原样返回**。
        #
        # 这个函数原本只服务「深度分析」那一种模板(它固定以
        # "### 一、多空核心观点" 开头)。但同一条链路也在跑别的 SKILL,
        # 比如「66 位大佬评审团」用的是完全不同的结构,根本没有这个锚点。
        # 于是 start_idx 为 None → 原样 return → 模型的英文前言被留在卡片里:
        #
        #     , here's the structured markdown analysis based on the
        #     provided data. Let's write it directly.
        #
        # 用户看到的富卡片里就只有这一句英文,正文全在下面的对话正文里。
        #
        # 兜底:把开头的元话唠逐行剥掉,直到遇到第一行像正文的内容
        # (标题 / 加粗 / 列表 / 中文段落)。剥不动就原样返回,
        # 宁可多留几个字,也不要把正文剪没了。
        return _strip_leading_meta(md)

    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        line = lines[i]
        stripped = line.lstrip()
        # 只在明确英文元话唠时截断
        if stripped.startswith(("Let's", "Let me", "Reviewing", "Checking",
                                "Note:", "Now, let", "```")):
            end_idx = i
            break
        # 或 LLM 重新开一份报告
        if line.startswith("###") and anchor in line:
            end_idx = i
            break

    return "\n".join(lines[start_idx:end_idx]).strip()


def _has_report_body(markdown: str, anchor: str) -> bool:
    """判断 LLM 到底写没写正文 —— 有一行 ### 标题命中锚点才算。"""
    return any(ln.startswith("###") and anchor in ln for ln in (markdown or "").split(NL))


def _fallback_markdown(code: str, name: str, market: str, bundle: dict) -> str:
    """LLM 没产出正文时的兜底 —— 只复述**已经取到的真实数据**,不替它下判断。

    ⚠️ 两条硬约束,都是从 SaaS 那份老兜底的教训来的:
    1. **不许把取到的数据说成没取到**。老版写死一句「龙虎榜/十大股东/新闻/
       研报维度均未采集」,news 明明有 8 条也照说,美股还被硬塞了 A 股术语。
    2. **不许下判断**。老版会写「暂时旁观为宜」——那是分析结论,不是数据。
       本地拼的模板没有分析能力,冒充分析就是在编。
    只列真实数字 + 一句"本次 AI 正文未生成",让用户知道发生了什么。
    """
    quote = bundle.get("quote") or {}
    kline = bundle.get("kline") or []
    fin = bundle.get("financials")
    filings = bundle.get("filings") or []
    news = bundle.get("news") or []

    parts = [f"### {name}（{code}）· 本次数据摘要", ""]
    parts.append("> 本次 AI 正文未能生成（模型只回了开场白）。以下是本次实际取到的"
                 "原始数据，未经任何推断，可直接重试一次。")
    parts.append("")

    if quote:
        parts.append("**行情**：" + _fmt_price_block(quote, market))
    if kline:
        parts.append("**K 线**：" + _fmt_kline_summary(kline))
    if fin:
        parts.append("**财务**：" + (_fmt_financials(fin) if market == "A"
                                     else _fmt_overseas_financials(fin)))
    if filings:
        parts.append("")
        parts.append("**近期公告**：")
        parts.append(_fmt_filings(filings))
    if news:
        parts.append("")
        parts.append("**近期新闻**：")
        parts.append(_fmt_news(news, 5))
    return NL.join(parts).strip()


def _stock_name(code: str) -> str:
    """从 STOCK_MAP / dynamic_map / DB watchlist 拿股票中文名。"""
    from app.config import STOCK_MAP
    bare = code.split(".")[0]
    s = STOCK_MAP.get(bare) or fd._dynamic_map.get(bare)
    if s and s.get("name"):
        return s["name"]
    # DB 兜底
    try:
        from app.services.database import get_conn
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT name FROM stocks WHERE code = %s AND deleted = FALSE LIMIT 1",
                (bare,),
            )
            row = cur.fetchone()
            if row and row[0]:
                return row[0]
    except Exception as _e:
        pass
    return bare


@router.post("/uzi/stock_deep_analysis")
async def deep_analysis(body: DeepAnalysisIn, request: Request):
    """chat 里深度分析入口 · Phase 1 · 秒级返回 markdown。"""
    _auth(request)
    code = body.code.strip()
    if not code:
        raise HTTPException(400, "code 不能为空")

    t0 = datetime.now()
    # 同步 httpx 客户端 · 在 async endpoint 里必须走 to_thread 避免阻塞事件循环
    # （从 async 直接 sync 调 httpx.get 会遇到 connection pool 或事件循环冲突 · 表现为 None 返回）
    #
    # ⚠️ **先判市场,再决定拉哪几路。** 2026-09-08 之前这里无条件走 A 股八路:
    # 给 GOOG 也去拉龙虎榜/十大股东/治理/研报(白等 0.4s,四路必空),
    # 而美股真正有的 SEC 公告和东财美股财报一路都没拉 —— 结果就是
    # 「66 位大佬评审团」里凡要看基本面的流派全写"暂无数据"。
    # 拉哪几路必须和 _SECTIONS_BY_MARKET(哪几段进提示词)对齐,否则要么白拉,
    # 要么拉了不用。
    market = _market_of(code)
    _bare = code.split(".")[0].strip()
    timing: dict[str, int] = {}
    try:
        _t = time.perf_counter()
        if market == "A":
            # A 股八路并发 · 总预算 _FETCH_BUDGET_S · 各路含义见 bundle 的 key
            bundle = await _gather_budget({
                "quote":        asyncio.to_thread(fd.get_quote, code),
                "kline":        asyncio.to_thread(fd.get_kline, code, "daily", 30),
                # 财报走 shared akshare(§9 铁律 3 · 独立部署时 fd._get 会打空)
                # · 主路径直接是 akshare · 不再作为 fd fallback 的备胎
                "financials":   asyncio.to_thread(_akshare_financials, _bare),
                "lhb":          asyncio.to_thread(fd.get_lhb, code, 30),
                "fund_holders": asyncio.to_thread(fd.get_fund_holders, code),
                "governance":   asyncio.to_thread(fd.get_governance, code),
                "news":         asyncio.to_thread(fd.get_news, code, 8),
                "research":     asyncio.to_thread(fd.get_research_reports, code, 10),
            }, _FETCH_BUDGET_S, f"主拉数 A code={code}")
        else:
            # 港美股五路 · quote/kline/news 走 finance_data_client(它内部对港美股
            # 已经会回落到 market_source 的腾讯/新浪通道,实测 GOOG 报价 192ms、
            # 日线 30 根),financials/filings 是本次新接的东财财报 + SEC/披露易。
            bundle = await _gather_budget({
                "quote":      asyncio.to_thread(fd.get_quote, code),
                "kline":      asyncio.to_thread(fd.get_kline, code, "daily", 30),
                "news":       asyncio.to_thread(fd.get_news, code, 8),
                "financials": asyncio.to_thread(_em_financials, _bare, market),
                "filings":    asyncio.to_thread(_overseas_filings, _bare, market),
            }, _FETCH_BUDGET_S, f"主拉数 {market} code={code}")
        timing["fetch_ms"] = int((time.perf_counter() - _t) * 1000)
    except Exception as e:
        logger.exception("[uzi] 拉数失败 code=%s market=%s", code, market)
        raise HTTPException(502, f"拉取数据失败: {e}")

    # akshare 兜底 · 只针对 A 股 · finance-data 没订阅时 5/8 维度会空,
    # 用东财公开接口补 kline/financials/news/research/lhb。港股/美股无此路径
    # (它们的兜底在上面那条分支里就已经是主路径了)。
    if market == "A":
        fb_tasks: dict[str, Awaitable[Any]] = {}
        if not bundle.get("kline"):
            fb_tasks["kline"] = asyncio.to_thread(_akshare_kline, _bare, 30)
        # financials 已在主 gather 里走 shared akshare · 这里不再重复补
        if not bundle.get("news"):
            fb_tasks["news"] = asyncio.to_thread(_akshare_news, _bare, 8)
        if not bundle.get("research"):
            fb_tasks["research"] = asyncio.to_thread(_akshare_research, _bare, 10)
        if not bundle.get("lhb"):
            fb_tasks["lhb"] = asyncio.to_thread(_akshare_lhb, _bare, 30)
        if fb_tasks:
            _t = time.perf_counter()
            fb_results = await _gather_budget(fb_tasks, _FALLBACK_BUDGET_S, f"akshare 兜底 code={code}")
            timing["fallback_ms"] = int((time.perf_counter() - _t) * 1000)
            filled: list[str] = []
            for slot, r in fb_results.items():
                if not r:
                    continue
                bundle[slot] = r
                filled.append(slot)
            logger.info("[uzi] akshare fallback code={} tried={} filled={}", code, list(fb_tasks), filled)

    # 走 OneAPI Gemini 合成
    client = get_client()
    if client is None:
        raise HTTPException(503, "LLM 客户端不可用 · 检查 .env 里 LLM_API_KEY / LLM_BASE_URL / LLM_DEFAULT_MODEL")

    # ⚠️ system 里的结构描述**必须跟着 outline 走**。
    #
    # 这里原本写死"从 '### 一、多空核心观点' 开头 · 到 '### 六、结论' 结束"。
    # 加了 outline 之后忘了改这一处,于是 system 和 user 各说一套结构,
    # 模型当场困惑并把内心戏打印了出来(实测原文):
    #     Wait, is there more to the structure? ...
    #     If I only output these two, it might be too short or violate
    #     the developer prompt's "从 '### 一、多空核心观点' 开头..."
    # 用户看到的就是一段英文推理。**同一件事写在两处、只改一处**,
    # 后果不是不生效,是两套指令打架。
    _outline = _sanitize_outline(body.outline)
    if _outline:
        _struct_line = ("**第一个字符必须是 `#`** · 严格按用户消息里给出的"
                        "小标题结构输出 · 中间只保留正文。")
    else:
        _struct_line = ("**第一个字符必须是 `#`(即以 `### 一、多空核心观点` 开头)**"
                        " · 到 '### 六、结论' 结束 · 中间只保留正文。")
    system_msg = (
        "你是一位专业的 A 股 / 港股 / 美股深度分析师。"
        "**只输出最终 markdown 报告本体**，不做任何思考过程 / 草稿 / 数据罗列 / 分析步骤说明。"
        "不写 'Let me analyze' / 'I will analyze' / 'Analysis of' / 'Draft Structure' / '### Draft' 等元描述。"
        + _struct_line +
        "全程使用中文（除股票代码外）· 不做免责声明 · 不给'买入/卖出'评级 · 不给目标价。"
    )
    user_msg = _build_llm_context(code, bundle, _outline)

    def _llm_call(sys_msg: str = "", temperature: float = 0.35):
        # OpenAI 客户端是同步的 · 直接在 async 端点里调会把整个事件循环卡住 LLM 那么久
        # (期间 /api/chat/sessions 等所有请求都排队)。挪进线程,并给这一次调用单独限时。
        return client.with_options(timeout=_LLM_TIMEOUT_S).chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": sys_msg or system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=temperature,
            # 原来是 1200 · 但社区版常用 deepseek-v4-pro 这类**推理型模型**,
            # 内部会先跑一大段 reasoning 再输出正文,1200 全被 reasoning 吃掉,
            # message.content 返回空串,前端就看到"深度分析报告仍为空"。
            # 提到 4096 给正文留足空间,非推理模型也不会浪费(只按实际生成计费)。
            max_tokens=4096,
        )

    try:
        _t = time.perf_counter()
        # 外层再套 5s 余量 · 防 SDK 自己的重试把 timeout 放大
        resp = await asyncio.wait_for(asyncio.to_thread(_llm_call), timeout=_LLM_TIMEOUT_S + 5)
        timing["llm_ms"] = int((time.perf_counter() - _t) * 1000)
        markdown = (resp.choices[0].message.content or "").strip()
        # 空返回时把 usage 打进日志,方便判断是"tokens 耗尽"还是"模型拒答"
        if not markdown:
            usage = getattr(resp, "usage", None)
            finish = resp.choices[0].finish_reason if resp.choices else "?"
            logger.warning(
                "[uzi] LLM 返回空 markdown · code={} finish={} usage={}",
                code, finish,
                {"prompt": getattr(usage, "prompt_tokens", None),
                 "completion": getattr(usage, "completion_tokens", None)} if usage else None,
            )
        _anchor = _outline_anchor(_outline) or "一、"
        markdown = _clean_llm_markdown(markdown, _anchor)

        # 模型只回一句开场白就收工时(实测 llm_ms 才 1.1s、正文 0 个标题),
        # 降温 + 点名上次的问题重跑一次。摆烂时那次调用本来就很快,重试很便宜,
        # 比直接给用户一份数据摘要划算。
        if not _has_report_body(markdown, _anchor):
            logger.warning("[uzi] LLM 未产出正文 · 重跑一次 · code={} raw_head={!r}",
                           code, markdown[:120])
            _retry_sys = system_msg + (
                "上一次你只回了一句开场白就结束了。本次**直接从 ### 开始输出正文**,"
                "每个小标题下面都要有 2-3 句正文,不要任何开场白、确认语、结束语。")
            _t = time.perf_counter()
            resp2 = await asyncio.wait_for(
                asyncio.to_thread(_llm_call, _retry_sys, 0.2), timeout=_LLM_TIMEOUT_S + 5)
            timing["llm_retry_ms"] = int((time.perf_counter() - _t) * 1000)
            markdown = _clean_llm_markdown(
                (resp2.choices[0].message.content or "").strip(), _anchor)
    except asyncio.TimeoutError:
        logger.error("[uzi] LLM 合成超时 code={} model={} 限时 {:.0f}s · timing={}", code, _MODEL, _LLM_TIMEOUT_S, timing)
        raise HTTPException(504, f"LLM 合成超时(>{_LLM_TIMEOUT_S:.0f}s · model={_MODEL})")
    except Exception as e:
        logger.exception("[uzi] LLM 失败 code=%s", code)
        raise HTTPException(502, f"LLM 合成失败: {e}")

    dims_covered = [k for k, v in bundle.items() if v not in (None, [], {})]
    dims_missing = [k for k in bundle if k not in dims_covered]

    # 重试之后仍然没有正文 —— 给一份"只有真实数据"的摘要,别把英文残句
    # (", let's write the analysis report ...")当报告透给用户。
    _quote_name_for_fb = (bundle.get("quote") or {}).get("name") or _stock_name(code)
    used_fallback = False
    if not _has_report_body(markdown, _outline_anchor(_outline) or "一、") or len(markdown) < 100:
        logger.warning("[uzi] 兜底数据摘要 code={} clean_len={} head={!r}",
                       code, len(markdown), markdown[:120])
        markdown = _fallback_markdown(code, _quote_name_for_fb, market, bundle)
        used_fallback = True

    duration_ms = int((datetime.now() - t0).total_seconds() * 1000)
    logger.info("[uzi] 完成 code={} market={} total={}ms timing={} covered={} missing={} fallback={}",
                code, market, duration_ms, timing, dims_covered, dims_missing, used_fallback)

    return {
        "ok": True,
        "code": code,
        # 调用方(chat 模型 / 前端卡片 / 排查的人)得知道这份报告是按哪个市场取的数,
        # 否则"为什么没有龙虎榜"这种问题只能靠猜。
        "market": market,
        # 港美股的中文名来自行情源(腾讯返 "谷歌-C"),STOCK_MAP / watchlist 里通常
        # 没有,_stock_name 会退化成原样吐 "GOOG"。有真名就用真名。
        "name": _quote_name_for_fb,
        "depth": body.depth,
        "markdown": markdown,
        "dims_covered": dims_covered,
        "dims_missing": dims_missing,
        # ⚠️ **兜底状态必须暴露给调用方**(同步 SaaS f0f88bd)。
        # 调用方(chat 模型、前端卡片、以及排查的人)拿到一份报告,得能分清
        # 它是 LLM 真写的还是本地拼的数据摘要 —— 否则排查时会误判两轮。
        "used_fallback": used_fallback,
        "duration_ms": duration_ms,
        "timing": timing,
        "model": _MODEL,
        "note": "Phase 1 MVP · 数据源 finance-data · LLM=OneAPI Gemini · 完整 22 dim 报告见 SG UZI worker（后续 phase）",
    }
