"""Chat 多专家辩论 · SKILL 后端

复用 agents/ 里的 7 个角色 · 通过 SSE 分 6 阶段推送进度 · 最终产 markdown 报告
从 chat SKILL 卡 "⚖️ 多专家辩论" 触发 · BFF 拦截 skill_key=debate 后代理到这里

Endpoints:
  POST /api/chat/debate/start                    创建任务 · 返 task_id
  GET  /api/chat/debate/stream/{task_id}         SSE 事件流(匿名 · 走 /public/ 白名单)

流程:
  1. 解析股票查询 → (code, name)
  2. Phase 1 · 并行 · market_analyst + sentinel_news_agent
  3. Phase 2 · 2 轮 bull ⇄ bear 辩论 → comprehensive_judge
  4. Phase 3 · risk_judge 最终裁决
  5. compose_debate_report 组 markdown
  6. emit "done" 事件·前端拿完整 md

Rate limit:单用户 30 min 内最多 3 次 · Redis 计数 · 超限 429
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

# ── agents/ 在 hermes 根目录 · 加 sys.path ────────────────────────
_HERMES_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.realpath(__file__)
))))
if _HERMES_ROOT not in sys.path:
    sys.path.insert(0, _HERMES_ROOT)

from agents.state import EnhancedAgentState, DebateState
from agents.market_analyst import run_market_analyst
from agents.sentinel_news_agent import run_sentinel_news_agent
from agents.bull_researcher import run_bull_researcher
from agents.bear_researcher import run_bear_researcher
from agents.comprehensive_judge import run_comprehensive_judge
from agents.risk_perspectives import (
    run_risk_aggressive, run_risk_neutral, run_risk_conservative,
)
from agents.final_risk_judge import run_final_risk_judge

from app.services.chat_debate.stock_resolver import resolve_stock
from app.services.chat_debate.report_composer import compose_debate_report
from app.services.database import get_conn

_CST = timezone(timedelta(hours=8))
_DEBATE_ROUNDS = 1    # depth 未识别时的兜底 · quick(1) 与前端默认对齐
_RATE_LIMIT_WINDOW_SEC = 30 * 60   # 30 min
_RATE_LIMIT_MAX = 3                # 单用户 30 min 内最多 3 次
_TASK_TTL_SEC = 15 * 60            # 任务队列存活 15 min

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_redis = redis_lib.from_url(_REDIS_URL, decode_responses=True)

router = APIRouter()

# 任务队列 · 与 online_analysis.py 同构 · module-level dict
_TASKS: dict[str, asyncio.Queue] = {}


# ── DB 持久化 ────────────────────────────────────────

def _save_report(
    task_id: str,
    user_id: str,
    session_id: Optional[str],
    stock_code: str,
    stock_name: str,
    decision: str,
    confidence: float,
    content_md: str,
    elapsed_sec: int,
    question: str,
    report_json: dict | None = None,
) -> None:
    """写辩论报告到 DB · 失败仅记 log 不阻塞主流程"""
    try:
        c = get_conn(); cur = c.cursor()
        cur.execute("""
            INSERT INTO chat_debate.reports
              (task_id, user_id, session_id, stock_code, stock_name,
               decision, confidence, content_md, elapsed_sec, question, report_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (task_id) DO NOTHING
        """, (
            task_id, user_id, session_id, stock_code, stock_name,
            decision, round(confidence, 2), content_md, elapsed_sec, question,
            json.dumps(report_json or {}),
        ))
        c.commit(); c.close()
    except Exception as e:
        logger.warning("[chat_debate] 报告持久化失败(非致命): {}", e)


def _list_session_reports(user_id: str, session_id: str, limit: int = 20) -> list[dict]:
    """列本 session 下用户的辩论报告 · 按时间升序 · session 加载时用"""
    try:
        c = get_conn(); cur = c.cursor()
        cur.execute("""
            SELECT task_id, stock_code, stock_name, decision, confidence,
                   content_md, elapsed_sec, question, created_at
            FROM chat_debate.reports
            WHERE user_id = %s AND session_id = %s
            ORDER BY created_at ASC
            LIMIT %s
        """, (user_id, session_id, limit))
        rows = cur.fetchall()
        c.close()
        return [{
            "task_id":     r[0],
            "stock_code":  r[1],
            "stock_name":  r[2],
            "decision":    r[3],
            "confidence":  float(r[4]),
            "content_md":  r[5],
            "elapsed_sec": r[6],
            "question":    r[7],
            "created_at":  r[8].isoformat() if r[8] else None,
        } for r in rows]
    except Exception as e:
        logger.warning("[chat_debate] 拉 session 报告失败: {}", e)
        return []


def _get_report_by_task(task_id: str, user_id: str) -> dict | None:
    """按 task_id 拉单条 · 权限校验:必须是本人的报告"""
    try:
        c = get_conn(); cur = c.cursor()
        cur.execute("""
            SELECT task_id, user_id, session_id, stock_code, stock_name,
                   decision, confidence, content_md, elapsed_sec, question, created_at
            FROM chat_debate.reports
            WHERE task_id = %s
        """, (task_id,))
        r = cur.fetchone()
        c.close()
        if not r:
            return None
        if r[1] != user_id:
            return None
        return {
            "task_id":     r[0],
            "session_id":  r[2],
            "stock_code":  r[3],
            "stock_name":  r[4],
            "decision":    r[5],
            "confidence":  float(r[6]),
            "content_md":  r[7],
            "elapsed_sec": r[8],
            "question":    r[9],
            "created_at":  r[10].isoformat() if r[10] else None,
        }
    except Exception as e:
        logger.warning("[chat_debate] 拉报告失败: {}", e)
        return None


# ── 请求/响应模型 ────────────────────────────────────────

class DebateStartIn(BaseModel):
    stock_query: str = Field(..., min_length=1, max_length=100,
                             description="用户输入的股票查询串 · '腾讯' / '600519' / '贵州茅台'")
    question: str = Field("", max_length=500,
                          description="用户原始问题 · 用作报告开头引言")
    session_id: Optional[str] = Field(None, description="opencode session id · 可空")
    message_id: Optional[str] = Field(None, description="opencode message id · 供 Artifact publish 关联")
    depth: str = Field("normal", description="辩论深度 · quick=1轮 / normal=2轮 / deep=3轮")


_DEPTH_ROUNDS = {"quick": 1, "normal": 2, "deep": 3}


class DebateStartResp(BaseModel):
    task_id: str
    stream_url: str
    stock_code: str
    stock_name: str


# ── Rate limit ──────────────────────────────────────

def _check_rate_limit(user_id: str) -> tuple[bool, int]:
    """
    Returns:
        (allowed, seconds_until_reset)
    """
    key = f"chat_debate:ratelimit:{user_id}"
    try:
        count = _redis.incr(key)
        if count == 1:
            _redis.expire(key, _RATE_LIMIT_WINDOW_SEC)
        if count > _RATE_LIMIT_MAX:
            ttl = _redis.ttl(key)
            return False, max(1, ttl if ttl and ttl > 0 else _RATE_LIMIT_WINDOW_SEC)
        return True, 0
    except Exception as e:
        logger.warning("[chat_debate] rate limit redis 异常 · 放行: {}", e)
        return True, 0


def _refund_rate_limit(user_id: str) -> None:
    """崩了不扣 · 回滚 rate limit 计数(按建议方案·失败不消耗配额)"""
    key = f"chat_debate:ratelimit:{user_id}"
    try:
        _redis.decr(key)
    except Exception as e:
        logger.warning("[chat_debate] rate limit refund 异常: {}", e)


# ── Endpoint 1 · 启动辩论 ─────────────────────────────

@router.post("/chat/debate/start", response_model=DebateStartResp)
async def start_debate(payload: DebateStartIn, request: Request):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "需要登录")

    allowed, reset_sec = _check_rate_limit(user_id)
    if not allowed:
        raise HTTPException(
            429,
            f"多专家辩论 30 分钟内最多 3 次 · 请 {reset_sec // 60} 分 {reset_sec % 60} 秒后再试"
        )

    # 股票解析在启动前做 · 早失败早退款
    try:
        code, name = await resolve_stock(payload.stock_query)
    except HTTPException as e:
        _refund_rate_limit(user_id)
        raise

    task_id = f"dbg_{uuid.uuid4().hex[:12]}"
    queue: asyncio.Queue = asyncio.Queue(maxsize=300)
    _TASKS[task_id] = queue

    debate_rounds = _DEPTH_ROUNDS.get(payload.depth, _DEBATE_ROUNDS)

    asyncio.create_task(_worker(
        task_id=task_id,
        user_id=user_id,
        code=code,
        name=name,
        question=payload.question,
        session_id=payload.session_id,
        debate_rounds=debate_rounds,
        queue=queue,
    ))

    return DebateStartResp(
        task_id=task_id,
        stream_url=f"/api/chat/debate/stream/{task_id}",
        stock_code=code,
        stock_name=name,
    )


# ── worker · 复用 agents/ 7 角色 · 6 阶段 emit ─────────────

async def _worker(
    task_id: str,
    user_id: str,
    code: str,
    name: str,
    question: str,
    session_id: Optional[str],
    debate_rounds: int,
    queue: asyncio.Queue,
) -> None:
    t0 = time.time()
    session_id_for_save = session_id   # 供 finally 段和保存段使用

    async def emit(phase: str, pct: int, text: str = "", extra: dict | None = None) -> None:
        payload = {"phase": phase, "pct": pct, "text": text}
        if extra:
            payload.update(extra)
        try:
            await queue.put({"type": "progress", "data": payload})
        except Exception:
            pass

    try:
        # ── 正规化 ticker · agents/ 需 "600519.SH" 格式 ──
        ticker = code
        if "." not in ticker:
            if ticker.startswith("6"):
                ticker = f"{ticker}.SH"
            elif ticker.startswith(("0", "3")):
                ticker = f"{ticker}.SZ"
            elif ticker.startswith(("4", "8", "9")):
                ticker = f"{ticker}.BJ"

        trade_date = datetime.now(_CST).strftime("%Y-%m-%d")
        state = EnhancedAgentState(
            ticker=ticker,
            stock_name=name,
            trade_date=trade_date,
            change_pct=0.0,
            trigger_desc=question or f"用户在 chat 里主动请求对 {name} 做多空辩论",
            thesis_text="",
            kill_conditions=[],
        )

        # ── Phase 1 · 并行 · 技术面 + 新闻情报 ─────────────
        await emit("technical", 5, f"启动 {name}({code}) 多专家辩论 · 6 位分析师就绪")
        await emit("technical", 15, f"[1/6] 技术面分析师 · 正在拉取 {name} K 线 + 指标...")

        market_task = run_market_analyst(ticker, name, 0.0, trade_date)
        sentinel_task = run_sentinel_news_agent(ticker, name, 0.0, "", [])

        market_report, sentinel_result = await asyncio.gather(
            market_task, sentinel_task, return_exceptions=True
        )

        if isinstance(market_report, Exception):
            logger.warning("[chat_debate:{}] market_analyst 失败: {}", task_id, market_report)
            market_report = f"{name} 技术数据获取失败 · 已使用默认判断"
        if isinstance(sentinel_result, Exception):
            logger.warning("[chat_debate:{}] sentinel 失败: {}", task_id, sentinel_result)
            sentinel_result = {
                "sentinel_report": "新闻情报暂不可用", "sentinel_opinion": "中性",
                "sentinel_confidence": 0.0, "verified_facts": [], "filtered_facts": [],
                "kill_condition_triggered": False, "kill_condition_desc": "", "debate_mode": "normal",
            }

        state.market_report = market_report
        state.sentinel_report = sentinel_result["sentinel_report"]
        state.sentinel_opinion = sentinel_result["sentinel_opinion"]
        state.sentinel_confidence = sentinel_result["sentinel_confidence"]
        state.verified_facts = sentinel_result["verified_facts"]
        state.filtered_facts = sentinel_result["filtered_facts"]
        state.debate_mode = "normal"

        await emit("news", 30,
                   f"[2/6] Sentinel 新闻官 · 完成 5 层反投毒过滤 · 综合研判 = {state.sentinel_opinion}")

        # ── Phase 2 · N 轮多空辩论 · depth 决定轮数 ─────
        debate = DebateState()
        for rn in range(debate_rounds):
            base_pct = 30 + int(rn * 40 / max(debate_rounds, 1))
            await emit("bull", base_pct + 5,
                       f"[3/8] 多头研究员 · 第 {rn + 1}/{debate_rounds} 轮论证中...")
            debate = await asyncio.to_thread(run_bull_researcher, state, debate)

            await emit("bear", base_pct + 10,
                       f"[4/8] 空头研究员 · 第 {rn + 1}/{debate_rounds} 轮反驳中...")
            debate = await asyncio.to_thread(run_bear_researcher, state, debate)

        state.debate_state = debate

        # ── Phase 3 · 综合裁决 (Deep Think · Sprint B1) ─────
        await emit("judge", 70, "[5/6] 综合判官 · Deep Think 深度权衡多空...")
        judgment = await asyncio.to_thread(run_comprehensive_judge, state)

        # ── Phase 4 · 3 方风控辩论 (Sprint B3) ─────────
        await emit("risk", 78, "[6/8] 风控·激进派意见中...")
        risk_agg = await asyncio.to_thread(run_risk_aggressive, state, judgment)

        await emit("risk", 84, "[7/8] 风控·中性派意见中...")
        risk_neu = await asyncio.to_thread(
            run_risk_neutral, state, judgment,
            prior_debate=f"【激进派】{risk_agg}"
        )

        await emit("risk", 90, "[8/8] 风控·保守派意见中...")
        risk_con = await asyncio.to_thread(
            run_risk_conservative, state, judgment,
            prior_debate=f"【激进派】{risk_agg}\n\n【中性派】{risk_neu}"
        )

        # ── 风控最终裁决 · Deep Think ─────────────
        await emit("risk", 96, "风控委员会综合裁决中...")
        final = await asyncio.to_thread(
            run_final_risk_judge, state, judgment,
            {"aggressive": risk_agg, "neutral": risk_neu, "conservative": risk_con},
        )

        # ── 组 markdown ────────────────────────────
        md = compose_debate_report(state, judgment, final, user_question=question)

        elapsed = int(time.time() - t0)
        decision_str = final.get("decision", "HOLD")
        confidence_val = float(final.get("confidence", 0.5))

        # ── 持久化 · 让刷新页面后仍能看到 ──
        _save_report(
            task_id=task_id,
            user_id=user_id,
            session_id=session_id_for_save,
            stock_code=code,
            stock_name=name,
            decision=decision_str,
            confidence=confidence_val,
            content_md=md,
            elapsed_sec=elapsed,
            question=question,
            report_json={
                "sentinel_opinion": state.sentinel_opinion,
                "sentinel_confidence": state.sentinel_confidence,
                "debate_rounds": _DEBATE_ROUNDS,
                "sentinel_conflicts": final.get("sentinel_conflicts", False),
            },
        )

        await emit("done", 100, md, extra={
            "decision": decision_str,
            "confidence": round(confidence_val, 2),
            "elapsed_sec": elapsed,
            "stock_code": code,
            "stock_name": name,
            "task_id": task_id,   # 前端要 · 用作 sourceMessageId
        })

        logger.info(
            "[chat_debate:{}] 完成 · user={} · {}({}) · {} · {}%置信 · {}s",
            task_id, user_id, name, code,
            final.get("decision"), int(round(float(final.get("confidence", 0.5)) * 100)),
            elapsed,
        )

    except Exception as e:
        # 崩了退回 rate limit 配额 · 按建议方案
        _refund_rate_limit(user_id)
        logger.exception("[chat_debate:{}] worker 失败 · user={}: {}", task_id, user_id, e)
        try:
            await queue.put({"type": "error", "data": {"error": str(e)}})
        except Exception:
            pass
    finally:
        # 结束标记 · SSE gen 见 None 就 break
        try:
            await queue.put(None)
        except Exception:
            pass


# ── Endpoint 2 · SSE 流(公开 · 走 /api/public 白名单) ─────────

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


@router.get("/public/chat_debate/stream/{task_id}")
async def stream_debate_public(task_id: str):
    """SSE 流·匿名可访问·走 /api/public 白名单
    task_id 是随机 12 hex · 不可枚举 · 只有创建方拿到"""
    return StreamingResponse(
        _sse_gen(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Endpoint 3 · 拉本 session 历史辩论(供页面刷新后恢复) ─────

@router.get("/chat/debate/session_reports")
async def list_session_reports(request: Request, session_id: str, limit: int = 20):
    """列本 session 下当前用户的所有辩论报告 · 按创建时间升序
    前端 ChatWorkspace 加载 session 时调 · 恢复被刷新掉的辩论 assistant 消息
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "需要登录")
    reports = _list_session_reports(user_id, session_id, limit=min(limit, 50))
    return {"items": reports, "count": len(reports)}


# ── Endpoint 4 · 拉单条(备用 · 若前端跨 session 想引用) ─────

@router.get("/chat/debate/report/{task_id}")
async def get_single_report(task_id: str, request: Request):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "需要登录")
    r = _get_report_by_task(task_id, user_id)
    if not r:
        raise HTTPException(404, "报告不存在或无权访问")
    return r
