"""在线分析 · 多智能体综合分析引擎

GET  /api/online-analysis/search-stock      (公开)
POST /api/online-analysis/start             ← 触发分阶段 PriceAlertGraph，SSE 推送进度
GET  /api/online-analysis/stream/{task_id}  (公开，EventSource 不支持 header)
GET  /api/online-analysis/reports
GET  /api/online-analysis/reports/{id}
"""
import asyncio
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import AsyncGenerator, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel

# ── agents/ 在 hermes 根目录，加到 sys.path ──────────────────────────
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
from agents.risk_judge import run_risk_judge
from agents.sentinel.stock_search import search as stock_search

from app.services.online_analysis.thesis_gen import generate_thesis
from app.services.online_analysis.kill_condition_gen import generate_kill_conditions

from app.services.finance_data_client import (
    subscribe as fd_subscribe,
    get_kline as fd_get_kline,
    save_analysis_report,
    list_analysis_reports,
    get_analysis_report,
)
from app.services.database import get_user_preference, save_stock_memory, update_user_portrait

_CST = timezone(timedelta(hours=8))
_DEBATE_ROUNDS = 2

router = APIRouter()

_TASKS:   dict[str, asyncio.Queue] = {}
_RESULTS: dict[str, dict]          = {}


# ─── 数据模型 ─────────────────────────────────────────────────────────

class GenerateThesisIn(BaseModel):
    stock_code: str
    stock_name: str

class StartAnalysisIn(BaseModel):
    stock_code:      str
    stock_name:      str
    change_pct:      float      = 0.0
    trigger_desc:    str        = "手动在线分析"
    thesis:          str        = ""
    kill_conditions: List[str]  = []


# ─── Endpoint 0a: AI 生成看好理由 + 卖出红线 ─────────────────────────

@router.post("/online-analysis/generate-thesis-kills")
async def generate_thesis_kills(payload: GenerateThesisIn):
    """股票确认后自动生成看好理由和卖出红线建议（供用户确认/修改后再启动分析）"""
    thesis_result = await generate_thesis(payload.stock_code, payload.stock_name)
    thesis = thesis_result.get("thesis", "")

    kc_result = await generate_kill_conditions(payload.stock_name, thesis) if thesis else {"suggestions": [], "error": "no_thesis"}
    kill_conditions = [s["text"] for s in kc_result.get("suggestions", []) if s.get("text")]

    return {
        "thesis":          thesis,
        "kill_conditions": kill_conditions,
        "error":           thesis_result.get("error") or kc_result.get("error"),
    }


# ─── Endpoint 0b: 股票模糊搜索（公开）───────────────────────────────

@router.get("/online-analysis/search-stock")
async def search_stock(
    q:     str = Query(..., min_length=1),
    limit: int = Query(10, le=30),
):
    items = await stock_search(q, limit=limit)
    return {"items": items, "count": len(items), "query": q}


# ── check-stock：显式检查按钮用；命中 A 股返回 matches，识别港/美股则硬拦截 ────

_US_ALPHA_RE = re.compile(r"^[A-Za-z]{1,5}$")
_HK_NUM_RE   = re.compile(r"^\d{4,5}$")
_KNOWN_US_HK_ALIASES: dict[str, str] = {
    # 常见美股（中文别名）
    "苹果": "US", "特斯拉": "US", "英伟达": "US", "谷歌": "US", "微软": "US",
    "亚马逊": "US", "脸书": "US", "meta": "US", "奈飞": "US", "台积电": "US",
    # 常见港股
    "腾讯": "HK", "阿里": "HK", "阿里巴巴": "HK", "美团": "HK", "小米": "HK",
    "京东": "HK", "网易": "HK", "百度": "HK", "字节跳动": "HK", "字节": "HK",
    "快手": "HK", "比亚迪": "HK",
}


def _detect_non_a_market(q: str) -> str | None:
    """启发式识别非 A 股。返回 'HK' / 'US' / None。"""
    q_stripped = q.strip()
    q_upper = q_stripped.upper()
    if q_upper.endswith(".HK"):
        return "HK"
    if q_upper.endswith(".US"):
        return "US"
    # 中文/英文别名（子串匹配）
    q_lower = q_stripped.lower()
    for alias, mkt in _KNOWN_US_HK_ALIASES.items():
        if alias in q_stripped or alias.lower() in q_lower:
            return mkt
    # 4-5 位纯数字：港股常见（0700 / 00700 / 09988）
    if _HK_NUM_RE.match(q_stripped):
        return "HK"
    # 全字母 1-5 位：美股 ticker（AAPL / TSLA / MSFT）
    if _US_ALPHA_RE.match(q_stripped):
        return "US"
    return None


@router.get("/online-analysis/check-stock")
async def check_stock(q: str = Query(..., min_length=1)):
    """检查用户输入的股票：
    - 命中 A 股 catalog → {ok: True, matches: [...]}
    - 疑似港/美股 → {ok: False, reason: 'unsupported_market', detected_market: 'HK'|'US'}
    - 完全未识别 → {ok: False, reason: 'not_found'}
    """
    q = q.strip()
    items = await stock_search(q, limit=8)
    if items:
        return {"ok": True, "matches": items, "count": len(items), "query": q}
    market = _detect_non_a_market(q)
    if market:
        market_cn = {"HK": "港股", "US": "美股"}[market]
        return {
            "ok": False, "reason": "unsupported_market",
            "detected_market": market, "query": q,
            "message": f"「{q}」看起来是{market_cn}，本活动分析目前仅支持 A 股，"
                       "请输入沪深两市股票代码（如 600519）或中文名（如 贵州茅台）",
        }
    return {
        "ok": False, "reason": "not_found", "query": q,
        "message": "未识别到该股票，请换个关键词试试（如「中际」「600519」）",
    }


# ─── Endpoint 1: 启动分析 ─────────────────────────────────────────────

async def _fetch_today_change_pct(code: str) -> float | None:
    """从 akshare 取最近 1 个交易日涨跌幅（非自选股用）"""
    def _blocking():
        try:
            import akshare as ak
            df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="")
            if df is None or df.empty:
                return None
            last = df.iloc[-1]
            cols = {str(c) for c in df.columns}
            for col in ("涨跌幅", "pct_chg"):
                if col in cols:
                    return float(last[col])
        except Exception as e:
            logger.warning("_fetch_today_change_pct({}) failed: {}", code, e)
        return None
    return await asyncio.to_thread(_blocking)


@router.post("/online-analysis/start")
async def start_analysis(request: Request, payload: StartAnalysisIn):
    user_id = getattr(request.state, "user_id", "anonymous")
    task_id = f"ag_{uuid.uuid4().hex[:12]}"
    queue: asyncio.Queue = asyncio.Queue(maxsize=300)
    _TASKS[task_id] = queue

    async def emit(event_type: str, data: dict) -> None:
        try:
            await queue.put({"type": event_type, "data": data})
        except Exception:
            pass

    async def _worker():
        t0 = time.time()
        name       = payload.stock_name
        trade_date = datetime.now(_CST).strftime("%Y-%m-%d")

        # 加载用户偏好记忆 + 本股历史分析（供前端展示上下文）
        user_pref = {}
        prev_analysis = None
        try:
            user_pref = await asyncio.to_thread(get_user_preference, user_id)
            history = await asyncio.to_thread(
                list_analysis_reports, 5, 0, payload.stock_code
            )
            if history:
                prev_analysis = history[0]
        except Exception as e:
            logger.warning("load memory context failed (non-fatal): {}", e)

        await emit("memory_context", {
            "user_preference": user_pref,
            "prev_analysis":   prev_analysis,
        })

        # normalize ticker 放最前，确保 exchange 正确
        ticker = payload.stock_code
        if "." not in ticker:
            if ticker.startswith(("6", "8")):
                ticker = f"{ticker}.SH"
            elif ticker.startswith(("0", "3")):
                ticker = f"{ticker}.SZ"

        code_6   = ticker.split(".")[0]
        exchange = ticker.split(".")[1] if "." in ticker else "SH"

        # 订阅 finance-data（非阻塞，exchange 已正确）
        try:
            await asyncio.to_thread(
                fd_subscribe,
                code=code_6, name=name,
                market="HK" if exchange == "HK" else "US" if exchange == "US" else "A",
                exchange=exchange, asset_type="stock",
            )
        except Exception as e:
            logger.warning("fd_subscribe non-fatal: {}", e)

        try:
            # ── Phase 1 ──────────────────────────────────────────────
            await emit("progress", {"phase": "phase1", "text": f"Phase 1 · 正在并行分析 {name} 技术面与新闻情报..."})

            # 从 finance-data 预取 K 线；非自选股同时兜底获取实时涨跌幅
            prefetched_kline: str | None = None
            actual_change_pct = payload.change_pct
            try:
                kline_rows = await asyncio.to_thread(fd_get_kline, ticker, "daily", 20)
                if kline_rows:
                    lines = [
                        f"{r['ts']}: 收盘={r['close']:.2f} 成交量={r['volume']}"
                        for r in kline_rows
                    ]
                    prefetched_kline = "\n".join(lines)
                else:
                    # 非自选股：akshare 兜底获取今日涨跌幅（仅在用户未手动填写时）
                    if payload.change_pct == 0.0:
                        pct = await _fetch_today_change_pct(code_6)
                        if pct is not None:
                            actual_change_pct = pct
                            logger.info("非自选股 {} 实时涨跌幅 akshare: {:.2f}%", code_6, pct)
            except Exception as e:
                logger.warning("fd_get_kline failed, will fallback to akshare: {}", e)

            # 把用户偏好和自动画像以结构化多行块注入，让 Agent 能明确参考
            trigger_with_pref = payload.trigger_desc
            portrait = user_pref.get("extra", {}).get("auto_portrait", {}) if user_pref else {}
            portrait_lines = []
            if user_pref:
                if user_pref.get("investment_style"):
                    portrait_lines.append(f"  · 投资风格：{user_pref['investment_style']}")
                if user_pref.get("risk_tolerance"):
                    portrait_lines.append(f"  · 风险偏好：{user_pref['risk_tolerance']}")
                if user_pref.get("holding_period"):
                    portrait_lines.append(f"  · 持仓周期：{user_pref['holding_period']}")
            if portrait.get("tendency"):
                portrait_lines.append(
                    f"  · 历史倾向：{portrait['tendency']}"
                    f"（买入 {portrait.get('buy_count', 0)} / 持有 {portrait.get('hold_count', 0)}"
                    f" / 卖出 {portrait.get('sell_count', 0)} 次）"
                )
            if portrait.get("top_stocks"):
                tops = "、".join(
                    f"{s['name']}({s['count']}次)" for s in portrait["top_stocks"][:3]
                )
                portrait_lines.append(f"  · 近期最关注：{tops}")
            if portrait_lines:
                trigger_with_pref = (
                    payload.trigger_desc
                    + "\n\n【用户画像 — 请据此调整分析侧重点和结论表述】\n"
                    + "\n".join(portrait_lines)
                )

            state = EnhancedAgentState(
                ticker=ticker, stock_name=name, trade_date=trade_date,
                change_pct=actual_change_pct, trigger_desc=trigger_with_pref,
            )

            user_kills = [{"text": k, "type": "用户设置", "source": "user"}
                         for k in (payload.kill_conditions or []) if k.strip()]
            market_task   = run_market_analyst(ticker, name, actual_change_pct, trade_date, prefetched_kline)
            sentinel_task = run_sentinel_news_agent(ticker, name, actual_change_pct,
                                                    payload.thesis or "", user_kills)
            market_report, sentinel_result = await asyncio.gather(
                market_task, sentinel_task, return_exceptions=True
            )

            if isinstance(market_report, Exception):
                logger.warning("market_analyst failed: {}", market_report)
                market_report = f"{name} 技术数据获取失败"
            if isinstance(sentinel_result, Exception):
                logger.warning("sentinel failed: {}", sentinel_result)
                sentinel_result = {
                    "sentinel_report": "新闻情报暂不可用", "sentinel_opinion": "中性",
                    "sentinel_confidence": 0.0, "verified_facts": [], "filtered_facts": [],
                    "kill_condition_triggered": False, "kill_condition_desc": "", "debate_mode": "normal",
                }

            state.market_report            = market_report
            state.sentinel_report          = sentinel_result["sentinel_report"]
            state.sentinel_opinion         = sentinel_result["sentinel_opinion"]
            state.sentinel_confidence      = sentinel_result["sentinel_confidence"]
            state.verified_facts           = sentinel_result["verified_facts"]
            state.filtered_facts           = sentinel_result["filtered_facts"]
            state.kill_condition_triggered = sentinel_result["kill_condition_triggered"]
            state.kill_condition_desc      = sentinel_result["kill_condition_desc"]
            state.debate_mode              = sentinel_result["debate_mode"]

            await emit("progress", {
                "phase": "phase1_done",
                "text": f"Phase 1 完成 · Sentinel={state.sentinel_opinion} {state.sentinel_confidence:.0%}",
                "market_preview": (market_report[:200] + "…") if len(market_report) > 200 else market_report,
                "sentinel_preview": (state.sentinel_report[:200] + "…") if len(state.sentinel_report) > 200 else state.sentinel_report,
            })

            # ── Phase 2 ──────────────────────────────────────────────
            await emit("progress", {"phase": "phase2", "text": f"Phase 2 · Bull/Bear {_DEBATE_ROUNDS} 轮辩论进行中..."})

            debate = DebateState()
            for rn in range(_DEBATE_ROUNDS):
                debate = await asyncio.to_thread(run_bull_researcher, state, debate)
                debate = await asyncio.to_thread(run_bear_researcher, state, debate)
                await emit("progress", {
                    "phase": "phase2",
                    "text": f"Phase 2 · 第 {rn + 1}/{_DEBATE_ROUNDS} 轮辩论完成",
                })
            state.debate_state = debate

            judgment = await asyncio.to_thread(run_comprehensive_judge, state)
            await emit("progress", {
                "phase": "phase2_done",
                "text": f"Phase 2 完成 · 初步裁判 = {judgment.get('decision', '?')}",
            })

            # ── Phase 3 ──────────────────────────────────────────────
            await emit("progress", {"phase": "phase3", "text": "Phase 3 · 风险修正与综合裁判..."})
            final = await asyncio.to_thread(run_risk_judge, state, judgment)

            # ── 保存到 finance-data DB ────────────────────────────────
            try:
                report_id = await asyncio.to_thread(save_analysis_report, {
                    "stock_code":       ticker,
                    "stock_name":       name,
                    "thesis_status":    final.get("decision", "HOLD"),
                    "confidence":       final.get("confidence"),
                    "final_conclusion": final,
                    "duration_ms":      int((time.time() - t0) * 1000),
                })
                final["report_id"] = report_id
            except Exception as e:
                logger.warning("save_analysis_report failed: {}", e)

            _RESULTS[task_id] = final
            await emit("complete", final)

            # 后台写记忆（非阻塞，失败不影响主流程）
            async def _save_memory():
                try:
                    conclusion = final.get("decision") or final.get("thesis_status", "HOLD")
                    confidence = final.get("confidence")
                    await asyncio.to_thread(
                        save_stock_memory,
                        user_id, ticker, name, "analysis",
                        conclusion, confidence, None,
                    )
                    await asyncio.to_thread(update_user_portrait, user_id)
                except Exception as me:
                    logger.warning("memory save failed (non-fatal): {}", me)
            asyncio.create_task(_save_memory())

        except Exception as e:
            logger.exception("agent worker failed: {}", e)
            await emit("error", {"error": str(e)})
        finally:
            await queue.put(None)

    asyncio.create_task(_worker())
    return {"task_id": task_id, "stream_url": f"/api/online-analysis/stream/{task_id}"}


# ─── Endpoint 2: SSE 流（公开，心跳 15s 防 Nginx 60s 超时）────────────

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

        yield f"event: {item['type']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n"


@router.get("/online-analysis/stream/{task_id}")
async def stream_analysis(task_id: str):
    return StreamingResponse(
        _sse_gen(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":        "keep-alive",
        },
    )


# ─── Endpoint 3: 历史列表 ─────────────────────────────────────────────

@router.get("/online-analysis/reports")
async def list_reports(
    limit:      int           = Query(20, le=200),
    offset:     int           = Query(0, ge=0),
    stock_code: Optional[str] = None,
):
    items = list_analysis_reports(limit=limit, offset=offset, stock_code=stock_code)
    return {"items": items, "count": len(items)}


@router.get("/online-analysis/reports/{report_id}")
async def get_report(report_id: int):
    report = get_analysis_report(report_id)
    if not report:
        raise HTTPException(404, "report not found")
    return report
