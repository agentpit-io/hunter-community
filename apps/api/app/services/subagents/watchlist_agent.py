"""自选股 · 3 tool sub-agent（P0+P1）

- stock_quickview : 单股速答（股价卡 + AI 短评 + 按钮 CTA）
- stock_news      : 5 条精选新闻 + 每条影响短评
- watchlist_digest: 用户自选清单今日 Top3 涨/跌 + AI 归因

统一签名：@ToolRegistry.register → dispatch → ToolResult(status, summary, detail_ref)
summary 里的 JSON 结构由前端 tool_cards/*.tsx 匹配 tool 名分派渲染。
"""
from __future__ import annotations
import asyncio
import os
import time
from typing import Optional

from loguru import logger

from app.services.agent.tool_registry import ToolCall, ToolRegistry, ToolResult
from app.services.online_analysis.llm_client import get_client
from app.services import finance_data_client as fd
from app.services.database import get_stocks_by_user, get_all_stocks_by_user

# Redis 直连 · A 股优先读 collector 每 30s 写入的最新 quote:{code}
# fd.get_quote 走 finance-data.agentpit.io HTTP · 上游可能陈旧（详见 2026-08 排查）
import json as _json
import redis as _redis_mod
_REDIS = _redis_mod.Redis.from_url(
    os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True,
)


def _get_a_quote_from_redis(code: str) -> dict | None:
    """A 股行情 · 从 Redis quote:{code} 读 collector 最新写入。返回 dict 或 None。"""
    try:
        raw = _REDIS.get(f"quote:{code}")
        if not raw:
            return None
        q = _json.loads(raw)
        # 统一字段命名 · Redis 存的是 prev_close · fd.get_quote 用 pre_close · 兼容
        if "prev_close" in q and "pre_close" not in q:
            q["pre_close"] = q["prev_close"]
        return q
    except Exception as e:
        logger.warning("[wl_agent] Redis quote:{} 读失败: {}", code, e)
        return None


_MODEL = os.getenv("AGENT_SUB_WL_MODEL", "gemini-3.5-flash")


# ═════════════════════════════════════════════════════════════════
# 通用 helper · LLM 短评
# ═════════════════════════════════════════════════════════════════

def _llm_short(system: str, user: str, max_tokens: int = 80) -> str:
    """调 LLM 生成短评 · 失败时静默返回空串。"""
    client = get_client()
    if client is None:
        return ""
    try:
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.4,
        )
        text = (resp.choices[0].message.content or "").strip()
        # 剥常见前后缀
        for prefix in ("短评：", "评价：", "点评："):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
        return text[:200]
    except Exception as e:
        logger.warning("[wl_agent] LLM 短评失败: {}", e)
        return ""


# ═════════════════════════════════════════════════════════════════
# TOOL 1 · stock_quickview
# ═════════════════════════════════════════════════════════════════

_QUICKVIEW_DEF = {
    "name": "stock_quickview",
    "description": (
        "单股速答：拉取实时股价、涨跌、成交量、52 周区间、估值元数据，"
        "并生成一句 AI 短评。适合用户问『{股票} 今天怎么样 / 现在如何 / 值不值得买』时使用。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "6 位 A 股代码 / 5 位港股 / US 代码"},
            "user_id": {"type": "string", "description": "内部用 · 由 orchestrator 上下文注入判断是否已加自选"},
        },
        "required": ["code"],
    },
}


_QUICKVIEW_SYS = (
    "你是猎鹿人短评助手。给你一只股票的实时行情+估值元数据，用中文写一句 30-60 字的短评。"
    "覆盖：所在板块 / 定性判断（偏强/偏弱/横盘）/ 简短理由（估值/资金/趋势）。"
    "不给出买卖建议。语气冷静、专业、无营销感。"
)


async def _quickview(code: str, user_id: Optional[str] = None) -> dict:
    """构造 stock_quickview 结构化 summary。"""
    q = await asyncio.to_thread(fd.get_quote, code)
    if not q:
        return {"type": "stock_quickview", "code": code, "error": "无法获取行情（可能停牌或代码错误）"}

    # 拿近 252 交易日 K 线 · 算 52 周高低
    bars: list = await asyncio.to_thread(fd.get_kline_with_fallback, code, "daily", 252)
    high52 = max((b.get("high", 0) for b in bars), default=0) or 0
    low52 = min((b.get("low", 999999) for b in bars if b.get("low", 0) > 0), default=0) or 0

    # 判断是否已在自选（若有 user_id）
    in_wl = False
    if user_id:
        try:
            stocks = await asyncio.to_thread(get_stocks_by_user, user_id)
            in_wl = any(s.get("code") == code for s in stocks or [])
        except Exception:
            pass

    # 短评 · 组合基本信息喂 LLM
    price = q.get("price", 0)
    chg_pct = q.get("change_pct", 0)
    pos_in_range = ""
    if high52 > 0 and low52 > 0 and high52 > low52:
        pct = (price - low52) / (high52 - low52) * 100
        pos_in_range = f"52 周区间 {low52:.2f}-{high52:.2f}（当前位于 {pct:.0f}% 分位）"
    llm_input = (
        f"股票：{q.get('name')}（{code}）\n"
        f"当前价：{price:.2f} · 涨跌 {chg_pct:+.2f}%\n"
        f"今日：开 {q.get('open', 0):.2f} 高 {q.get('high', 0):.2f} "
        f"低 {q.get('low', 0):.2f} 成交额 {q.get('amount', 0) / 1e8:.1f} 亿\n"
        f"{pos_in_range}"
        # 估值也喂给写短评的 LLM —— 它看得到真实 PE/PB 才不会瞎说
        + ((chr(10) + "估值:PE {} · PE(TTM) {} · PB {}({})".format(
            q.get("pe"), q.get("pe_ttm"), q.get("pb"), q.get("valuation_date")))
           if q.get("pe") is not None else "")
    )
    ai_comment = await asyncio.to_thread(_llm_short, _QUICKVIEW_SYS, llm_input, 100)

    # 52 周分位（用于前端进度条）
    range_pos = None
    if high52 > 0 and low52 > 0 and high52 > low52:
        range_pos = round(min(1.0, max(0.0, (price - low52) / (high52 - low52))), 4)

    market = q.get("market", "A")
    mkt_suffix = {"A": "SH" if code.startswith(("6", "9")) else "SZ", "HK": "HK", "US": "US"}.get(market, "")

    return {
        "type": "stock_quickview",
        "code": code,
        "market": market,
        "market_suffix": mkt_suffix,
        "name": q.get("name", code),
        "price": {
            "current": round(price, 2),
            "change": round(q.get("change_amt", 0), 2),
            "change_pct": round(chg_pct, 2),
            "prev_close": round(q.get("prev_close", 0), 2),
            "open": round(q.get("open", 0), 2),
            "high": round(q.get("high", 0), 2),
            "low": round(q.get("low", 0), 2),
            "volume": q.get("volume", 0),
            "amount": round(q.get("amount", 0), 0),
        },
        "range_52w": {
            "high": round(high52, 2),
            "low": round(low52, 2),
            "position": range_pos,
        },
        # 估值 —— 用户配了估值源(Tushare daily_basic 之类)才有,没配就是 {}。
        #
        # ⚠️ **必须原样透出去。**`get_quote()` 里已经带了 PE/PB,但这个函数
        # 重新组装返回体时把它们丢了 —— 于是模型问「茅台市盈率多少」时
        # 手上没有这个数,**它就自己编了一个**(实测答 PE 21.46 / PB 6.75,
        # 而 Tushare 是 19.61 / 6.43,腾讯是 19.54 / 6.33,两个都对不上)。
        #
        # 取数层拿到了数据、组装层把它丢掉,是最难查的一类:
        # 数据源测试是绿的,日志里也看不出什么,只有回答是错的。
        "valuation": {k: q[k] for k in
                      ("pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio",
                       "dv_ttm", "total_mv", "circ_mv", "turnover_rate",
                       "valuation_date")
                      if q.get(k) is not None},
        "ai_comment": ai_comment or "行情正常，暂无特别信号。",
        "in_watchlist": in_wl,
        "actions": [
            {"key": "deep_analysis", "label": "🔬 深度分析", "workflow": "debate",
             "hint": f"对 {q.get('name')} 做多空辩论 · 给出买卖决策"},
            {"key": "add_watchlist", "label": "＋ 加自选",
             "hint": f"加入自选后收进日报"} if not in_wl else
            {"key": "add_watchlist", "label": "✓ 已加自选", "disabled": True},
            {"key": "view_news", "label": "📰 查看新闻",
             "prefill": f"{code} 最近有什么关键新闻?"},
        ],
    }


@ToolRegistry.register("stock_quickview", definition=_QUICKVIEW_DEF, timeout=15)
async def _quickview_tool(tc: ToolCall, bus) -> ToolResult:
    t0 = time.time()
    code = tc.args.get("code")
    if not code:
        return ToolResult.error_of(tc, "BAD_ARGS", "缺少 code")
    user_id = tc.args.get("user_id")
    try:
        summary = await _quickview(str(code).strip(), user_id)
    except Exception as e:
        return ToolResult.error_of(
            tc, "INTERNAL", f"stock_quickview 失败: {type(e).__name__}: {e}",
            duration_ms=int((time.time() - t0) * 1000),
        )
    return ToolResult(
        tool_call=tc, status="ok",
        duration_ms=int((time.time() - t0) * 1000),
        summary=summary,
    )


# ═════════════════════════════════════════════════════════════════
# TOOL 2 · stock_news
# ═════════════════════════════════════════════════════════════════

_NEWS_DEF = {
    "name": "stock_news",
    "description": (
        "拉取指定股票近 30 天的关键新闻并附上每条对股价的可能影响。"
        "适合用户问『{股票} 最近有什么新闻 / 有啥公告 / 有什么利好利空』时使用。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10},
        },
        "required": ["code"],
    },
}

_NEWS_SYS = (
    "你是猎鹿人新闻影响分析师。给你一条股票新闻，用一句话（20-40 字）判断对该股股价的可能影响。"
    "影响必须分类到 [positive | negative | neutral | high_impact]。"
    "不给出投资建议，只做事件影响推理。"
    "严格 JSON 输出，不要 markdown 代码块：{\"impact\":\"...\",\"note\":\"...\"}"
)


def _classify_news(stock_name: str, code: str, title: str, content: str = "") -> dict:
    """单条新闻 · 走 LLM 打影响 tag + 短评。失败静默降级为 neutral。"""
    import json as _json
    client = get_client()
    if client is None:
        return {"impact": "neutral", "note": ""}
    prompt = (
        f"股票：{stock_name}（{code}）\n"
        f"新闻标题：{title}\n"
        f"简要内容：{(content or '')[:300]}"
    )
    try:
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _NEWS_SYS},
                {"role": "user", "content": prompt},
            ],
            max_tokens=120,
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        text = (resp.choices[0].message.content or "").strip()
        d = _json.loads(text)
        impact = d.get("impact", "neutral")
        if impact not in ("positive", "negative", "neutral", "high_impact"):
            impact = "neutral"
        return {"impact": impact, "note": (d.get("note") or "")[:120]}
    except Exception as e:
        logger.warning("[wl_agent] news 影响标注失败: {}", e)
        return {"impact": "neutral", "note": ""}


async def _news(code: str, limit: int = 5) -> dict:
    news = await asyncio.to_thread(fd.get_news, code, 20)
    if not news:
        return {"type": "stock_news", "code": code, "items": []}

    # 简单排序：优先近的 + 有摘要的
    def _score(n):
        has_body = bool(n.get("content") or n.get("summary"))
        return (n.get("date") or n.get("time") or "", has_body)
    news_sorted = sorted(news, key=_score, reverse=True)[:limit]

    # 拿股票名（用第一次调用 quote 兜底）· 也可从 stocks 表拿
    stock_name = code
    try:
        q = await asyncio.to_thread(fd.get_quote, code)
        if q:
            stock_name = q.get("name", code)
    except Exception:
        pass

    # 并发打影响 tag
    def _tag_one(n):
        title = n.get("title", "")
        content = n.get("content", "") or n.get("summary", "")
        tag = _classify_news(stock_name, code, title, content)
        return {
            "title": title,
            "source": n.get("source", ""),
            "date": (n.get("date") or n.get("time") or "")[:10],
            "url": n.get("url", ""),
            "impact": tag["impact"],
            "ai_note": tag["note"],
        }

    items = await asyncio.gather(*[asyncio.to_thread(_tag_one, n) for n in news_sorted])

    return {
        "type": "stock_news",
        "code": code,
        "name": stock_name,
        "items": items,
    }


@ToolRegistry.register("stock_news", definition=_NEWS_DEF, timeout=30)
async def _news_tool(tc: ToolCall, bus) -> ToolResult:
    t0 = time.time()
    code = tc.args.get("code")
    if not code:
        return ToolResult.error_of(tc, "BAD_ARGS", "缺少 code")
    limit = int(tc.args.get("limit", 5))
    limit = max(1, min(10, limit))
    try:
        summary = await _news(str(code).strip(), limit)
    except Exception as e:
        return ToolResult.error_of(
            tc, "INTERNAL", f"stock_news 失败: {type(e).__name__}: {e}",
            duration_ms=int((time.time() - t0) * 1000),
        )
    return ToolResult(
        tool_call=tc, status="ok",
        duration_ms=int((time.time() - t0) * 1000),
        summary=summary,
    )


# ═════════════════════════════════════════════════════════════════
# TOOL 3 · watchlist_digest
# ═════════════════════════════════════════════════════════════════

_DIGEST_DEF = {
    "name": "watchlist_digest",
    "description": (
        "拉取当前用户的自选清单 · 拿今日行情排序 · 生成 Top3 涨/跌 AI 归因。"
        "适合用户问『我的自选今天/最近谁最强 / 谁最弱 / 自选股日报』时使用。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "内部用 · orchestrator 从上下文注入"},
            "top_n": {"type": "integer", "default": 3, "minimum": 1, "maximum": 10},
        },
        "required": [],
    },
}

_ATTRIB_SYS = (
    "你是猎鹿人自选股日报归因助手。直接输出中文归因文案，20-30 字，示例："
    "「触发底部反转，主力净流入 8500 万」或「放量拉升，突破前高」。"
    "严禁输出以下内容：任何英文（含 'I understand' / 'Sure' / 'Here is' 等 role-play 词）·"
    "任何解释「我将...」/「Let me...」· 任何前缀（'归因：' 之类）· 任何换行 · 任何 markdown。"
    "只输出那 20-30 字归因文本本身，别的什么都不要。"
)


def _clean_attribution(text: str) -> str:
    """剥 Gemini 常见 prompt echo · 输出纯净归因文案。"""
    if not text:
        return ""
    t = text.strip()
    # 逐行找第一个"看起来像归因"的中文行
    for line in t.splitlines():
        line = line.strip()
        if not line:
            continue
        # 去常见 role-play 前缀
        for prefix in (
            "I understand", "Sure", "Here is", "Here's", "OK,", "Okay,",
            "好的，", "好的,", "明白，", "明白,",
            "归因：", "归因:", "点评：", "点评:", "短评：", "短评:",
            "我将", "让我", "Let me", "I will",
        ):
            if line.lower().startswith(prefix.lower()):
                line = ""
                break
        if not line:
            continue
        # 如果行里有 . / , 后面接的都是英文 · 认为是 role-play 段
        if any(c.isascii() and c.isalpha() for c in line[:20]):
            has_cn = any("一" <= c <= "鿿" for c in line)
            if not has_cn:
                continue
        return line[:80]
    return t.split("\n")[0][:80] if t else ""


def _attribute_stock(name: str, code: str, quote: dict) -> tuple[str, list[str]]:
    """给一只股票生成 20 字归因 + 信号 chip 列表。"""
    chg_pct = quote.get("change_pct", 0)
    amount = quote.get("amount", 0) / 1e8  # 亿
    signals = []

    # 简单规则识别信号 chip
    if abs(chg_pct) >= 8:
        signals.append("反转信号" if chg_pct > 0 else "急跌")
    elif abs(chg_pct) >= 3:
        signals.append("放量拉升" if chg_pct > 0 else "回调")
    if amount > 20:
        signals.append("大成交")
    if not signals:
        signals.append("波动" if abs(chg_pct) > 0 else "横盘")

    # 走 LLM 给归因文案 · Gemini 常泄漏 system prompt · 做二次剥离
    llm_input = (
        f"股票：{name}（{code}） · 涨跌 {chg_pct:+.2f}% · 今日成交 {amount:.1f} 亿"
    )
    raw = _llm_short(_ATTRIB_SYS, llm_input, 60)
    cleaned = _clean_attribution(raw)
    if not cleaned:
        cleaned = f"涨跌 {chg_pct:+.2f}% · 成交 {amount:.1f} 亿"
    return cleaned, signals


def _hk_quote_sync(code: str) -> dict | None:
    """港股快照 · yahoo_hk 数据源（延迟 15 分钟）。"""
    try:
        from app.services.gm import yahoo_hk
        return yahoo_hk.hk_quote(code)
    except Exception as e:
        logger.warning("[wl_agent] hk_quote failed for {}: {}", code, e)
        return None


def _us_quote_sync(code: str) -> dict | None:
    """美股快照 · findata_db。"""
    try:
        from app.services.gm import findata_db
        return findata_db.us_quote(code)
    except Exception as e:
        logger.warning("[wl_agent] us_quote failed for {}: {}", code, e)
        return None


async def _digest(user_id: str, top_n: int = 3) -> dict:
    if not user_id:
        return {"type": "watchlist_digest", "error": "需要登录后才能拉自选股日报"}
    # Bug A 修复（2026-08）· 用 get_all_stocks_by_user 覆盖 A/HK/US 全市场自选，
    # 原 get_stocks_by_user 硬过滤 HK/US 会漏掉港股/美股仓位。
    try:
        stocks = await asyncio.to_thread(get_all_stocks_by_user, user_id)
    except Exception as e:
        logger.warning("[wl_agent] 拉自选失败: {}", e)
        stocks = []

    if not stocks:
        return {
            "type": "watchlist_digest",
            "empty": True,
            "hint": "你还没有自选股。去『自选股』页面添加 3 只股票后，再来问我。",
        }

    # 按 market 分派 quote 数据源
    # · A 股：优先 Redis (collector 每 30s 写入 · 最新) · Redis 空 → fd.get_quote (HTTP · 可能陈旧)
    # · HK：gm.yahoo_hk.hk_quote（Yahoo · 延迟 15min）
    # · US：gm.findata_db.us_quote
    async def _q(s):
        code = s.get("code")
        market = (s.get("market") or "A").upper()
        try:
            if market == "HK":
                q = await asyncio.to_thread(_hk_quote_sync, code)
            elif market == "US":
                q = await asyncio.to_thread(_us_quote_sync, code)
            else:
                # A 股：优先 Redis · 拿不到再走 fd.get_quote
                q = await asyncio.to_thread(_get_a_quote_from_redis, code)
                src = "redis" if q else None
                if not q:
                    q = await asyncio.to_thread(fd.get_quote, code)
                    src = "fd.http" if q else None
                if q:
                    q = dict(q)
                    q["_src"] = src  # 标注数据源便于排查
            if q:
                q = dict(q)
                q["_name_local"] = s.get("name") or q.get("name")
                q["_market"] = market
                q["code"] = q.get("code") or code
                return q
        except Exception as e:
            logger.warning("[wl_agent] quote {} ({}): {}", code, market, e)
        return None

    quotes = await asyncio.gather(*[_q(s) for s in stocks])
    quotes = [q for q in quotes if q]
    if not quotes:
        return {"type": "watchlist_digest", "error": "拉自选股行情失败",
                "requested_count": len(stocks)}

    # Bug B 修复（2026-08）· 数据新鲜度检查 · 全 0 change_pct 时给 LLM 明确提示
    def _cp(q):  # 兼容 change_pct 缺失 / None
        v = q.get("change_pct")
        try:
            return float(v) if v is not None else 0.0
        except Exception:
            return 0.0

    total_cp_nonzero = sum(1 for q in quotes if abs(_cp(q)) > 0.01)

    # 采集时戳 · 找最老/最新（用 ts / updated_at / _name_local 之外的字段兜底）
    def _ts(q):
        return q.get("ts") or q.get("updated_at") or ""
    ts_all = sorted([_ts(q) for q in quotes if _ts(q)])
    ts_oldest = ts_all[0] if ts_all else None
    ts_newest = ts_all[-1] if ts_all else None

    # 排序
    sorted_up = sorted(quotes, key=lambda x: -_cp(x))[:top_n]
    sorted_dn = sorted(quotes, key=lambda x: _cp(x))[:top_n]

    up_count = sum(1 for q in quotes if _cp(q) > 0.01)
    dn_count = sum(1 for q in quotes if _cp(q) < -0.01)
    flat_count = len(quotes) - up_count - dn_count
    avg_pct = sum(_cp(q) for q in quotes) / len(quotes)

    def _mkt_suffix(q):
        m = q.get("_market") or q.get("market", "A")
        if m == "A":
            code = q.get("code", "")
            return "SH" if code.startswith(("6", "9")) else "SZ"
        return {"HK": "HK", "US": "US"}.get(m, "")

    def _card(q):
        attribution, signals = _attribute_stock(
            q.get("_name_local") or q.get("name"), q.get("code"), q
        )
        return {
            "code": q.get("code"),
            "name": q.get("_name_local") or q.get("name"),
            "market": q.get("_market") or q.get("market", "A"),
            "market_suffix": _mkt_suffix(q),
            "price": round(float(q.get("price", 0) or 0), 3),
            "change_pct": round(_cp(q), 2),
            "attribution": attribution,
            "signals": signals,
        }

    top_gainers = [_card(q) for q in sorted_up if _cp(q) > 0]
    top_losers = [_card(q) for q in sorted_dn if _cp(q) < 0]

    # Bug D 修复（2026-08）· 全部持仓列表 · 让 LLM 完整看到 7 只 · 无需脑补
    # 原返回体里 flat 股票只统计 count · 不给 code/name · LLM 拿到"有 5 只平盘"
    # 会从"A 股龙头"脑补出中芯/宁德/工业富联等幻觉股票组装答案。
    all_positions = [_card(q) for q in sorted(quotes, key=lambda x: -_cp(x))]

    # 数据陈旧 warning · 供 LLM 参考
    warnings: list[str] = []
    if len(quotes) < len(stocks):
        missing = len(stocks) - len(quotes)
        warnings.append(f"{missing} 只自选行情拉取失败 · 已从统计中剔除")
    if total_cp_nonzero == 0 and len(quotes) > 0:
        warnings.append(
            "全部自选 change_pct=0 · 数据源可能未更新今日行情 · "
            "以 top_gainers/top_losers 空为准 · 请勿从历史 tool 输出推测"
        )

    if top_gainers and top_losers:
        ai_summary = (
            f"你的自选整体 {avg_pct:+.2f}% · "
            f"最强 {top_gainers[0]['name']} {top_gainers[0]['change_pct']:+.2f}% · "
            f"最弱 {top_losers[0]['name']} {top_losers[0]['change_pct']:+.2f}%"
        )
    else:
        ai_summary = (
            f"你的 {len(quotes)} 只自选今日全部平盘（change_pct 均为 0）· "
            "行情数据可能未更新 · 无涨跌可排序"
        )

    return {
        "type": "watchlist_digest",
        "total_count": len(quotes),
        "requested_count": len(stocks),
        "up_count": up_count,
        "down_count": dn_count,
        "flat_count": flat_count,
        "avg_pct": round(avg_pct, 2),
        # ⭐ all_positions 完整列 · LLM 只能引用这里出现的股票 · 严禁脑补
        "all_positions": all_positions,
        "top_gainers": top_gainers,
        "top_losers": top_losers,
        "data_freshness": {
            "oldest": ts_oldest,
            "newest": ts_newest,
            "all_zero_change_pct": total_cp_nonzero == 0,
        },
        "warnings": warnings,
        "ai_summary": ai_summary,
    }


@ToolRegistry.register("watchlist_digest", definition=_DIGEST_DEF, timeout=45)
async def _digest_tool(tc: ToolCall, bus) -> ToolResult:
    t0 = time.time()
    user_id = tc.args.get("user_id") or ""
    top_n = int(tc.args.get("top_n", 3))
    top_n = max(1, min(10, top_n))
    try:
        summary = await _digest(str(user_id), top_n)
    except Exception as e:
        return ToolResult.error_of(
            tc, "INTERNAL", f"watchlist_digest 失败: {type(e).__name__}: {e}",
            duration_ms=int((time.time() - t0) * 1000),
        )
    return ToolResult(
        tool_call=tc, status="ok",
        duration_ms=int((time.time() - t0) * 1000),
        summary=summary,
    )
