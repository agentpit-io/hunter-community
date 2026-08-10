"""推送内容生成器：真实数据 → 飞书交互卡片。

三张定时卡片：
- watchlist_morning  08:00 CST  早报（自选股新闻 + 昨日收盘快照）
- fundflow_topn      14:30 CST  异动追踪（主力净流入 Top3 + 实时行情）
- close_review       15:15 CST  收盘总结（当日涨跌 + 资金 + 亮点）

价格预警：
- price_alert  豪迈科技偏离开盘价 ±PRICE_ALERT_THRESHOLD_PCT%
"""
import asyncio
import os
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from app.services.database import get_stocks, get_stocks_by_user
from app.services.finance_data_client import get_quote, get_reliable_close, get_money_flow, get_news, register_stocks

CST = timezone(timedelta(hours=8))


def _send_alert_email(subject: str, body: str) -> None:
    """发送运维告警邮件。需配置 ALERT_EMAIL_SMTP_USER / ALERT_EMAIL_SMTP_PASS。"""
    host = os.getenv("ALERT_EMAIL_SMTP_HOST", "smtp.qq.com")
    port = int(os.getenv("ALERT_EMAIL_SMTP_PORT", "587"))
    user = os.getenv("ALERT_EMAIL_SMTP_USER", "")
    pwd  = os.getenv("ALERT_EMAIL_SMTP_PASS", "")
    to   = os.getenv("ALERT_EMAIL_TO", "402493977@qq.com")
    if not user or not pwd:
        logger.warning("⚠️ 告警邮件跳过（ALERT_EMAIL_SMTP_USER/PASS 未配置）: {}", subject)
        return
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"]    = user
        msg["To"]      = to
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(user, pwd)
            smtp.sendmail(user, [to], msg.as_string())
        logger.info("✉️ 告警邮件已发送 to={} subject={}", to, subject)
    except Exception as e:
        logger.error("✉️ 告警邮件发送失败: {}", e)
WEB_BASE = "https://hunter.agentpit.io"

_STOCK_EMOJI = {
    "002595": "🔩", "000933": "⛏", "601899": "⛰",
    "002001": "🧪", "02333":  "🚗", "01378":  "🌉",
}


def _now_cst() -> datetime:
    return datetime.now(CST)


# ── 飞书卡片基础元素 ─────────────────────────────────────────────────────────

def _card_header(title: str, subtitle: str = "", color: str = "blue") -> Dict[str, Any]:
    h = {"title": {"tag": "plain_text", "content": title}, "template": color}
    if subtitle:
        h["subtitle"] = {"tag": "plain_text", "content": subtitle}
    return h


def _md(text: str) -> Dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": text}}


def _hr() -> Dict[str, Any]:
    return {"tag": "hr"}


def _action(text: str, url: str) -> Dict[str, Any]:
    return {
        "tag": "action",
        "actions": [{"tag": "button", "text": {"tag": "plain_text", "content": text},
                     "type": "primary", "url": url}],
    }


# ── 数据拉取（通过 finance_data_client → finance-data.agentpit.io）──────────

async def _all_quotes(user_id: str = "", kline_fallback: bool = False) -> List[Tuple[Dict, Optional[Dict]]]:
    today = _now_cst().strftime("%Y-%m-%d")
    stocks = get_stocks_by_user(user_id) if user_id else get_stocks()
    register_stocks(stocks)
    results = []
    stale_failed: List[str] = []  # 双路径均失败的股票，汇总后发一封告警邮件

    for s in stocks:
        q = await asyncio.to_thread(get_quote, s["code"])
        if q:
            ts = (q.get("ts") or "")[:10]
            if ts and ts != today:
                logger.warning(
                    "⚠️ 行情数据过期 {} ts={} expected={}{}",
                    s["code"], ts, today,
                    " — 尝试 kline 兜底" if kline_fallback else " — finance-data 尚未更新当日收盘价",
                )
                if kline_fallback:
                    try:
                        kq = await asyncio.to_thread(get_reliable_close, s["code"], today)
                    except Exception as exc:
                        logger.error("❌ kline 兜底异常 {} {}", s["code"], exc)
                        kq = None
                    if kq:
                        logger.info("✅ kline 兜底成功 {} price={} chg={:.2f}%",
                                    s["code"], kq["price"], kq["change_pct"])
                        q = kq
                    else:
                        logger.error("❌ kline 兜底失败 {} — 放弃旧数据，置空", s["code"])
                        stale_failed.append(f"{s['name']}({s['code']}) ts={ts}")
                        q = None  # 不使用旧数据
        elif kline_fallback:
            # quote 接口完全无数据，也走 kline 兜底
            try:
                kq = await asyncio.to_thread(get_reliable_close, s["code"], today)
            except Exception as exc:
                logger.error("❌ kline 兜底异常 {} {}", s["code"], exc)
                kq = None
            if kq:
                logger.info("✅ kline 兜底成功（无 quote） {} price={} chg={:.2f}%",
                            s["code"], kq["price"], kq["change_pct"])
                q = kq
            else:
                stale_failed.append(f"{s['name']}({s['code']}) quote=None kline=None")

        results.append((s, q))

    if stale_failed:
        now_str = _now_cst().strftime("%Y-%m-%d %H:%M:%S CST")
        _send_alert_email(
            f"[Hunter] 收盘数据异常 {today}",
            f"以下股票 quote 过期且 kline 兜底均失败，对应行情已置空（不展示旧数据）：\n\n"
            + "\n".join(f"  · {f}" for f in stale_failed)
            + f"\n\n时间：{now_str}"
            + "\n请检查 finance-data.agentpit.io 服务状态。",
        )

    return results


async def _all_flows(user_id: str = "") -> Dict[str, Optional[Dict]]:
    stocks = get_stocks_by_user(user_id) if user_id else get_stocks()
    flows = {}
    for s in stocks:
        flows[s["code"]] = await asyncio.to_thread(get_money_flow, s["code"])
    return flows


async def _stock_latest_news(code: str) -> Optional[Dict]:
    items = await asyncio.to_thread(get_news, code, 1)
    if isinstance(items, list) and items:
        return items[0]
    return None


# ── 行情行格式化 ──────────────────────────────────────────────────────────────

def _fmt_quote_line(s: Dict, q: Optional[Dict], flow: Optional[Dict] = None) -> str:
    emoji = _STOCK_EMOJI.get(s["code"], "📌")
    currency = "HK$" if s["market"] == "HK" else "¥"

    if not q or q.get("price") is None:
        return f"{emoji} **{s['name']}** ({s['code']}) — 暂无行情"

    price = q.get("price", 0)
    chg   = q.get("change_pct") or 0
    chg_a = q.get("change_amt") or 0
    dot   = "🔴" if chg >= 0 else "🟢"   # 中国股市：红涨绿跌
    c_col = "red" if chg >= 0 else "green"

    line = (
        f"{dot} **{s['name']}** ({s['code']}) "
        f"<font color='{c_col}'>{chg:+.2f}%</font>  "
        f"{currency}{price:.2f} ({chg_a:+.2f})"
    )
    if flow and flow.get("main_net") is not None:
        net = flow["main_net"] / 1e8
        fc  = "red" if net >= 0 else "green"
        verb = "净流入" if net >= 0 else "净流出"
        line += f"\n  <font color='{fc}'>{verb} {currency}{abs(net):.2f}亿</font>"
    return line


# ── 三张定时卡片 ──────────────────────────────────────────────────────────────

async def render_watchlist_morning(user_id: str = "") -> Dict[str, Any]:
    """08:00 早报：自选股相关新闻 + LLM 隔夜研报搜索 + 行情快照"""
    now = _now_cst()
    date_str = now.strftime("%m月%d日 %H:%M")
    date_full = now.strftime("%Y年%m月%d日")
    stocks = get_stocks_by_user(user_id) if user_id else get_stocks()
    register_stocks(stocks)
    stock_names = [s["name"] for s in stocks if s.get("name")]

    # 两路并行：各股最新新闻 + LLM 搜索隔夜重要资讯
    news_tasks = [_stock_latest_news(s["code"]) for s in stocks]

    async def _morning_llm_search():
        import os
        from openai import OpenAI
        api_key  = os.getenv("ONE_API_KEY", "")
        base_url = os.getenv("ONE_API_BASE_URL", "http://104.197.139.51:3000/v1")
        model    = os.getenv("ONE_API_MODEL", "gemini-3-flash-preview")
        if not api_key or not stock_names:
            return ""
        names_str = "、".join(stock_names[:8])
        prompt = (
            f"今天是{date_full}（注意：是2026年，不是2024年）。"
            f"请通过搜索工具查找以下股票的隔夜重要消息：{names_str}。\n"
            "【重要】只使用搜索到的2026年真实信息，不要使用训练数据中的历史内容，不要提及2024年。\n"
            "重点：昨晚至今晨的券商评级变化、研报观点、重大公告、行业政策、影响股价的关键信息。\n"
            "用3-5句话总结最值得开盘前关注的内容，点出具体股票和原因。"
        )
        try:
            client = OpenAI(api_key=api_key, base_url=base_url, timeout=30)
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                tools=[{"type": "google_search"}],
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            logger.warning("[watchlist_morning] llm search failed: {}", e)
            return ""

    news_results, ai_summary = await asyncio.gather(
        asyncio.gather(*news_tasks),
        _morning_llm_search(),
    )

    elements: List[Dict] = [
        _md(f"**📅 {date_str}** ｜ 隔夜消息 + 开盘快照"),
        _hr(),
    ]

    # AI 搜索：隔夜研报/机构观点
    if ai_summary and "暂无" not in ai_summary:
        elements.append(_md("**🤖 AI搜索 · 隔夜重要资讯**"))
        elements.append(_md(ai_summary))
        elements.append(_hr())

    # finance-data 各股最新新闻（带原文链接）
    elements.append(_md(f"**📰 自选股相关新闻（{len(stocks)}只）**"))
    for s, n in zip(stocks, news_results):
        emoji = _STOCK_EMOJI.get(s["code"], "📌")
        if n:
            title  = (n.get("title") or "").strip()[:50]
            url    = n.get("url") or ""
            source = n.get("source") or "东方财富"
            if url:
                elements.append(_md(
                    f"{emoji} **{s['name']}** ({s['code']}) — "
                    f"[{title}]({url})  <font color='grey'>{source}</font>"
                ))
            else:
                elements.append(_md(
                    f"{emoji} **{s['name']}** ({s['code']}) — "
                    f"{title}  <font color='grey'>{source}</font>"
                ))
        else:
            elements.append(_md(f"{emoji} **{s['name']}** ({s['code']}) — 暂无相关新闻"))

    elements.append(_hr())
    elements.append(_md("**📊 行情快照**"))
    for s, q in await _all_quotes(user_id):
        elements.append(_md(_fmt_quote_line(s, q)))

    elements.append(_hr())
    elements.append(_action("打开盯盘看板 →", f"{WEB_BASE}/"))

    return {
        "config": {"wide_screen_mode": True},
        "header": _card_header("🌅 自选股早报", f"{date_str} · 隔夜消息 + 开盘前快照", "turquoise"),
        "elements": elements,
    }


async def render_fundflow_topn(user_id: str = "") -> Dict[str, Any]:
    """14:30 异动追踪：主力净流入 Top3 + 盘中实时行情"""
    now = _now_cst()
    date_str = now.strftime("%m月%d日 %H:%M")

    flows = await _all_flows(user_id)
    pairs = await _all_quotes(user_id)

    # 按主力净流入排序
    ranked = sorted(
        [(s, q, flows.get(s["code"])) for s, q in pairs],
        key=lambda x: (x[2] or {}).get("main_net") or 0,
        reverse=True,
    )

    elements: List[Dict] = [
        _md(f"**📅 {date_str}** ｜ 盘中实时数据快照"),
        _hr(),
        _md("**🚀 主力净流入 Top3（盘中实时）**"),
    ]

    for i, (s, q, f) in enumerate(ranked[:3], 1):
        net = (f.get("main_net") or 0) / 1e8 if f else 0
        currency = "HK$" if s["market"] == "HK" else "¥"
        chg  = (q.get("change_pct") or 0) if q else 0
        col  = "red" if chg >= 0 else "green"
        sign = "+" if net >= 0 else ""
        elements.append(_md(
            f"**{i}.** **{s['name']}** ({s['code']})  "
            f"<font color='{col}'>{currency}{abs(net):.2f}亿</font>"
        ))

    elements.append(_hr())
    elements.append(_md("**📈 盘中实时行情**"))
    for s, q in pairs:
        elements.append(_md(_fmt_quote_line(s, q)))

    elements.append(_hr())
    elements.append(_action("查看分时与盘口 →", f"{WEB_BASE}/"))

    return {
        "config": {"wide_screen_mode": True},
        "header": _card_header("⚡ 自选股异动追踪", f"{date_str} · 实时数据快照", "orange"),
        "elements": elements,
    }


async def render_close_review(user_id: str = "") -> Dict[str, Any]:
    """15:15 收盘总结：当日涨跌一览 + 资金 + 今日亮点"""
    now = _now_cst()
    date_str = now.strftime("%m月%d日 %H:%M")

    flows = await _all_flows(user_id)
    pairs = await _all_quotes(user_id, kline_fallback=True)

    data = [
        (s, q, flows.get(s["code"]),
         (q.get("change_pct") or 0) if q else 0,
         (flows.get(s["code"]) or {}).get("main_net") or 0)
        for s, q in pairs
    ]

    up   = sum(1 for *_, chg, _ in data if chg > 0)
    down = sum(1 for *_, chg, _ in data if chg < 0)

    elements: List[Dict] = [
        _md(
            f"**📅 {date_str}** ｜ {len(data)}只股票 "
            f"**<font color='red'>{up}涨</font>** "
            f"**<font color='green'>{down}跌</font>**"
        ),
        _hr(),
        _md("**📉 当日涨跌一览**"),
    ]

    for s, q, f, chg, net in data:
        elements.append(_md(_fmt_quote_line(s, q, f)))

    # 今日亮点
    by_chg  = sorted(data, key=lambda x: x[3], reverse=True)
    by_flow = sorted(data, key=lambda x: x[4], reverse=True)
    highlights = []
    if by_chg[0][3] > 0:
        s, _, _, chg, _ = by_chg[0]
        highlights.append(f"涨幅最大：**{s['name']}** {chg:+.2f}%")
    if by_chg[-1][3] < 0:
        s, _, _, chg, _ = by_chg[-1]
        highlights.append(f"跌幅最大：**{s['name']}** {chg:+.2f}%")
    if by_flow[0][4] > 0:
        s = by_flow[0][0]
        net = by_flow[0][4]
        cur = "HK$" if s["market"] == "HK" else "¥"
        highlights.append(f"主力净流入最多：**{s['name']}** {cur}{net/1e8:.2f}亿")

    if highlights:
        elements.append(_hr())
        elements.append(_md("**🏆 今日亮点**"))
        for h in highlights:
            elements.append(_md(f"- {h}"))

    elements.append(_hr())
    elements.append(_action("查看复盘 →", f"{WEB_BASE}/"))

    return {
        "config": {"wide_screen_mode": True},
        "header": _card_header(
            "📊 自选股收盘总结",
            f"{date_str} · {up}涨{down}跌",
            "carmine",
        ),
        "elements": elements,
    }


def render_position_sentinel(
    code: str, name: str,
    change_pct: float,
    attribution: Dict[str, Any],
    market_context: Dict[str, Any] | None = None,
    thesis_text: str = "",
    shares: int | None = None,
    cost_price: float | None = None,
) -> Dict[str, Any]:
    """持仓哨兵三色推送卡片（INTACT / WEAKENING / BROKEN）。

    attribution 来自 attribution.analyze()：
      thesis_status / confidence / reasoning / user_message / evidence
    """
    status = attribution.get("thesis_status", "INTACT")
    msg    = attribution.get("user_message", "")
    reason = attribution.get("reasoning", "")
    conf   = attribution.get("confidence", 0)
    evidence = attribution.get("evidence", []) or []

    # 三色配色 + 情绪 emoji
    if status == "BROKEN":
        color, emoji, label = "red",      "🔴", "买入逻辑被破坏，建议复盘"
    elif status == "WEAKENING":
        color, emoji, label = "yellow",   "🟡", "部分逻辑受挑战，建议关注"
    else:  # INTACT 或 兜底
        color, emoji, label = "orange",   "🟠", "买入逻辑未受影响"

    direction = "下跌" if change_pct < 0 else "上涨"
    now = _now_cst()
    date_str = now.strftime("%m月%d日 %H:%M")

    # 持仓信息行（如填）
    pos_line = ""
    if shares is not None:
        pos_line = f"\n**持仓** {shares} 股"
        if cost_price and cost_price > 0:
            pl_per_share = (1 + change_pct/100) * cost_price - cost_price
            pl_total = pl_per_share * shares
            pl_color = "red" if pl_total > 0 else "green"
            pos_line += f"  ·  当日盈亏 <font color='{pl_color}'>¥{pl_total:+,.0f}</font>"

    # 大盘指数行
    idx_line = ""
    if market_context and market_context.get("indices"):
        parts = []
        for n, pct in market_context["indices"].items():
            c = "red" if pct > 0 else "green"
            parts.append(f"{n} <font color='{c}'>{pct:+.2f}%</font>")
        idx_line = "**大盘** " + "  ·  ".join(parts)

    # 板块同步性
    sector_line = ""
    if market_context and market_context.get("sector_stats"):
        s = market_context["sector_stats"]
        sc = "red" if s["change_pct"] > 0 else "green"
        sector_line = (
            f"**板块**「{s['sector_name']}」"
            f"<font color='{sc}'>{s['change_pct']:+.2f}%</font>  "
            f"·  下跌 {s['down_count']}/{s['total']} 家"
        )

    # 证据条目
    evidence_block = ""
    if evidence:
        ev_lines = []
        for e in evidence[:4]:
            ev_lines.append(f"  ◾ {e}")
        evidence_block = "**📋 归因证据**\n" + "\n".join(ev_lines)

    # thesis 摘要（限 100 字）
    thesis_brief = (thesis_text or "").strip()
    if len(thesis_brief) > 100:
        thesis_brief = thesis_brief[:100] + "..."

    elements = [
        _md(
            f"**{name}**（{code}）今日**{direction} {change_pct:+.2f}%**"
            + pos_line
        ),
    ]
    if idx_line or sector_line:
        elements.append(_hr())
        if idx_line:   elements.append(_md(idx_line))
        if sector_line: elements.append(_md(sector_line))

    elements += [
        _hr(),
        _md(f"**🎯 你的买入逻辑**\n{thesis_brief or '（未填写）'}"),
    ]
    if evidence_block:
        elements.append(_md(evidence_block))
    elements += [
        _hr(),
        _md(
            f"**{emoji} 哨兵结论**  ·  置信度 {int(conf * 100)}%\n"
            f"<font color='{'red' if status=='BROKEN' else 'orange' if status=='INTACT' else 'yellow'}'>"
            f"{label}</font>\n\n"
            f"{msg or reason}"
        ),
        _hr(),
        _action("查看行情 →", f"{WEB_BASE}/stock/{code}"),
    ]

    return {
        "config": {"wide_screen_mode": True},
        "header": _card_header(
            f"{emoji} 持仓哨兵 · {name}",
            f"{date_str}  ·  {direction} {change_pct:+.2f}%",
            color,
        ),
        "elements": elements,
    }


def render_price_alert(
    code: str, name: str,
    current: float, open_price: float,
    change_pct: float, threshold: float,
) -> Dict[str, Any]:
    """豪迈科技偏离开盘价预警卡片"""
    now = _now_cst()
    date_str = now.strftime("%m月%d日 %H:%M")
    direction = "上涨" if change_pct > 0 else "下跌"
    color = "red" if change_pct > 0 else "green"

    return {
        "config": {"wide_screen_mode": True},
        "header": _card_header(
            f"🚨 {name} 价格异动预警",
            f"{date_str} · 偏离开盘价 {change_pct:+.2f}%",
            "red",
        ),
        "elements": [
            _md(
                f"**{name}** ({code}) 盘中**{direction}**，已超过预设阈值"
            ),
            _hr(),
            _md(
                f"**当前价**  ¥{current:.2f}\n"
                f"**开盘价**  ¥{open_price:.2f}\n"
                f"**偏离幅度**  <font color='{color}'>{change_pct:+.2f}%</font>"
                f"（阈值 ±{threshold:.1f}%）"
            ),
            _hr(),
            _action("查看行情详情 →", f"{WEB_BASE}/stock/{code}"),
        ],
    }


# ── render_card 分发 ─────────────────────────────────────────────────────────

def render_custom(text: str, title: str = "📌 自定义推送") -> Dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True},
        "header": _card_header(title, _now_cst().strftime("%Y-%m-%d %H:%M"), "grey"),
        "elements": [
            _md(text or "_（推送内容为空）_"),
            _hr(),
            _action("打开 Hermes →", WEB_BASE),
        ],
    }



async def _llm_news_search(stock_names: list[str], date_str: str) -> str:
    """用 LLM + google_search 搜券商研报和分析师观点，返回摘要文本。"""
    import time
    from openai import OpenAI
    api_key  = os.getenv("ONE_API_KEY", "")
    base_url = os.getenv("ONE_API_BASE_URL", "http://104.197.139.51:3000/v1")
    model    = os.getenv("ONE_API_MODEL", "gemini-3-flash-preview")
    if not api_key or not stock_names:
        return ""
    names_str = "、".join(stock_names[:8])
    prompt = (
        f"今天是{date_str}（注意：是2026年，不是2024年）。"
        f"请通过搜索工具查找以下A股/港股在{date_str}前后的最新重要资讯：{names_str}。\n"
        "【重要】只使用搜索到的2026年真实信息，不要使用训练数据中的历史内容，不要提及2024年。\n"
        "重点寻找：券商研报观点、分析师评级变化、机构调研、重大利好利空、涨跌原因分析。\n"
        "用3-5句话总结最值得关注的内容，点出具体股票名称和关键原因，语言简洁。\n"
        "如搜索无结果则回复：今日暂无重大研报或机构观点。"
    )
    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=30)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            tools=[{"type": "google_search"}],
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning("[news_today] llm search failed: {}", e)
        return ""


async def render_news_today(user_id: str = "") -> Dict[str, Any]:
    """每日新闻速递：finance-data 官方新闻 + LLM google_search 券商研报整合。"""
    from app.services.finance_data_client import get_all_news_bulk
    now = _now_cst()
    date_str = now.strftime("%Y年%m月%d日")
    date_disp = now.strftime("%m月%d日 %H:%M")
    stocks = get_stocks_by_user(user_id) if user_id else get_stocks()
    register_stocks(stocks)
    stock_names = [s["name"] for s in stocks if s.get("name")]

    # 两路并行：官方新闻 + LLM 搜索
    all_news_task = asyncio.to_thread(get_all_news_bulk, limit=30)
    llm_task      = _llm_news_search(stock_names, date_str)
    all_news_raw, ai_summary = await asyncio.gather(all_news_task, llm_task)

    all_news = [n for n in all_news_raw if n.get("title")][:15]

    elements: List[Dict] = [
        _md(f"**📅 {date_disp}** ｜ 市场热点速览"),
        _hr(),
    ]

    # AI 搜索补充（券商研报 / 机构观点）
    if ai_summary and "暂无" not in ai_summary:
        elements.append(_md("**🤖 AI搜索 · 券商研报/机构观点**"))
        elements.append(_md(ai_summary))
        elements.append(_hr())

    # finance-data 官方新闻（带原文链接）
    elements.append(_md(f"**📰 今日财经新闻（共{len(all_news)}条）**"))
    for n in all_news:
        title  = (n.get("title") or "").strip()[:60]
        url    = n.get("url") or ""
        source = n.get("source") or "财经媒体"
        if url:
            elements.append(_md(f"▸ [{title}]({url})  <font color='grey'>{source}</font>"))
        else:
            elements.append(_md(f"▸ {title}  <font color='grey'>{source}</font>"))
    if not all_news:
        elements.append(_md("_暂无最新新闻，请稍后查看_"))

    elements.append(_hr())
    elements.append(_md("**📊 自选股行情**"))
    for s, q in await _all_quotes(user_id, kline_fallback=True):
        elements.append(_md(_fmt_quote_line(s, q)))

    elements.append(_hr())
    elements.append(_action("打开完整行情 →", f"{WEB_BASE}/"))

    return {
        "config": {"wide_screen_mode": True},
        "header": _card_header("📰 每日新闻速递", f"{date_disp} · 市场热点速览", "blue"),
        "elements": elements,
    }

async def render_weekly_summary(user_id: str = "") -> Dict[str, Any]:
    """周五 18:00 周度持仓汇总：本周每只股票的涨跌幅 + 亮点。"""
    from app.services.finance_data_client import get_kline
    now = _now_cst()
    date_str = now.strftime("%m月%d日 %H:%M")

    stocks = get_stocks_by_user(user_id) if user_id else get_stocks()
    register_stocks(stocks)

    # 取最近 6 个交易日 K 线，用首条 close 作周起始价
    rows_map: Dict[str, list] = {}
    for s in stocks:
        rows = await asyncio.to_thread(get_kline, s["code"], "daily", 6)
        rows_map[s["code"]] = rows

    # 计算每只股票的周涨跌幅
    data = []
    for s in stocks:
        rows = rows_map.get(s["code"]) or []
        if len(rows) < 2:
            data.append((s, None, 0.0))
            continue
        week_start = rows[0]["close"]
        week_end   = rows[-1]["close"]
        if not week_start:
            data.append((s, None, 0.0))
            continue
        week_chg = (week_end - week_start) / week_start * 100
        data.append((s, week_end, week_chg))

    up   = sum(1 for _, _, chg in data if chg > 0)
    down = sum(1 for _, _, chg in data if chg < 0)

    elements: List[Dict] = [
        _md(
            f"**📅 {date_str}** ｜ {len(data)}只股票 "
            f"**<font color='red'>{up}涨</font>** "
            f"**<font color='green'>{down}跌</font>**"
        ),
        _hr(),
        _md("**📈 本周涨跌一览**"),
    ]

    for s, price, chg in data:
        emoji = _STOCK_EMOJI.get(s["code"], "📌")
        currency = "HK$" if s["market"] == "HK" else "¥"
        if price is None:
            elements.append(_md(f"{emoji} **{s['name']}** ({s['code']}) — 暂无数据"))
            continue
        col  = "red" if chg >= 0 else "green"
        dot  = "🔴" if chg >= 0 else "🟢"
        elements.append(_md(
            f"{dot} **{s['name']}** ({s['code']}) "
            f"<font color='{col}'>{chg:+.2f}%</font>  {currency}{price:.2f}"
        ))

    # 本周亮点
    valid = [(s, chg) for s, _, chg in data if _ is not None]
    if valid:
        best  = max(valid, key=lambda x: x[1])
        worst = min(valid, key=lambda x: x[1])
        elements.append(_hr())
        elements.append(_md("**🏆 本周亮点**"))
        if best[1] > 0:
            elements.append(_md(f"- 涨幅最大：**{best[0]['name']}** {best[1]:+.2f}%"))
        if worst[1] < 0:
            elements.append(_md(f"- 跌幅最大：**{worst[0]['name']}** {worst[1]:+.2f}%"))

    elements.append(_hr())
    elements.append(_action("查看完整行情 →", f"{WEB_BASE}/"))

    return {
        "config": {"wide_screen_mode": True},
        "header": _card_header(
            "📅 周度持仓汇总",
            f"{date_str} · 本周持仓表现",
            "indigo",
        ),
        "elements": elements,
    }


async def render_card(content_type: str, custom_content: str = "", title: str = "", user_id: str = "") -> Dict[str, Any]:
    if content_type == "watchlist_morning":
        return await render_watchlist_morning(user_id)
    if content_type == "fundflow_topn":
        return await render_fundflow_topn(user_id)
    if content_type == "close_review":
        return await render_close_review(user_id)
    if content_type == "news_today":
        return await render_news_today(user_id)
    if content_type == "weekly_summary":
        return await render_weekly_summary(user_id)
    if content_type == "custom":
        return render_custom(custom_content, title or "📌 自定义推送")
    return render_custom(f"未知 content_type: {content_type}", "⚠️ 配置错误")
