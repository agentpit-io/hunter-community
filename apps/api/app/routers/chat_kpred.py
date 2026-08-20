"""Chat Kronos 预测 SKILL · Sprint E · HTML 富可视化输出

复用 kpred.py 的 get_kpred_pro 逻辑 · 输出与 /kpred 页同款的 HTML 报告 ·
通过 SSE 推进度 · 前端 iframe 展示 · 支持 Publish 匿名公开链接

Endpoints:
  POST /api/chat/kpred/start                     创建任务 · 返 task_id
  GET  /api/public/chat_kpred/stream/{task_id}   SSE 事件流(匿名 · task_id 随机不可枚举)

Rate limit:与 chat_debate 共用 Redis 命名空间 · 24h 30 次(比 debate 宽松 · kpred 便宜)
"""
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import AsyncGenerator, Optional

import redis as redis_lib
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

# ── agents/ 加 sys.path(与 chat_debate 一致) ────────────────
_HERMES_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.realpath(__file__)
))))
if _HERMES_ROOT not in sys.path:
    sys.path.insert(0, _HERMES_ROOT)

from app.services.chat_debate.stock_resolver import resolve_stock
from app.services.chat_debate.kpred_html_renderer import render_kpred_html
from app.services.database import get_conn

_CST = timezone(timedelta(hours=8))

_RATE_LIMIT_WINDOW_SEC = 24 * 3600   # 24 h
_RATE_LIMIT_MAX = 30                  # 24h 30 次 · 比 debate 宽松

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_redis = redis_lib.from_url(_REDIS_URL, decode_responses=True)

router = APIRouter()

# 任务队列 · 与 chat_debate 同构
_TASKS: dict[str, asyncio.Queue] = {}


# ── DB 持久化 · Sprint H ──────────────────────────

def _save_kpred_report(
    task_id: str,
    user_id: str,
    session_id: Optional[str],
    stock_code: str,
    stock_name: str,
    days: int,
    composite_score: int,
    rating: str,
    adj_return_pct: float,
    content_html: str,
    summary_md: str,
    question: str,
    elapsed_sec: int,
) -> None:
    """保存 kpred 报告 · 失败仅 log 不阻塞主流程"""
    try:
        c = get_conn(); cur = c.cursor()
        cur.execute("""
            INSERT INTO chat_kpred.reports
              (task_id, user_id, session_id, stock_code, stock_name, days,
               composite_score, rating, adj_return_pct, content_html, summary_md,
               question, elapsed_sec)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (task_id) DO NOTHING
        """, (
            task_id, user_id, session_id, stock_code, stock_name, days,
            composite_score, rating, round(float(adj_return_pct), 2),
            content_html, summary_md, question, elapsed_sec,
        ))
        c.commit(); c.close()
    except Exception as e:
        logger.warning("[chat_kpred] 报告持久化失败(非致命): {}", e)


def _list_session_kpreds(user_id: str, session_id: str, limit: int = 20) -> list[dict]:
    """列本 session 下用户所有 kpred 报告 · 按时间升序 · session 加载时用"""
    try:
        c = get_conn(); cur = c.cursor()
        cur.execute("""
            SELECT task_id, stock_code, stock_name, days,
                   composite_score, rating, adj_return_pct,
                   content_html, summary_md, question, elapsed_sec, created_at
            FROM chat_kpred.reports
            WHERE user_id = %s AND session_id = %s
            ORDER BY created_at ASC
            LIMIT %s
        """, (user_id, session_id, limit))
        rows = cur.fetchall()
        c.close()
        return [{
            "task_id":         r[0],
            "stock_code":      r[1],
            "stock_name":      r[2],
            "days":            r[3],
            "composite_score": r[4],
            "rating":          r[5],
            "adj_return_pct":  float(r[6]) if r[6] is not None else 0.0,
            "content_html":    r[7],
            "summary_md":      r[8],
            "question":        r[9],
            "elapsed_sec":     r[10],
            "created_at":      r[11].isoformat() if r[11] else None,
        } for r in rows]
    except Exception as e:
        logger.warning("[chat_kpred] 拉 session 报告失败: {}", e)
        return []


class KpredStartIn(BaseModel):
    stock_query: str = Field(..., min_length=1, max_length=100)
    days: int = Field(5, ge=1, le=30, description="预测天数 · 1-30")
    # question 只用于留档展示,前端会把完整用户原文塞过来 · 500 太紧,长问题一发就 422。
    # 提到 2000 保留一定收敛,防止误粘超长文本把 DB 也撑坏。
    question: str = Field("", max_length=2000)
    session_id: Optional[str] = Field(None)


class KpredStartResp(BaseModel):
    task_id: str
    stream_url: str
    stock_code: str
    stock_name: str


def _check_rate_limit(user_id: str) -> tuple[bool, int]:
    key = f"chat_kpred:ratelimit:{user_id}"
    try:
        count = _redis.incr(key)
        if count == 1:
            _redis.expire(key, _RATE_LIMIT_WINDOW_SEC)
        if count > _RATE_LIMIT_MAX:
            ttl = _redis.ttl(key)
            return False, max(1, ttl if ttl and ttl > 0 else _RATE_LIMIT_WINDOW_SEC)
        return True, 0
    except Exception as e:
        logger.warning("[chat_kpred] rate limit redis 异常 · 放行: {}", e)
        return True, 0


def _refund_rate_limit(user_id: str) -> None:
    key = f"chat_kpred:ratelimit:{user_id}"
    try:
        _redis.decr(key)
    except Exception as e:
        logger.warning("[chat_kpred] rate limit refund 异常: {}", e)


@router.post("/chat/kpred/start", response_model=KpredStartResp)
async def start_kpred(payload: KpredStartIn, request: Request):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "需要登录")

    allowed, reset_sec = _check_rate_limit(user_id)
    if not allowed:
        raise HTTPException(
            429,
            f"走势预测 24 小时内最多 30 次 · 请 {reset_sec // 3600} 小时 {(reset_sec % 3600) // 60} 分后再试"
        )

    try:
        code, name = await resolve_stock(payload.stock_query)
    except HTTPException:
        _refund_rate_limit(user_id)
        raise

    task_id = f"kpd_{uuid.uuid4().hex[:12]}"
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _TASKS[task_id] = queue

    asyncio.create_task(_worker(
        task_id=task_id,
        user_id=user_id,
        code=code,
        name=name,
        days=payload.days,
        question=payload.question,
        session_id=payload.session_id,
        queue=queue,
    ))

    return KpredStartResp(
        task_id=task_id,
        stream_url=f"/api/public/chat_kpred/stream/{task_id}",
        stock_code=code,
        stock_name=name,
    )


async def _worker(
    task_id: str,
    user_id: str,
    code: str,
    name: str,
    days: int,
    question: str,
    session_id: Optional[str],
    queue: asyncio.Queue,
) -> None:
    t0 = time.time()

    async def emit(phase: str, pct: int, text: str = "", extra: dict | None = None):
        payload = {"phase": phase, "pct": pct, "text": text}
        if extra:
            payload.update(extra)
        try:
            await queue.put({"type": "progress", "data": payload})
        except Exception:
            pass

    try:
        logger.info("[chat_kpred:{}] worker 启动 · user={} · {}({}) · {}天", task_id, user_id, name, code, days)

        # 正规化 ticker(与 kpred.py 一致)
        ticker = code
        if "." not in ticker:
            if ticker.startswith("6"):
                ticker = f"{ticker}.SH"
            elif ticker.startswith(("0", "3")):
                ticker = f"{ticker}.SZ"
            elif ticker.startswith(("4", "8", "9")):
                ticker = f"{ticker}.BJ"

        await emit("start", 5, f"启动 Kronos 预测 · {name}({code}) · {days} 天...")
        await emit("kronos", 15, "调用 Kronos 金融时序大模型...")

        # 直接调 kpred.py 的 get_kpred_pro · 复用所有逻辑(含降级/重锚定/因子引擎)
        from app.routers.kpred import get_kpred_pro
        # get_kpred_pro 需要 request · 我们没有 · 传 None 走 _optional_user_id fallback
        t_pro = time.time()
        logger.info("[chat_kpred:{}] before get_kpred_pro · ticker={} · days={}", task_id, ticker, days)
        pro_result = await get_kpred_pro(
            code=ticker,
            days=days,
            request=None,
        )
        logger.info("[chat_kpred:{}] after get_kpred_pro · 耗时 {:.2f}s", task_id, time.time() - t_pro)

        await emit("factor", 70, "8 因子加权计算...")
        await asyncio.sleep(0)   # 让 event loop 走一下 · 保证事件先推出去

        await emit("rendering", 88, "生成 HTML 报告...")
        t_render = time.time()
        html_content = render_kpred_html(pro_result, stock_query=name)
        logger.info("[chat_kpred:{}] after render · html_len={} · 耗时 {:.2f}s", task_id, len(html_content), time.time() - t_render)

        # 简短 markdown 摘要 · 塞进 chat 消息流 · 供报告面板不打开时预览
        pro = pro_result.get("pro") or {}
        rating = pro.get("rating", "?")
        adj_ret = pro.get("adj_return_pct", 0.0)
        composite = int(round(float(pro.get("composite_score", 0)) * 100))
        summary = (
            f"📈 **{name}({code})** Kronos {days} 日预测已完成\n\n"
            f"综合评分 **{'+' if composite >= 0 else ''}{composite}/100** · **{rating}** · "
            f"预期收益 **{'+' if adj_ret >= 0 else ''}{adj_ret:.2f}%**\n\n"
            f"👉 请查看右侧 Artifact 面板 · 含 K 线图 · 综合评分 · 8 因子明细 · 每日预测表 · 可导出 HTML/PDF/发布公开链接"
        )

        elapsed = int(time.time() - t0)

        # ── 持久化 · 让刷新/切换 session 后仍可看到(Sprint H) ──
        _save_kpred_report(
            task_id=task_id,
            user_id=user_id,
            session_id=session_id,
            stock_code=code,
            stock_name=name,
            days=days,
            composite_score=composite,
            rating=rating,
            adj_return_pct=float(adj_ret),
            content_html=html_content,
            summary_md=summary,
            question=question,
            elapsed_sec=elapsed,
        )

        await emit("done", 100, summary, extra={
            "html_content": html_content,
            "stock_code": code,
            "stock_name": name,
            "days": days,
            "composite_score": composite,
            "rating": rating,
            "adj_return_pct": adj_ret,
            "elapsed_sec": elapsed,
            "task_id": task_id,
        })

        logger.info(
            "[chat_kpred:{}] 完成 · user={} · {}({}) · {}天 · {} · {}s",
            task_id, user_id, name, code, days, rating, elapsed,
        )

    except HTTPException as e:
        _refund_rate_limit(user_id)
        logger.warning("[chat_kpred:{}] HTTP {}: {}", task_id, e.status_code, e.detail)
        try:
            await queue.put({"type": "error", "data": {"error": str(e.detail), "status": e.status_code}})
        except Exception:
            pass
    except Exception as e:
        _refund_rate_limit(user_id)
        logger.exception("[chat_kpred:{}] worker 失败 · user={}: {}", task_id, user_id, e)
        try:
            await queue.put({"type": "error", "data": {"error": str(e)}})
        except Exception:
            pass
    finally:
        try:
            await queue.put(None)
        except Exception:
            pass


async def _sse_gen(task_id: str) -> AsyncGenerator[str, None]:
    queue = _TASKS.get(task_id)
    if not queue:
        yield f"event: error\ndata: {json.dumps({'error': 'task_not_found'})}\n\n"
        return

    yield f"event: hello\ndata: {json.dumps({'task_id': task_id})}\n\n"

    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=15.0)
        except asyncio.TimeoutError:
            yield ": heartbeat\n\n"
            continue

        if item is None:
            yield f"event: end\ndata: {json.dumps({'task_id': task_id})}\n\n"
            _TASKS.pop(task_id, None)
            break

        event_type = item.get("type", "progress")
        data = item.get("data", {})
        yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/public/chat_kpred/stream/{task_id}")
async def stream_kpred_public(task_id: str):
    return StreamingResponse(
        _sse_gen(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Endpoint 3 · 拉本 session 历史 kpred 报告(供页面刷新后恢复) ─────

@router.get("/chat/kpred/session_reports")
async def list_session_kpred_reports(request: Request, session_id: str, limit: int = 20):
    """列本 session 下当前用户的所有 kpred 报告 · 按创建时间升序
    前端 ChatWorkspace 加载 session 时调 · 恢复被刷新掉的 kpred assistant 消息
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "需要登录")
    reports = _list_session_kpreds(user_id, session_id, limit=min(limit, 50))
    return {"items": reports, "count": len(reports)}
