"""quant_predict sub-agent · 量化择时

复用 hermes 现有 kpred pro 逻辑（Kronos + 8 因子）。为避免绕 HTTP，
直接 in-process 调用 factor_engine 里的核心函数；Kronos 通过 HTTP POST 触发。
"""
from __future__ import annotations
import os
import time
from typing import Optional
import httpx
from loguru import logger

from app.services.agent.tool_registry import ToolCall, ToolRegistry, ToolResult


_DEF = {
    "name": "quant_predict",
    "description": "量化择时：Kronos ML + 8 因子加权预测未来 5/10/20 日走势，输出趋势方向、幅度、5 档评级。",
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "6 位 A 股代码"},
            "horizon": {"type": "integer", "default": 10, "minimum": 1, "maximum": 30,
                         "description": "预测天数，默认 10"},
        },
        "required": ["code"],
    },
}


_KRONOS_URL = os.getenv("KRONOS_URL", "http://127.0.0.1:8890")


async def invoke_quant_predict(code: str, horizon: int = 10) -> tuple[dict, Optional[dict]]:
    """
    Returns:
        summary = {
          "trend": "up|down|flat",
          "pct_predicted": float,   # 预测涨跌幅 %
          "rating": str,             # 5 档星级
          "horizon_days": int,
          "factors": {factor_name: score},
          "last_close": float,
        }
        detail_ref = {"type": "kpred", "code": code}
    """
    try:
        from app.services.factor_engine import compute_pro_prediction
    except Exception as e:
        logger.warning("[quant_agent] factor_engine 导入失败: {}", e)
        return _degraded(code, "factor_engine 不可用"), None

    # Step 1: 调 Kronos
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=3.0, read=25.0, write=5.0, pool=3.0)
        ) as client:
            r = await client.post(
                f"{_KRONOS_URL}/predict",
                json={"symbol": code, "pred_len": horizon},
            )
        if r.status_code != 200:
            body_text = r.text[:200]
            if "未找到" in body_text and ("K 线" in body_text or "K线" in body_text):
                return _degraded(code, "Kronos 未收录（可能是新股/停牌）"), _dref(code)
            logger.warning("[quant_agent] Kronos 非 200 code={}: {}", code, body_text)
            return _degraded(code, f"Kronos {r.status_code}"), _dref(code)
        kronos_result = r.json()
    except Exception as e:
        logger.warning("[quant_agent] Kronos 请求异常 code={}: {}", code, e)
        return _degraded(code, f"Kronos 通信失败: {e}"), _dref(code)

    # Step 2: 多因子合成
    try:
        pro = await compute_pro_prediction(code, kronos_result, horizon)
    except Exception as e:
        logger.warning("[quant_agent] compute_pro_prediction 失败: {}", e)
        return _degraded(code, f"因子合成失败: {e}"), _dref(code)

    # Step 3: 归一化 summary（防御字段缺失）
    preds = pro.get("predictions") or kronos_result.get("predictions") or []
    last_close = float(pro.get("last_close") or kronos_result.get("last_close") or 0)
    end_close = float(preds[-1].get("close", last_close)) if preds else last_close
    pct = ((end_close - last_close) / last_close * 100) if last_close > 0 else 0.0
    trend = "up" if pct > 0.5 else "down" if pct < -0.5 else "flat"

    rating = pro.get("rating") or pro.get("stars") or _rating_from_pct(pct)
    factors = pro.get("factor_scores") or pro.get("factors") or {}

    return {
        "trend": trend,
        "pct_predicted": round(pct, 2),
        "rating": rating,
        "horizon_days": horizon,
        "factors": factors if isinstance(factors, dict) else {},
        "last_close": round(last_close, 2),
    }, _dref(code)


def _degraded(code: str, reason: str) -> dict:
    return {"trend": "unknown", "pct_predicted": 0.0, "rating": "N/A",
            "horizon_days": 0, "factors": {}, "last_close": 0.0,
            "note": reason}


def _dref(code: str) -> dict:
    return {"type": "kpred", "code": code}


def _rating_from_pct(pct: float) -> str:
    if pct >= 3:   return "★★★★★"
    if pct >= 1:   return "★★★★☆"
    if pct >= -1:  return "★★★☆☆"
    if pct >= -3:  return "★★☆☆☆"
    return "★☆☆☆☆"


@ToolRegistry.register("quant_predict", definition=_DEF, timeout=45)
async def _quant_tool(tc: ToolCall, bus) -> ToolResult:
    t0 = time.time()
    code = tc.args.get("code")
    if not code:
        return ToolResult.error_of(tc, "BAD_ARGS", "缺少 code")
    horizon = int(tc.args.get("horizon", 10))
    horizon = max(1, min(30, horizon))
    try:
        summary, detail = await invoke_quant_predict(str(code), horizon)
    except Exception as e:
        return ToolResult.error_of(
            tc, "INTERNAL", f"quant_predict 失败: {type(e).__name__}: {e}",
            duration_ms=int((time.time() - t0) * 1000),
        )
    return ToolResult(
        tool_call=tc, status="ok",
        duration_ms=int((time.time() - t0) * 1000),
        summary=summary, detail_ref=detail,
    )
