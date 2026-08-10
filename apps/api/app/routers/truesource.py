"""
TrueSource 数据代理路由
将 Hunter 前端请求转发到 TrueSource API (truesource.agentpit.io:8000)

端点：
  GET /api/truesource/daily-brief?symbols=300308,688041
      → 副驾每日简报（信号摘要 + 预警级别）
  GET /api/truesource/alert-signals?symbols=300308,688041
      → 推送触发判断（供 push_scheduler 调用）
  GET /api/truesource/report/{symbol}
      → 单只标的完整研究报告（含真相提示）
  GET /api/truesource/procurement?days=7&limit=15
      → 最新政采中标信号（发现 Tab 使用）
  GET /api/truesource/macro?days=30
      → 宏观信号（国统局+海关+行业协会）
"""
import os
import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger

router = APIRouter()

TRUESOURCE_BASE = os.environ.get("TRUESOURCE_API_URL", "http://34.92.72.140:8000")
_TIMEOUT = 15.0


async def _proxy_post(path: str, params: dict | None = None, timeout: float = 70.0) -> dict:
    """POST 转发到 TrueSource，供主动采集类接口使用（耗时 30-60s）。"""
    url = f"{TRUESOURCE_BASE}{path}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=timeout, write=5.0, pool=5.0)) as client:
            r = await client.post(url, params=params or {})
            r.raise_for_status()
            return r.json()
    except httpx.TimeoutException:
        logger.warning("TrueSource POST 请求超时: {}", url)
        raise HTTPException(504, "TrueSource 采集超时，Gemini 搜索耗时较长，请稍后重试")
    except httpx.HTTPStatusError as e:
        logger.warning("TrueSource POST 返回错误 {}: {}", e.response.status_code, url)
        raise HTTPException(e.response.status_code, f"TrueSource 错误: {e.response.text[:200]}")
    except Exception as e:
        logger.error("TrueSource POST 代理失败: {} — {}", url, e)
        raise HTTPException(503, "TrueSource 数据源暂时不可用")


async def _proxy(path: str, params: dict | None = None) -> dict:
    """转发请求到 TrueSource，统一错误处理。"""
    url = f"{TRUESOURCE_BASE}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, params=params or {})
            r.raise_for_status()
            return r.json()
    except httpx.TimeoutException:
        logger.warning("TrueSource 请求超时: {}", url)
        raise HTTPException(504, "TrueSource 数据源超时，请稍后重试")
    except httpx.HTTPStatusError as e:
        logger.warning("TrueSource 返回错误 {}: {}", e.response.status_code, url)
        raise HTTPException(e.response.status_code, f"TrueSource 错误: {e.response.text[:200]}")
    except Exception as e:
        logger.error("TrueSource 代理失败: {} — {}", url, e)
        raise HTTPException(503, "TrueSource 数据源暂时不可用")


@router.get("/truesource/daily-brief")
async def daily_brief(
    request: Request,
    symbols: str = Query(..., description="逗号分隔的股票代码，如 300308,688041"),
):
    """
    副驾每日简报：指定标的的信号摘要 + 预警级别（最近3天）。
    alert_level: red / yellow / green / grey
    """
    return await _proxy("/api/hunter/daily-brief", {"symbols": symbols})


@router.get("/truesource/alert-signals")
async def alert_signals(
    request: Request,
    symbols: str = Query(..., description="逗号分隔的股票代码"),
):
    """
    推送触发判断：达到预警阈值的信号（最近26小时）。
    供 push_scheduler 使用，判断哪些股票需要发微信推送。
    """
    return await _proxy("/api/hunter/alert-signals", {"symbols": symbols})


def _scout_to_report(scout: dict) -> dict:
    """把 scout 结构转换为 report 兼容结构，让前端复用现有展示。"""
    sources = scout.get("sources", {}) or {}
    price_info = sources.get("price", {}) or {}
    announcements = sources.get("announcements", []) or []
    rd_exp = sources.get("rd_expansion", []) or []
    ai_search = sources.get("ai_search", []) or []
    northbound = sources.get("northbound", []) or []

    # recent_signals：合并各源、按日期倒序取前 12 条（前端 UI 主要展示）
    merged = [*ai_search, *rd_exp, *announcements, *northbound]
    recent = sorted(merged, key=lambda s: s.get("date") or "", reverse=True)[:12]

    total = scout.get("total_signals", len(merged))
    return {
        "symbol":          scout.get("symbol", ""),
        "name":            scout.get("name", ""),
        "sector":          "",
        "chain":           "",
        "price":           scout.get("price"),
        "change_pct":      scout.get("change_pct"),
        "week_change_pct": price_info.get("week_change_pct"),
        "conclusion":      f"实时采集到 {total} 条一手信号，覆盖公告、研发扩张、北向持仓、AI 搜索等维度，供你参考。",
        "signals": {
            "cninfo":       announcements,
            "rd_expansion": rd_exp,
            "northbound":   northbound,
            "ai_search":    ai_search,
        },
        "recent_signals":  recent,
        "truth_alert":     None,
        "report_md":       "",
        "fetched_at":      scout.get("fetched_at"),
        "scout_mode":      True,
        "summary":         scout.get("summary"),
    }


@router.get("/truesource/report/{symbol}")
async def get_report(
    symbol: str,
    request: Request,
    name: str | None = Query(None, description="股票名称，scout 兜底时传入"),
):
    """
    单只标的完整研究报告（含真相提示、分类信号、AI 摘要）。
    - 优先走预制 report（35 只 AI 算力标的，秒级）
    - 若 404 → fallback 到 scout（任意 A 股，30-60s 实时采集）
    """
    try:
        return await _proxy(f"/api/hunter/report/{symbol}")
    except HTTPException as e:
        if e.status_code != 404:
            raise
        logger.info("report 未覆盖 {}, 走 scout 实时采集", symbol)
        scout = await _proxy_post(f"/api/hunter/scout/{symbol}",
                                  {"name": name} if name else None,
                                  timeout=90.0)
        return _scout_to_report(scout)


@router.get("/truesource/procurement")
async def procurement(
    request: Request,
    days: int = Query(7, description="查询最近N天"),
    limit: int = Query(15, description="最多返回条数"),
):
    """
    最新政采中标信号，供"发现"Tab 展示实时产业链动态。
    """
    return await _proxy("/api/procurement", {"days": days, "limit": limit})


@router.post("/truesource/scout/{symbol}")
async def scout_stock(symbol: str, request: Request, name: str = Query(None, description="股票名称（可选）")):
    """
    主动全量采集：对指定股票并行运行价格 + 公告 + Gemini AI 搜索爬虫。
    约 30-60s，前端需设置足够长的超时与加载提示。
    """
    return await _proxy_post(f"/api/hunter/scout/{symbol}", {"name": name} if name else None)


@router.get("/truesource/macro")
async def macro(
    request: Request,
    days: int = Query(30, description="查询最近N天"),
):
    """
    最新宏观信号（国统局+海关+行业协会），供"发现"Tab 宏观区块使用。
    """
    return await _proxy("/api/macro", {"days": days})
