"""WeChat template message push for Hermes push_scheduler."""
import os
import re
from datetime import datetime, timezone, timedelta

import httpx
import pymysql
import redis as _redis_lib
from loguru import logger

_WX_TEMPLATE_ID = "lY-reONONeKMeRTR4EwOK388twzNM1eSlDX-HJ6alSI"
_OA_DOMAIN      = os.getenv("WECHAT_OA_DOMAIN", "https://yiqihecheng.net")
_REDIS_URL      = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_OA_APPID       = os.getenv("WECHAT_OA_APPID", "wxbf533fc58dc6b296")

_WX_MYSQL = dict(
    host=os.getenv("YIQIHECHENG_MYSQL_HOST", "35.220.204.98"),
    port=int(os.getenv("YIQIHECHENG_MYSQL_PORT", "3306")),
    user=os.getenv("YIQIHECHENG_MYSQL_USER", "root"),
    password=os.getenv("YIQIHECHENG_MYSQL_PASSWORD", "AKPfnHDFCaMHwNxK22*"),
    database=os.getenv("YIQIHECHENG_MYSQL_DB", "myhub3"),
    connect_timeout=8,
)

_wx_redis = _redis_lib.from_url(_REDIS_URL, decode_responses=True)
CST = timezone(timedelta(hours=8))


def get_authorizer_token() -> str:
    cached = _wx_redis.get("wx_authorizer_access_token")
    if cached:
        return cached
    try:
        conn = pymysql.connect(**_WX_MYSQL)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT authorizerAccessToken, expiresIn FROM authorize WHERE authorizerAppid = %s",
                (_OA_APPID,),
            )
            row = cur.fetchone()
        conn.close()
        if row:
            token, expires_in = row
            ttl = max(int(expires_in) - 300, 60)
            _wx_redis.set("wx_authorizer_access_token", token, ex=ttl)
            logger.info("[wx_push] authorizer_token synced ttl={}s", ttl)
            return token
    except Exception as e:
        logger.error("[wx_push] sync authorizer_token failed: {}", e)
    return ""


def _extract_fields(title: str, content: str, push_type: str) -> tuple[str, str, str]:
    """返回 (first_line, keyword1_类型, keyword3_关键数据)"""
    lines = [l.strip() for l in content.split("\n") if l.strip()]
    now_date = datetime.now(CST).strftime("%m/%d")

    if push_type == "close_review":
        # 第2行: "📅 06月09日 16:52 ｜ 6只股票 3涨 3跌"
        stat_line = next((l for l in lines if "只股票" in l or ("涨" in l and "跌" in l and "📅" in l)), "")
        stat_short = re.search(r"(\d+只股票\s*\d+涨\s*\d+跌|\d+涨\s*\d+跌)", stat_line)
        stat_str = stat_short.group(0).replace(" ", "") if stat_short else ""
        first = f"📊 {now_date} 收盘 · {stat_str}" if stat_str else f"📊 {now_date} 自选股收盘总结"

        # 提取涨跌行: 🟢 新和成 (002001) +3.46%  ¥31.74
        moves = []
        for l in lines:
            m = re.search(r"[🟢🔴]\s*([一-龥A-Za-z0-9]+)\s*\([A-Za-z0-9]+\)\s*([+-][\d.]+%)", l)
            if m:
                moves.append(f"{m.group(1)}{m.group(2)}")
            if len(moves) >= 3:
                break
        data = "  ".join(moves) if moves else content[:60].replace("\n", " ")
        return first, "自选股收盘总结", data

    if push_type in ("fundflow_topn", "fundflow_alert"):
        # 提取 Top3 资金流入
        top_items = []
        for l in lines:
            m = re.search(r"(\d+)\.\s*([一-龥A-Za-z0-9]+)\s*\([^)]+\)\s*([¥HK$\d.亿万]+)", l)
            if m:
                top_items.append(f"{m.group(2)} {m.group(3)}")
            if len(top_items) >= 3:
                break
        first_item = top_items[0] if top_items else ""
        first = f"🐂 {now_date} 资金流向 · {first_item}" if first_item else f"🐂 {now_date} 资金流向异动"
        data = "  ".join(top_items) if top_items else content[:60].replace("\n", " ")
        return first, "自选股资金流向", data

    if push_type == "watchlist_morning":
        # 第2行含"N只": "📅 06月09日 ｜ 隔夜消息 + 开盘快照"
        count_m = re.search(r"(\d+)只", content)
        count_str = f"{count_m.group(1)}只" if count_m else ""
        first = f"🌅 {now_date} 早报 · {count_str}股票开盘快照" if count_str else f"🌅 {now_date} 自选股早报"
        # 取第一条新闻标题（破折号后）
        news_line = next((l for l in lines if " — " in l or "—" in l), "")
        news_short = re.split(r"[—–]", news_line)[-1].strip()[:40] if news_line else ""
        data = news_short if news_short else content[:60].replace("\n", " ")
        return first, "自选股早报", data

    if push_type == "price_alert":
        # "⚡ 价格提醒 · 豪迈科技（002595）"
        stock_m = re.search(r"·\s*([一-龥A-Za-z0-9]+)（(\d{6})）", lines[0]) if lines else None
        stock_name = stock_m.group(1) if stock_m else title
        stock_code = stock_m.group(2) if stock_m else ""
        first = f"⚡ 价格提醒 · {stock_name}({stock_code})" if stock_code else f"⚡ 价格提醒 · {stock_name}"
        # 第2行: 触发描述
        trigger = lines[1] if len(lines) > 1 else ""
        trigger_clean = re.sub(r"\*+", "", trigger).strip()[:50]
        data = trigger_clean if trigger_clean else content[:60].replace("\n", " ")
        return first, f"{stock_name}({stock_code})", data

    if push_type == "price_alert_analysis":
        # "🤖 多智能体深度分析 · 豪迈科技（002595）"
        stock_m = re.search(r"·\s*([一-龥A-Za-z0-9]+)（(\d{6})）", lines[0]) if lines else None
        stock_name = stock_m.group(1) if stock_m else title
        stock_code = stock_m.group(2) if stock_m else ""
        # 综合决策行 → keyword1（用户最想知道的结论）
        dec_line = next((l for l in lines if "综合决策" in l), "")
        dec_m = re.search(r"综合决策：(.+?)（", dec_line)
        decision_str = dec_m.group(1).strip() if dec_m else "分析完成"
        # 核心理由 → keyword3
        reason_line = next((l for l in lines if "核心理由" in l), "")
        reason_m = re.search(r"核心理由：(.+)", reason_line)
        reason_str = reason_m.group(1).strip()[:45] if reason_m else ""
        # 真源信号 → 追加到 keyword3
        ts_line = next((l for l in lines if "真源产业链" in l), "")
        ts_m = re.search(r"真源产业链信号：(.+)", ts_line)
        ts_str = ts_m.group(1).strip()[:20] if ts_m else ""
        data = (reason_str + ("  " + ts_str if ts_str else ""))[:60] or content[:60].replace("\n", " ")
        first = f"📊 归因分析 · {stock_name}({stock_code})"
        return first, decision_str, data

    if push_type == "news_today":
        first = f"📰 {now_date} 财经新闻速递"
        news_line = next((l for l in lines if "▸" in l or " — " in l), "")
        news_short = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", news_line)[:50].strip()
        data = news_short if news_short else content[:60].replace("\n", " ")
        return first, "今日财经新闻", data

    if push_type in ("oil_alert", "cpi_alert", "fomc_alert", "spacex_alert", "northbound_alert"):
        label_map = {
            "oil_alert": "🛢️ 油价异动",
            "cpi_alert": "📊 CPI通胀信号",
            "fomc_alert": "🏦 FOMC利率决议",
            "spacex_alert": "🚀 SpaceX IPO",
            "northbound_alert": "🧲 北向资金",
        }
        label = label_map.get(push_type, "📡 市场信号")
        first = f"{label} · {now_date}"
        # 第1行是信号摘要（如"布伦特 $94.0/桶 24H +6.10%..."）
        summary_line = lines[0] if lines else ""
        summary_clean = summary_line[:50].strip()
        data = summary_clean or content[:50].replace("\n", " ")
        return first, label, data

    # 默认：用原 title + 截取摘要
    first = f"📢 {title}"
    summary = content[:60].replace("\n", " ").strip()
    return first, title, summary


async def broadcast(title: str, content: str, log_id: str, user_id: str = "", push_type: str = "", detail_url: str = "") -> int:
    """Send WeChat template messages. If user_id given, only send to that user. Returns sent count."""
    if user_id:
        openid = _wx_redis.get(f"wx_openid:{user_id}")
        bound = {user_id} if openid else set()
    else:
        bound = _wx_redis.smembers("wx_bound_users")
    if not bound:
        return 0
    auth_token = get_authorizer_token()
    if not auth_token:
        logger.error("[wx_push] no authorizer_access_token, skip")
        return 0

    now_str = datetime.now(CST).strftime("%Y-%m-%d %H:%M")
    detail_url = detail_url or (f"{_OA_DOMAIN}/push-detail/{log_id}" if log_id else _OA_DOMAIN)

    first_line, kw1, kw3 = _extract_fields(title, content, push_type)

    sent = 0
    for uid in bound:
        openid = _wx_redis.get(f"wx_openid:{uid}")
        if not openid:
            continue
        payload = {
            "touser": openid,
            "template_id": _WX_TEMPLATE_ID,
            "url": detail_url,
            "data": {
                "first":    {"value": first_line, "color": "#07c160"},
                "keyword1": {"value": kw1, "color": "#173177"},
                "keyword2": {"value": now_str, "color": "#173177"},
                "keyword3": {"value": kw3, "color": "#333333"},
                "remark":   {"value": "点击查看完整内容 →", "color": "#07c160"},
            },
        }
        api_url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={auth_token}"
        try:
            async with httpx.AsyncClient(timeout=10, verify=False) as c:
                r = await c.post(api_url, json=payload)
            data = r.json()
            if data.get("errcode") == 40001:
                _wx_redis.delete("wx_authorizer_access_token")
                auth_token = get_authorizer_token()
                if auth_token:
                    api_url2 = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={auth_token}"
                    async with httpx.AsyncClient(timeout=10, verify=False) as c2:
                        r = await c2.post(api_url2, json=payload)
                    data = r.json()
            if data.get("errcode", 0) == 0:
                sent += 1
            else:
                logger.error("[wx_push] send failed openid={}... {}", openid[:8], data)
        except Exception as e:
            logger.error("[wx_push] send error user={}: {}", uid, e)

    logger.info("[wx_push] broadcast done sent={}/{}", sent, len(bound))
    return sent
