"""定时推送调度器 + 持仓哨兵循环。

定时推送：每 60s 扫一次 push_tasks（北京时间 HH:MM 比较）。
持仓哨兵：每 5 分钟检查所有"有 thesis 卡片的"自选股，超阈值时调归因引擎 + 三色推送。
"""
import asyncio
import os
import re as _re
import sys
from datetime import datetime, timezone, timedelta, date
from typing import Optional

import httpx

# 将 hermes 根目录加入 sys.path，使 agents/ 包可被 import
_HERMES_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if _HERMES_ROOT not in sys.path:
    sys.path.insert(0, _HERMES_ROOT)

from loguru import logger
from app.services import wx_push

_FINANCE_DATA_URL   = os.getenv("FINANCE_DATA_URL",   "https://finance-data.agentpit.io")
_FINANCE_DATA_TOKEN = os.getenv("FINANCE_DATA_TOKEN", "FinAPI@2026!")
_FD_HEADERS = {"X-Finance-Token": _FINANCE_DATA_TOKEN}


async def _broadcast_and_log(push_type: str, content: str, stocks: list, task_name: str = "", user_id: str = "", detail_url: str = "") -> None:
    today = __import__("datetime").datetime.now(__import__("datetime").timezone(__import__("datetime").timedelta(hours=8))).strftime("%Y-%m-%d")
    written = 0
    status = "ok"
    error_msg = ""
    try:
        async with __import__("httpx").AsyncClient(timeout=10, verify=False) as c:
            r = await c.post(
                f"{_FINANCE_DATA_URL}/api/v1/internal/wechat/push-broadcast",
                headers=_FD_HEADERS,
                json={"push_date": today, "push_type": push_type, "stocks": stocks, "content": content,
                      "user_id": user_id},
            )
            r.raise_for_status()
            try:
                data = r.json()
            except Exception as je:
                raise ValueError(f"push-broadcast 返回非 JSON (HTTP {r.status_code}): {r.text[:200]}") from je
            written = data.get("written", 0)
            logger.info("wechat broadcast push_type={} user_id={} written={}", push_type, user_id, written)
    except Exception as e:
        logger.warning("wechat broadcast failed: {}", e)
        status = "error"
        error_msg = str(e)
    # 先写 push-log 拿到 log_id，再发一次微信（避免重复推送）
    log_id = ""
    try:
        async with __import__("httpx").AsyncClient(timeout=10, verify=False) as c:
            lr = await c.post(
                f"{_FINANCE_DATA_URL}/api/v1/internal/wechat/push-log",
                headers=_FD_HEADERS,
                json={
                    "task_name": task_name or push_type,
                    "push_type": push_type,
                    "push_date": today,
                    "status": status,
                    "written_count": 0,
                    "error_msg": error_msg,
                    "content": content,
                    "user_id": user_id,
                },
            )
            lr.raise_for_status()
            log_id = str(lr.json().get("id", ""))
    except Exception as e:
        logger.warning("write_push_log failed (non-fatal): {}", e)

    # 微信模板消息：只发一次，带正确的 log_id 详情链接（信号报告传入 detail_url 直接跳 HTML）
    wx_sent = await wx_push.broadcast(task_name or push_type, content, log_id, user_id=user_id, push_type=push_type, detail_url=detail_url)

from app.services import attribution
from app.services.database import (
    get_conn, get_stocks, list_stocks_with_thesis,
    list_all_enabled_price_alerts, update_alert_triggered,
    get_thesis_for_stock, get_latest_kill_conditions_for_stock,
)
from app.services.feishu import send_card
from app.services.finance_data_client import get_quote, get_news
from app.services.push_content import render_card, render_price_alert, render_position_sentinel

CST = timezone(timedelta(hours=8))

_HERMES_ENV = os.path.expanduser("~/.hermes/.env")


def _get_default_chat() -> str:
    val = os.environ.get("FEISHU_HOME_CHANNEL", "").strip()
    if val:
        return val
    try:
        for line in open(_HERMES_ENV).read().splitlines():
            line = line.strip()
            if line.startswith("FEISHU_HOME_CHANNEL="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""

# 持仓哨兵：多阈值（绝对值），按从轻到重排序：
#   >= 3% 黄色（WEAKENING 提示）
#   >= 5% 橙色（INTACT 联动判定）
#   >= 6% 红色（重要异动，强制走归因）
# 旧 PRICE_ALERT_THRESHOLD_PCT 兼容用最低阈值。
_SENTINEL_YELLOW = float(os.getenv("SENTINEL_YELLOW_PCT", "3.0"))
_SENTINEL_ORANGE = float(os.getenv("SENTINEL_ORANGE_PCT", "5.0"))
_SENTINEL_RED    = float(os.getenv("SENTINEL_RED_PCT",    "6.0"))

# 老版兼容：豪迈科技偏离开盘价（与新哨兵互不影响，可逐步淘汰）
_LEGACY_ALERT_CODE      = "002595"
_LEGACY_ALERT_THRESHOLD = float(os.getenv("PRICE_ALERT_THRESHOLD_PCT", "2.0"))

_SCHEDULER_TASK: Optional[asyncio.Task] = None
_ALERT_TASK:     Optional[asyncio.Task] = None
_STOP = asyncio.Event()

# 已发送的预警 key 集合（格式 "YYYY-MM-DD_code_up/down"）
_alerts_sent: set[str] = set()


def _now_cst() -> datetime:
    return datetime.now(CST)


# ── 定时推送 ─────────────────────────────────────────────────────────────────

def _list_due_tasks() -> list:
    now   = _now_cst()
    today = now.date()
    hhmm  = now.strftime("%H:%M")
    rows  = []
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute(
            """
            SELECT id, name, template_id, schedule_time, content_type, custom_content,
                   target_chat, last_run_date, user_id
            FROM push_tasks
            WHERE enabled = TRUE
              AND schedule_time <= %s
              AND (last_run_date IS NULL OR last_run_date < %s)
            ORDER BY schedule_time ASC
            """,
            (hhmm, today),
        )
        for r in cur.fetchall():
            rows.append({
                "id": r[0], "name": r[1], "template_id": r[2],
                "schedule_time": r[3], "content_type": r[4],
                "custom_content": r[5] or "", "target_chat": r[6] or _get_default_chat(),
                "last_run_date": r[7], "user_id": r[8] or "",
            })
        conn.close()
    except Exception as e:
        logger.error("扫描推送任务失败: {}", e)
    return rows


def _mark_done(task_id: int, success: bool, message: str = "") -> None:
    today_cst = datetime.now(CST).date()
    try:
        conn = get_conn()
        cur  = conn.cursor()
        if success:
            cur.execute(
                "UPDATE push_tasks SET last_run_date=%s, last_status='ok', last_message=%s WHERE id=%s",
                (today_cst, message[:200], task_id),
            )
        else:
            # 失败也记录 last_run_date，防止每分钟无限重试
            cur.execute(
                "UPDATE push_tasks SET last_run_date=%s, last_status='fail', last_message=%s WHERE id=%s",
                (today_cst, message[:200], task_id),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("更新任务状态失败: {}", e)


def _card_to_text(card: dict) -> str:
    """从飞书卡片提取纯文本摘要（用于写入 WeChat 队列）。"""
    import re
    lines = []
    header = card.get("header", {})
    if header.get("title", {}).get("content"):
        lines.append(header["title"]["content"])
    for el in card.get("elements", []):
        tag = el.get("tag")
        if tag == "div":
            content = el.get("text", {}).get("content", "")
            # 去掉飞书 markdown 标签
            content = re.sub(r"<[^>]+>", "", content)
            content = re.sub(r"\*\*(.+?)\*\*", r"\1", content)
            content = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", content)
            content = content.strip()
            if content:
                lines.append(content)
        elif tag == "hr":
            lines.append("---")
    return "\n".join(lines)


async def _run_one(task: dict) -> dict:
    user_id = task.get("user_id", "")
    content_type = task.get("content_type", "daily_summary")

    # 渲染卡片（飞书格式），传入 user_id 确保用该用户自己的自选股
    try:
        card = await render_card(task["content_type"], task.get("custom_content", ""), task.get("name", ""), user_id)
    except Exception as e:
        logger.exception("render_card 失败 task_id={}", task["id"])
        return {"ok": False, "error": str(e)}

    # 广播写 WeChat 队列 + 推送日志（不依赖飞书，渲染完即写）
    wechat_text = _card_to_text(card) or task.get("name", "定时推送")
    asyncio.create_task(_broadcast_and_log(
        push_type=content_type,
        content=wechat_text,
        stocks=[],
        task_name=task.get("name", "定时推送"),
        user_id=task.get("user_id", ""),
    ))

    # 飞书推送（有配置才推，无配置视为成功跳过）
    target = task.get("target_chat") or ""
    if not target and user_id:
        from app.services.database import get_feishu_config
        cfg = get_feishu_config(user_id)
        target = cfg["home_channel"] if cfg else ""
    if not target:
        target = _get_default_chat()
    if not target:
        return {"ok": True, "note": "wechat written, feishu skipped (no target_chat)"}
    try:
        return await send_card(target, card, user_id=user_id)
    except Exception as e:
        logger.exception("推送任务执行异常 task_id={}", task["id"])
        return {"ok": False, "error": str(e)}


async def trigger_task_now(task_id: int) -> dict:
    """立即跑一次（测试用），不更新 last_run_date。"""
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute(
            "SELECT id, name, template_id, schedule_time, content_type, custom_content, target_chat, user_id "
            "FROM push_tasks WHERE id = %s",
            (task_id,),
        )
        row = cur.fetchone()
        conn.close()
    except Exception as e:
        return {"ok": False, "error": f"db: {e}"}
    if not row:
        return {"ok": False, "error": "task_not_found"}
    task = {
        "id": row[0], "name": row[1], "template_id": row[2],
        "schedule_time": row[3], "content_type": row[4],
        "custom_content": row[5] or "", "target_chat": row[6] or _get_default_chat(),
        "user_id": row[7] or "",
    }
    result = await _run_one(task)
    _mark_done(task_id, result.get("ok", False), result.get("error") or "manual_test")
    return result


async def _scheduler_loop() -> None:
    logger.info("push_scheduler started (CST timezone)")
    while not _STOP.is_set():
        try:
            tasks = _list_due_tasks()
            for t in tasks:
                logger.info("触发推送 #{} {} -> {}", t["id"], t["name"], t["target_chat"])
                result = await _run_one(t)
                _mark_done(t["id"], result.get("ok", False), result.get("error") or "ok")
                logger.info("推送结果 #{}: {}", t["id"], result)
        except Exception as e:
            logger.error("scheduler tick error: {}", e)
        try:
            await asyncio.wait_for(_STOP.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass
    logger.info("push_scheduler stopped")


# ── 价格预警 ─────────────────────────────────────────────────────────────────

async def _run_sentinel_for(item: dict, today_str: str) -> None:
    """对单只有 thesis 的持仓股跑一次哨兵检查 + 归因 + 推送。"""
    code   = item["code"]
    name   = item.get("name") or code

    try:
        q = await asyncio.to_thread(get_quote, code)
    except Exception as e:
        logger.warning("sentinel get_quote({}) failed: {}", code, e)
        return
    if not q or q.get("price") is None:
        return

    # 哨兵阈值用"基于昨收的当日涨跌幅"(change_pct)，更符合用户直觉
    change_pct = q.get("change_pct")
    if change_pct is None:
        # 兜底：用今开算
        open_price = q.get("open") or 0
        price = q.get("price") or 0
        if not open_price or open_price <= 0:
            return
        change_pct = (price - open_price) / open_price * 100

    abs_pct = abs(float(change_pct))
    if abs_pct < _SENTINEL_YELLOW:
        return  # 没到最低档，不推

    # 同股每天每方向最多 1 次（冷却防刷屏）
    direction = "up" if change_pct > 0 else "down"
    alert_key = f"{today_str}_{code}_{direction}"
    if alert_key in _alerts_sent:
        return

    target = _get_default_chat()
    if not target:
        logger.warning("持仓哨兵：FEISHU_HOME_CHANNEL 未配置，{}({})跳过推送", name, code)
        return

    # 拉新闻 + 市场上下文（akshare）
    try:
        news_items = await asyncio.to_thread(get_news, code, 10)
        news_titles = [n.get("title", "") for n in (news_items or []) if n.get("title")]
    except Exception as e:
        logger.warning("sentinel get_news({}) failed: {}", code, e)
        news_titles = []
    try:
        market_context = await asyncio.to_thread(attribution.fetch_market_context, code)
    except Exception as e:
        logger.warning("sentinel fetch_market_context({}) failed: {}", code, e)
        market_context = {"indices": {}, "industry": None, "sector_stats": None, "_errors": [str(e)]}

    # 调归因引擎
    attr = await asyncio.to_thread(
        attribution.analyze,
        code, name, float(change_pct),
        item.get("thesis_text", ""),
        news_titles,
        market_context,
        item.get("shares"),
        item.get("cost_price"),
    )

    # 推送
    card = render_position_sentinel(
        code=code, name=name, change_pct=float(change_pct),
        attribution=attr,
        market_context=market_context,
        thesis_text=item.get("thesis_text", ""),
        shares=item.get("shares"),
        cost_price=item.get("cost_price"),
    )
    result = await send_card(target, card)
    _alerts_sent.add(alert_key)
    logger.info(
        "持仓哨兵 {} ({}) {:+.2f}% status={} conf={}: {}",
        name, code, change_pct, attr.get("thesis_status"), attr.get("confidence"), result,
    )


async def _check_price_alert() -> None:
    """主入口：遍历所有有 thesis 的持仓股，跑哨兵；保留豪迈科技老版预警作为兼容。"""
    now   = _now_cst()
    hhmm  = now.strftime("%H:%M")

    # 只在交易时段检查（A股 09:30-15:00）
    if hhmm < "09:30" or hhmm > "15:00":
        return

    today_str = now.strftime("%Y-%m-%d")

    # 1. 新版持仓哨兵：遍历有 thesis 的股
    try:
        items = await asyncio.to_thread(list_stocks_with_thesis)
        for item in items:
            if not item.get("thesis_text"):
                continue  # 没填 thesis 跳过（不启用哨兵）
            try:
                await _run_sentinel_for(item, today_str)
            except Exception as e:
                logger.exception("sentinel {} crashed: {}", item.get("code"), e)
    except Exception as e:
        logger.exception("sentinel loop failed: {}", e)


async def _check_user_price_alerts(skip_time_check: bool = False) -> None:
    """遍历所有用户设置的价格提醒，满足条件且冷却过期就推飞书并写 push_logs。"""
    now = _now_cst()
    hhmm = now.strftime("%H:%M")
    if not skip_time_check and (hhmm < "09:25" or hhmm > "15:05"):
        return

    try:
        alerts = await asyncio.to_thread(list_all_enabled_price_alerts)
    except Exception as e:
        logger.error("list_all_enabled_price_alerts failed: {}", e)
        return

    # 按 code 合并 quote 请求
    codes = list({a["code"] for a in alerts})
    quotes: dict = {}
    for code in codes:
        try:
            q = await asyncio.to_thread(get_quote, code)
            if q:
                quotes[code] = q
        except Exception:
            pass

    from app.services.database import get_feishu_config
    from datetime import datetime, timezone

    for alert in alerts:
        code = alert["code"]
        q = quotes.get(code)
        if not q or q.get("price") is None:
            continue

        price = float(q["price"])
        prev_close = float(q.get("prev_close") or price)
        change_pct = float(q.get("change_pct") or 0)
        high = float(q.get("high") or price)
        low = float(q.get("low") or price)
        volatility = (high - low) / prev_close * 100 if prev_close else 0

        ct = alert["condition_type"]
        thr = alert["threshold"]
        thr2 = alert["threshold2"]

        triggered = False
        desc = ""
        if ct == "price_below" and price <= thr:
            triggered = True
            desc = f"价格跌至 **{price:.2f}**，触发阈值 ≤ {thr:.2f}"
        elif ct == "price_above" and price >= thr:
            triggered = True
            desc = f"价格涨至 **{price:.2f}**，触发阈值 ≥ {thr:.2f}"
        elif ct == "change_pct_below" and change_pct <= -thr:
            triggered = True
            desc = f"跌幅达 **{change_pct:.2f}%**，触发阈值 ≤ -{thr:.1f}%"
        elif ct == "volatility_above" and volatility >= thr:
            if thr2 is None or price <= thr2:
                triggered = True
                desc = f"日内振幅 **{volatility:.2f}%**，触发阈值 ≥ {thr:.1f}%"
                if thr2 is not None:
                    desc += f"，当前价 {price:.2f} ≤ {thr2:.2f}"

        if not triggered:
            continue

        # 冷却检查
        last = alert.get("last_triggered_at")
        if last:
            elapsed = (_now_cst() - last).total_seconds() / 60
            if elapsed < alert["cooldown_minutes"]:
                continue

        user_id = alert["user_id"]
        stock_name = q.get("name") or code
        label_part = f"「{alert['label']}」 · " if alert.get("label") else ""
        content_text = f"⚡ 价格提醒 · {stock_name}（{code}）\n{label_part}{desc}\n今开 {q.get('open','--')}  昨收 {prev_close:.2f}  最高 {high:.2f}  最低 {low:.2f}"

        # 更新触发时间 + 先发简单微信提醒
        await asyncio.to_thread(update_alert_triggered, alert["id"])
        await _broadcast_and_log(
            push_type="price_alert",
            content=content_text,
            stocks=[],
            task_name=f"价格提醒·{stock_name}({code})",
            user_id=user_id,
        )
        logger.info("价格提醒微信推送 alert_id={} code={} user={}", alert["id"], code, user_id)

        # 异步启动多智能体深度分析（不阻塞当前循环）
        asyncio.create_task(
            _run_price_alert_analysis(
                code=code,
                stock_name=stock_name,
                change_pct=change_pct,
                trigger_desc=desc,
                user_id=user_id,
            )
        )


async def _run_price_alert_analysis(
    code: str, stock_name: str, change_pct: float,
    trigger_desc: str, user_id: str,
) -> None:
    """在价格预警触发后，异步运行多智能体深度分析并推送报告。"""
    import os as _os
    import httpx
    from agents.graph import run_price_alert_graph

    try:
        thesis_info = await asyncio.to_thread(get_thesis_for_stock, code)
        thesis_text = thesis_info["thesis_text"] if thesis_info else ""
        kill_conditions = await asyncio.to_thread(get_latest_kill_conditions_for_stock, code)
    except Exception as e:
        logger.warning("获取 thesis/kill_conditions 失败 code={}: {}", code, e)
        thesis_text = ""
        kill_conditions = []

    # 拉取 TrueSource 产业链信号：调 scout 接口（并行跑价格+巨潮公告+Gemini），回退 daily-brief
    truesource_signals: list = []
    truesource_alert_level: str = "grey"
    truesource_current_price: float | None = None
    ts_base = _os.getenv("TRUESOURCE_API_URL", "http://34.92.72.140:8000")
    _refreshed = False
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=90.0, write=5.0, pool=5.0)
        ) as c:
            rr = await c.post(f"{ts_base}/api/hunter/scout/{code}")
            if rr.status_code == 200:
                rd = rr.json()
                # scout 返回结构：sources.announcements + sources.ai_search + sources.historical
                sources = rd.get("sources", {})
                ann_signals  = sources.get("announcements", [])
                ccgp_signals = sources.get("ccgp", [])
                nb_signals   = sources.get("northbound", [])
                rd_signals   = sources.get("rd_expansion", [])
                # 历史信号只保留真实爬虫来源，过滤掉 Gemini AI 生成的
                hist_signals = [s for s in sources.get("historical", [])
                                if s.get("source") not in ("gemini_search", "gemini", "ai_search")]
                # 真源只用真实爬虫数据，不包含 Gemini AI 搜索
                truesource_signals = rd_signals + nb_signals + ccgp_signals + ann_signals + hist_signals
                truesource_current_price = rd.get("price") or None
                change = rd.get("change_pct")
                truesource_alert_level = "yellow" if truesource_signals else "grey"
                _refreshed = True
                logger.info("TrueSource scout {} 完成: rd={} nb={} ccgp={} ann={} hist={}",
                            code, len(rd_signals), len(nb_signals), len(ccgp_signals),
                            len(ann_signals), len(hist_signals))
    except Exception as e:
        logger.warning("TrueSource scout 失败 code={}: {}", code, e)
    if not _refreshed:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{ts_base}/api/hunter/daily-brief?symbols={code}")
                if r.status_code == 200:
                    for stk in r.json().get("stocks", []):
                        if stk.get("symbol") == code:
                            truesource_signals = stk.get("signals", [])
                            truesource_alert_level = stk.get("alert_level", "grey")
                            truesource_current_price = stk.get("price") or None
                            break
        except Exception as e:
            logger.warning("TrueSource 信号拉取失败 code={}: {}", code, e)

    try:
        result = await run_price_alert_graph(
            ticker=code,
            stock_name=stock_name,
            change_pct=change_pct,
            trigger_desc=trigger_desc,
            thesis_text=thesis_text,
            kill_conditions=kill_conditions,
            current_price=truesource_current_price,
        )
    except Exception as e:
        logger.error("PriceAlertGraph 失败 code={}: {}", code, e)
        return

    content = _format_analysis_push(stock_name, code, change_pct, trigger_desc, result, truesource_signals, truesource_alert_level)
    await _broadcast_and_log(
        push_type="price_alert_analysis",
        content=content,
        stocks=[code],
        task_name=f"深度分析·{stock_name}({code})",
        user_id=user_id,
    )
    logger.info(
        "多智能体分析推送完成: {} {} decision={} confidence={:.0%} truesource_signals={}",
        stock_name, code, result.get("decision"), result.get("confidence", 0), len(truesource_signals),
    )


def _format_analysis_push(
    stock_name: str, code: str, change_pct: float, trigger_desc: str, result: dict,
    truesource_signals: list | None = None, truesource_alert_level: str = "grey",
) -> str:
    """将多智能体分析结果格式化为微信推送文本，附带真源产业链信号"""
    decision   = result.get("decision", "HOLD")
    confidence = result.get("confidence", 0.5)
    key_reason = result.get("key_reason", "")
    debate_mode = result.get("debate_mode", "normal")

    decision_label = {"BUY": "📈 建议买入", "SELL": "📉 建议卖出", "HOLD": "⏸ 建议持有"}.get(decision, decision)
    conf_pct = int(confidence * 100)

    # 去掉触发描述里的 Markdown 加粗符号
    trigger_clean = _re.sub(r'\*\*(.+?)\*\*', r'\1', trigger_desc)

    lines = [
        f"🤖 多智能体深度分析 · {stock_name}（{code}）",
        f"触发条件：{trigger_clean}（当日 {change_pct:+.2f}%）",
        "---",
        f"综合决策：{decision_label}（置信度 {conf_pct}%）",
        f"核心理由：{key_reason}",
    ]

    if debate_mode == "exit_strategy":
        exit_plan = result.get("exit_plan", "")
        recommended = result.get("recommended_exit", "B")
        option_map = {"A": "立即清仓", "B": "分批减仓", "C": "移动止损"}
        lines.append(f"\n🚨 止损策略：{option_map.get(recommended, recommended)}")
        if exit_plan:
            lines.append(f"退出方案：{exit_plan}")
    else:
        bull_summary = result.get("bull_summary", "")
        bear_summary = result.get("bear_summary", "")
        investment_plan = result.get("investment_plan", "")

        if bull_summary:
            lines.append(f"\n多头观点：{bull_summary[:150]}...")
        if bear_summary:
            lines.append(f"空头观点：{bear_summary[:150]}...")
        if investment_plan:
            lines.append(f"\n操作建议：{investment_plan[:200]}")

    sentinel_conflicts = result.get("sentinel_conflicts", False)
    if sentinel_conflicts:
        lines.append("\n⚠️ 注意：新闻面与技术面存在矛盾，请谨慎决策。")

    stop_loss = result.get("stop_loss")
    if stop_loss:
        lines.append(f"止损参考：{stop_loss:.2f}")

    # 真源产业链信号段
    if truesource_signals is not None:
        level_icon = {"red": "🔴", "yellow": "🟡", "green": "🟢"}.get(truesource_alert_level, "⚪")
        lines.append("---")
        lines.append(f"{level_icon} 真源产业链信号：")
        if truesource_signals:
            for s in truesource_signals[:6]:
                title = (s.get("title") or "").strip()
                url   = (s.get("url") or "").strip()
                src   = (s.get("source") or "").lower()
                if not title:
                    continue
                # cninfo公告是PDF viewer，微信不支持，换成东方财富公告列表页
                if src == "cninfo" or "cninfo.com.cn" in url:
                    url = f"https://data.eastmoney.com/notices/stock/{code[:6]}.html"
                if url:
                    lines.append(f"▸ [{title}]({url})")
                else:
                    lines.append(f"▸ {title}")
        else:
            lines.append("▸ 暂无该股覆盖数据")

    return "\n".join(lines)


async def _alert_loop() -> None:
    logger.info("price_alert_loop started")
    while not _STOP.is_set():
        # 持仓哨兵已迁移到 sentinel.agentpit.io，此处不再调用
        try:
            await _check_user_price_alerts()
        except Exception as e:
            logger.error("user_price_alert error: {}", e)
        try:
            await asyncio.wait_for(_STOP.wait(), timeout=300)  # 每 5 分钟检查一次
        except asyncio.TimeoutError:
            pass
    logger.info("price_alert_loop stopped")


# ── 启停 ─────────────────────────────────────────────────────────────────────

async def start_scheduler() -> None:
    global _SCHEDULER_TASK, _ALERT_TASK
    _STOP.clear()
    if not (_SCHEDULER_TASK and not _SCHEDULER_TASK.done()):
        _SCHEDULER_TASK = asyncio.create_task(_scheduler_loop())
    if not (_ALERT_TASK and not _ALERT_TASK.done()):
        _ALERT_TASK = asyncio.create_task(_alert_loop())


async def stop_scheduler() -> None:
    _STOP.set()
    for t in [_SCHEDULER_TASK, _ALERT_TASK]:
        if t:
            try:
                await asyncio.wait_for(t, timeout=5)
            except Exception:
                pass
