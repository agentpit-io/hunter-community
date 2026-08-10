"""消息推送 REST：模板列表 + 任务 CRUD + 立即试发 + 推送日志。"""
import asyncio
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services.database import get_conn
from app.services.feishu import send_card
from app.services.finance_data_client import get_quote
from app.services.push_content import render_price_alert
from app.services.push_scheduler import trigger_task_now

router = APIRouter()

_FD_URL     = os.getenv("FINANCE_DATA_URL",   "https://finance-data.agentpit.io")
_FD_TOKEN   = os.getenv("FINANCE_DATA_TOKEN", "FinAPI@2026!")
_FD_HEADERS = {"X-Finance-Token": _FD_TOKEN}

_HERMES_ENV = os.path.expanduser("~/.hermes/.env")


def _get_default_chat() -> str:
    """动态读取 ~/.hermes/.env 里的 FEISHU_HOME_CHANNEL，支持通过页面配置后立即生效"""
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


TEMPLATES = [
    {
        "id": "daily_news_evening",
        "title": "每日新闻速递",
        "emoji": "📰",
        "description": "每晚汇总 6 只自选股相关新闻，附原文链接",
        "default_time": "20:00",
        "default_content_type": "news_today",
        "color": "blue",
    },
    {
        "id": "watchlist_morning",
        "title": "自选股早报",
        "emoji": "📊",
        "description": "盘前快照：6 只自选股开盘前行情、隔夜消息",
        "default_time": "08:30",
        "default_content_type": "watchlist_morning",
        "color": "turquoise",
    },
    {
        "id": "close_review",
        "title": "盘后复盘",
        "emoji": "💹",
        "description": "收盘后推送当日涨跌、资金流向、异动",
        "default_time": "15:30",
        "default_content_type": "close_review",
        "color": "carmine",
    },
    {
        "id": "fundflow_alert",
        "title": "资金流向异动",
        "emoji": "🐂",
        "description": "盘中检测主力净流入 Top3 自选股",
        "default_time": "14:30",
        "default_content_type": "fundflow_topn",
        "color": "orange",
    },
    {
        "id": "weekly_summary",
        "title": "周度持仓汇总",
        "emoji": "📅",
        "description": "每周五推送过去 5 个交易日的自选股表现",
        "default_time": "18:00",
        "default_content_type": "weekly_summary",
        "color": "violet",
    },
    {
        "id": "custom",
        "title": "自定义推送",
        "emoji": "✏️",
        "description": "自己输入推送内容和触发时间",
        "default_time": "12:00",
        "default_content_type": "custom",
        "color": "grey",
    },
]


@router.get("/push/templates")
async def list_templates():
    return {"templates": TEMPLATES, "default_target_chat": _get_default_chat()}


class TaskIn(BaseModel):
    name: str
    template_id: str
    schedule_time: str  # HH:MM
    content_type: str
    custom_content: Optional[str] = ""
    target_chat: Optional[str] = ""
    enabled: bool = True


def _uid(request: Request) -> str:
    return getattr(request.state, "user_id", "") or ""


@router.get("/push/tasks")
async def list_tasks(request: Request):
    user_id = _uid(request)
    rows = []
    conn = get_conn()
    cur = conn.cursor()
    if user_id:
        cur.execute(
            "SELECT id, name, template_id, schedule_time, content_type, custom_content, "
            "target_chat, enabled, last_run_date, last_status, last_message, created_at "
            "FROM push_tasks WHERE user_id = %s ORDER BY id DESC", (user_id,)
        )
    else:
        cur.execute(
            "SELECT id, name, template_id, schedule_time, content_type, custom_content, "
            "target_chat, enabled, last_run_date, last_status, last_message, created_at "
            "FROM push_tasks ORDER BY id DESC"
        )
    for r in cur.fetchall():
        rows.append({
            "id": r[0], "name": r[1], "template_id": r[2],
            "schedule_time": r[3], "content_type": r[4],
            "custom_content": r[5] or "",
            "target_chat": r[6] or _get_default_chat(),
            "enabled": r[7],
            "last_run_date": str(r[8]) if r[8] else None,
            "last_status": r[9] or "", "last_message": r[10] or "",
            "created_at": str(r[11]) if r[11] else "",
        })
    conn.close()
    return {"tasks": rows}


def _validate_hhmm(s: str) -> str:
    s = (s or "").strip()
    if len(s) != 5 or s[2] != ":":
        raise HTTPException(400, "schedule_time 必须 HH:MM 格式")
    try:
        h, m = int(s[:2]), int(s[3:])
    except Exception:
        raise HTTPException(400, "schedule_time 格式错")
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise HTTPException(400, "schedule_time 越界")
    return s


@router.post("/push/tasks")
async def create_task(body: TaskIn, request: Request):
    user_id = _uid(request)
    schedule_time = _validate_hhmm(body.schedule_time)
    target = body.target_chat or _get_default_chat()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO push_tasks (name, template_id, schedule_time, content_type, "
        "custom_content, target_chat, enabled, user_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (body.name.strip() or "未命名", body.template_id, schedule_time,
         body.content_type, body.custom_content or "", target, body.enabled, user_id),
    )
    task_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return {"ok": True, "id": task_id}


class TaskPatch(BaseModel):
    name: Optional[str] = None
    schedule_time: Optional[str] = None
    content_type: Optional[str] = None
    custom_content: Optional[str] = None
    target_chat: Optional[str] = None
    enabled: Optional[bool] = None


@router.patch("/push/tasks/{task_id}")
async def update_task(task_id: int, body: TaskPatch, request: Request):
    user_id = _uid(request)
    fields = []
    values = []
    if body.name is not None:
        fields.append("name = %s"); values.append(body.name)
    if body.schedule_time is not None:
        fields.append("schedule_time = %s"); values.append(_validate_hhmm(body.schedule_time))
    if body.content_type is not None:
        fields.append("content_type = %s"); values.append(body.content_type)
    if body.custom_content is not None:
        fields.append("custom_content = %s"); values.append(body.custom_content)
    if body.target_chat is not None:
        fields.append("target_chat = %s"); values.append(body.target_chat or _get_default_chat())
    if body.enabled is not None:
        fields.append("enabled = %s"); values.append(body.enabled)
    if not fields:
        return {"ok": True, "noop": True}
    if user_id:
        values.append(user_id); values.append(task_id)
        where = "WHERE id = %s AND user_id = %s"
        cur_vals = values[:-2] + [values[-1], values[-2]]  # reorder: fields..., user_id, id
    else:
        values.append(task_id)
        where = "WHERE id = %s"
        cur_vals = values

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"UPDATE push_tasks SET {', '.join(fields)} {where}", cur_vals)
    conn.commit()
    conn.close()
    return {"ok": True}




@router.patch("/push/tasks/{task_id}/toggle")
async def toggle_task(task_id: int, request: Request):
    user_id = _uid(request)
    conn = get_conn()
    cur = conn.cursor()
    if user_id:
        cur.execute(
            "UPDATE push_tasks SET enabled = NOT enabled WHERE id = %s AND user_id = %s RETURNING id, enabled",
            (task_id, user_id),
        )
    else:
        cur.execute(
            "UPDATE push_tasks SET enabled = NOT enabled WHERE id = %s RETURNING id, enabled",
            (task_id,),
        )
    row = cur.fetchone()
    conn.commit(); conn.close()
    if not row:
        raise HTTPException(404, "任务不存在")
    return {"id": row[0], "enabled": row[1]}

@router.delete("/push/tasks/{task_id}")
async def delete_task(task_id: int, request: Request):
    user_id = _uid(request)
    conn = get_conn()
    cur = conn.cursor()
    if user_id:
        cur.execute("DELETE FROM push_tasks WHERE id = %s AND user_id = %s", (task_id, user_id))
    else:
        cur.execute("DELETE FROM push_tasks WHERE id = %s", (task_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/push/test/{task_id}")
async def test_task(task_id: int):
    return await trigger_task_now(task_id)


@router.get("/push/logs")
async def list_push_logs(limit: int = 100, request: Request = None):
    """推送日志列表（只返回当前用户的）"""
    user_id = _uid(request) if request else ""
    try:
        async with httpx.AsyncClient(timeout=10, verify=False) as c:
            r = await c.get(
                f"{_FD_URL}/api/v1/internal/wechat/push-logs",
                headers=_FD_HEADERS,
                params={"limit": limit, "user_id": user_id},
            )
            return r.json()
    except Exception as e:
        raise HTTPException(503, f"finance-data unavailable: {e}")


@router.get("/push/logs/public/{log_id}")
async def get_push_log_public(log_id: int):
    """推送详情（无需登录，供微信用户点击链接查看）"""
    try:
        async with httpx.AsyncClient(timeout=10, verify=False) as c:
            r = await c.get(
                f"{_FD_URL}/api/v1/internal/wechat/push-logs/{log_id}",
                headers=_FD_HEADERS,
            )
            if r.status_code == 404:
                raise HTTPException(404, "推送记录不存在")
            return r.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"finance-data unavailable: {e}")


class BroadcastNowBody(BaseModel):
    content_type: str
    custom_content: str = ""
    admin_only: bool = False


@router.post("/push/broadcast-now")
async def broadcast_now(body: BroadcastNowBody, request: Request):
    """手工触发一次推送广播：admin_only=true 仅推给当前登录用户（测试用）。"""
    from app.services.push_content import render_card
    from app.services.push_scheduler import _card_to_text, _broadcast_and_log

    user_id = _uid(request)
    if not user_id:
        raise HTTPException(401, "需要登录")

    try:
        card = await render_card(body.content_type, body.custom_content, "手工推送", user_id)
    except Exception as e:
        raise HTTPException(500, f"渲染内容失败: {e}")

    wechat_text = _card_to_text(card)
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    content_labels = {
        "watchlist_morning": "自选股早报", "close_review": "盘后复盘",
        "news_today": "今日新闻速递", "fundflow_topn": "资金流向TopN",
        "weekly_summary": "周度汇总", "custom": "自定义推送",
    }
    label = content_labels.get(body.content_type, body.content_type)

    if body.admin_only:
        try:
            async with httpx.AsyncClient(timeout=10, verify=False) as c:
                r = await c.get(
                    f"{_FD_URL}/api/v1/internal/wechat/bind/by-user/{user_id}",
                    headers=_FD_HEADERS,
                )
                bind = r.json()
            if not bind.get("bound"):
                raise HTTPException(400, "当前账号未绑定微信，请先关注服务号完成绑定")
            async with httpx.AsyncClient(timeout=10, verify=False) as c:
                await c.post(
                    f"{_FD_URL}/api/v1/internal/wechat/push",
                    headers=_FD_HEADERS,
                    json={
                        "user_id": user_id, "openid": bind["openid"],
                        "push_date": today, "push_type": f"{body.content_type}_test",
                        "stocks": [], "content": wechat_text,
                    },
                )
                await c.post(
                    f"{_FD_URL}/api/v1/internal/wechat/push-log",
                    headers=_FD_HEADERS,
                    json={
                        "task_name": f"手工测试·{label}",
                        "push_type": body.content_type,
                        "push_date": today,
                        "status": "ok",
                        "written_count": 1,
                        "content": wechat_text,
                        "user_id": user_id,
                    },
                )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"推送失败: {e}")
        return {"ok": True, "note": "已推给本账号绑定的微信（测试）"}
    else:
        asyncio.create_task(_broadcast_and_log(
            push_type=body.content_type,
            content=wechat_text,
            stocks=[],
            task_name=f"手工执行·{label}",
            user_id=user_id,
        ))
        return {"ok": True, "note": "已触发，结果将写入推送日志"}


@router.post("/push/test-price-alert")
async def test_price_alert():
    """立即发送豪迈科技价格预警测试（跳过时段和阈值限制，用真实行情数据）。"""
    target = _get_default_chat()
    if not target:
        raise HTTPException(400, "FEISHU_HOME_CHANNEL 未配置")

    q = await asyncio.to_thread(get_quote, "002595")
    if not q or q.get("price") is None:
        raise HTTPException(503, "无法获取豪迈科技行情数据")

    current    = float(q["price"])
    open_price = float(q.get("open") or current)
    change_pct = (current - open_price) / open_price * 100 if open_price > 0 else 0.0
    threshold  = float(os.getenv("PRICE_ALERT_THRESHOLD_PCT", "2.0"))

    card   = render_price_alert("002595", "豪迈科技", current, open_price, change_pct, threshold)
    result = await send_card(target, card)
    return {"ok": result.get("ok"), "change_pct": round(change_pct, 2),
            "current": current, "open": open_price, "feishu": result}
