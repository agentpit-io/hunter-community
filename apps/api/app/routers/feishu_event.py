"""飞书事件接收 & 自然语言处理

POST /api/feishu/event  — 飞书 Open Platform 回调地址

处理两类事件：
  1. contact.user.created_v3  — 新员工加入企业 → Bot 发欢迎语 + 引导绑定
  2. im.message.receive_v1    — 用户给 Bot 发消息 → 绑定账号 / 查行情 / 查自选股

绑定流程：
  用户首次发消息 → 未绑定 → Bot 提示输入邮箱 → 用户发邮箱 → 查 AgentPit 用户表 → 写 feishu_bindings
"""
import json
import os
import re

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger

from app.services.database import (
    get_feishu_binding_by_open_id,
    save_feishu_binding,
    get_stocks_by_user,
)
from app.services.feishu import send_text_p2p

router = APIRouter()

FINANCE_DATA_URL = os.getenv("FINANCE_DATA_URL", "https://finance-data.agentpit.io")
FINANCE_DATA_TOKEN = os.getenv("FINANCE_DATA_TOKEN", "FinAPI@2026!")


# ─── 主入口 ───────────────────────────────────────────────────────────

@router.post("/feishu/event")
async def feishu_event(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"code": 0})

    # 飞书 URL 验证（首次填写回调地址时）
    if body.get("type") == "url_verification":
        logger.info("飞书 URL 验证 challenge")
        return {"challenge": body.get("challenge", "")}

    event_type = body.get("header", {}).get("event_type", "")
    event = body.get("event", {})
    logger.info("飞书事件收到: type={} keys={}", event_type, list(event.keys()))

    if event_type == "contact.user.created_v3":
        await _on_user_join(event)
    elif event_type == "im.message.receive_v1":
        await _on_message(event)
    else:
        logger.warning("未处理的飞书事件: {}", event_type)

    return {"code": 0}


# ─── 新员工加入 ───────────────────────────────────────────────────────

async def _on_user_join(event: dict):
    open_id = event.get("object", {}).get("open_id", "")
    if not open_id:
        return
    name = event.get("object", {}).get("name", "")
    greeting = name and f"你好 {name}！" or "欢迎！"
    await send_text_p2p(open_id, (
        f"👋 {greeting}欢迎加入 Hermes 财经！\n\n"
        "请发送您的 AgentPit 账号邮箱完成绑定，例如：\n"
        "  xxx@gmail.com\n\n"
        "绑定后即可：\n"
        "• 实时接收股价异动提醒\n"
        "• 查看自选股行情\n"
        "• 管理推送任务\n\n"
        "网页版：https://finance.hermes.agentpit.io"
    ))


# ─── 收到消息 ─────────────────────────────────────────────────────────

async def _on_message(event: dict):
    open_id = event.get("sender", {}).get("sender_id", {}).get("open_id", "")
    if not open_id:
        return

    # 只处理文本消息
    msg = event.get("message", {})
    if msg.get("message_type") != "text":
        await send_text_p2p(open_id, "目前只支持文字消息，请输入文字指令。")
        return

    try:
        text = json.loads(msg.get("content", "{}")).get("text", "").strip()
    except Exception:
        text = ""
    if not text:
        return

    # 去掉 @机器人 前缀（群消息里会有）
    text = re.sub(r"@\S+\s*", "", text).strip()
    if not text:
        return

    binding = get_feishu_binding_by_open_id(open_id)

    if not binding:
        await _handle_binding(open_id, text)
    else:
        await _handle_command(open_id, binding["user_id"], text)


# ─── 绑定流程 ─────────────────────────────────────────────────────────

async def _handle_binding(open_id: str, text: str):
    # 判断是否是邮箱
    email_pattern = r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
    if not re.match(email_pattern, text):
        await send_text_p2p(open_id, (
            "请发送您的 AgentPit 账号邮箱完成绑定。\n"
            "例如：xxx@gmail.com\n\n"
            "还没有账号？前往注册：https://agentpit.io"
        ))
        return

    # 查 AgentPit 用户表
    user = await _lookup_agentpit_user(text.lower().strip())
    if not user:
        await send_text_p2p(open_id, (
            f"❌ 未找到账号 {text}\n\n"
            "请确认邮箱是否正确，或前往注册：https://agentpit.io"
        ))
        return

    save_feishu_binding(user["id"], open_id)
    await send_text_p2p(open_id, (
        f"✅ 绑定成功！\n\n"
        f"账号：{user['email']}\n\n"
        "现在可以发送指令：\n"
        "• 发送「我的股票」查看自选股\n"
        "• 发送「查 600519」查贵州茅台行情\n"
        "• 发送「帮助」查看所有指令\n\n"
        "网页设置更多功能：https://finance.hermes.agentpit.io"
    ))


async def _lookup_agentpit_user(email: str) -> dict | None:
    """查 AgentPit 数据库验证邮箱是否存在。"""
    import psycopg2
    db_url = os.environ.get(
        "AGENTPIT_DATABASE_URL",
        ""
    )
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute('SELECT id, email, name FROM "apbase_User" WHERE email = %s', (email,))
        row = cur.fetchone()
        conn.close()
        if row:
            return {"id": row[0], "email": row[1], "name": row[2]}
    except Exception as e:
        logger.error("查 AgentPit 用户失败: {}", e)
    return None


# ─── 指令处理 ─────────────────────────────────────────────────────────

async def _handle_command(open_id: str, user_id: str, text: str):
    t = text.lower()

    # 帮助
    if any(k in t for k in ["帮助", "help", "怎么用", "命令", "指令"]):
        await send_text_p2p(open_id, _help_text())
        return

    # 查看自选股
    if any(k in t for k in ["自选股", "我的股票", "持仓", "我的自选"]):
        await _cmd_list_stocks(open_id, user_id)
        return

    # 查行情：「查 600519」「茅台行情」「600519 涨了多少」
    code = _extract_stock_code(text)
    if code:
        await _cmd_quote(open_id, code)
        return

    # 兜底
    await send_text_p2p(open_id, (
        "没太明白您的意思 🤔\n\n"
        "支持的指令：\n"
        "• 我的股票\n"
        "• 查 600519\n"
        "• 帮助\n\n"
        "更多功能请访问网页版：https://finance.hermes.agentpit.io"
    ))


def _extract_stock_code(text: str) -> str | None:
    """从文本里提取6位股票代码。"""
    # 直接6位数字
    m = re.search(r"\b(\d{6})\b", text)
    if m:
        return m.group(1)
    # 「查 xxx」「看 xxx」形式
    m = re.search(r"[查看]\s*([^\s，。！？]+)", text)
    if m:
        word = m.group(1)
        m2 = re.search(r"\d{6}", word)
        if m2:
            return m2.group()
    return None


async def _cmd_list_stocks(open_id: str, user_id: str):
    stocks = get_stocks_by_user(user_id)
    if not stocks:
        await send_text_p2p(open_id, (
            "您还没有添加自选股。\n"
            "请访问网页版添加：https://finance.hermes.agentpit.io"
        ))
        return
    lines = [f"📋 您的自选股（{len(stocks)} 只）：\n"]
    for s in stocks:
        lines.append(f"• {s['name']}（{s['code']}）{s.get('market','')}股")
    await send_text_p2p(open_id, "\n".join(lines))


async def _cmd_quote(open_id: str, code: str):
    try:
        async with httpx.AsyncClient(timeout=10, verify=False) as c:
            r = await c.get(
                f"{FINANCE_DATA_URL}/api/v1/realtime-quote/{code}.SH",
                headers={"X-Finance-Token": FINANCE_DATA_TOKEN},
            )
            # 试 SH，失败再试 SZ
            if r.status_code != 200 or not r.json().get("price"):
                r = await c.get(
                    f"{FINANCE_DATA_URL}/api/v1/realtime-quote/{code}.SZ",
                    headers={"X-Finance-Token": FINANCE_DATA_TOKEN},
                )
        if r.status_code == 200:
            d = r.json()
            price = d.get("price") or "--"
            pct = d.get("change_pct")
            pct_str = f"{float(pct):+.2f}%" if pct is not None else "--"
            name = d.get("name") or code
            arrow = "📈" if pct and float(pct) > 0 else "📉" if pct and float(pct) < 0 else "➖"
            await send_text_p2p(open_id, (
                f"{arrow} {name}（{code}）\n"
                f"当前价：{price}\n"
                f"涨跌幅：{pct_str}"
            ))
            return
    except Exception as e:
        logger.warning("查行情失败 {}: {}", code, e)

    await send_text_p2p(open_id, f"暂时无法获取 {code} 的行情数据，请稍后再试。")


def _help_text() -> str:
    return (
        "🤖 Hermes 财经助手 — 指令列表\n\n"
        "📊 行情查询\n"
        "  查 600519   — 查贵州茅台行情\n"
        "  查 300750   — 查宁德时代行情\n\n"
        "📋 自选股\n"
        "  我的股票    — 查看自选股列表\n\n"
        "⚙️ 更多功能\n"
        "  添加自选股、设置提醒、推送任务\n"
        "  请访问：https://finance.hermes.agentpit.io\n\n"
        "如有问题请联系管理员。"
    )
