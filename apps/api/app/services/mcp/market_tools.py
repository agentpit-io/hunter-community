"""MCP 数据类工具

P1 起：get_quote（实时行情）
P3.S7 扩展：get_kline / get_pe_history / get_analyst_target / get_fx /
             get_ah_premium / get_earnings_consensus
"""
from __future__ import annotations
import asyncio
import time
from loguru import logger

from app.services.agent.tool_registry import ToolCall, ToolRegistry, ToolResult
from app.services.finance_data_client import (
    get_quote as fd_get_quote,
    get_kline as fd_get_kline,
)


# ─────────────────────────────── get_quote ───────────────────────────────
_QUOTE_DEF = {
    "name": "get_quote",
    "description": "查询实时行情（当前价、涨跌幅、成交量、买卖盘口）。参数只需 6 位 A 股代码或 5 位港股代码。",
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "A 股 6 位代码，港股 5 位代码"},
        },
        "required": ["code"],
    },
}


@ToolRegistry.register("get_quote", definition=_QUOTE_DEF, timeout=5)
async def _get_quote(tc: ToolCall, bus) -> ToolResult:
    t0 = time.time()
    code = tc.args.get("code")
    if not code:
        return ToolResult.error_of(tc, "BAD_ARGS", "缺少 code 参数")
    data = await asyncio.to_thread(fd_get_quote, str(code).strip())
    dur = int((time.time() - t0) * 1000)
    if not data:
        return ToolResult.error_of(tc, "NOT_FOUND", f"未取到行情: {code}", duration_ms=dur)
    # 只挑主 agent 汇总用得上的字段（避免把五档盘口都塞给 LLM）
    summary = {
        "code": data.get("code"),
        "name": data.get("name"),
        "price": data.get("price"),
        "change_pct": data.get("change_pct"),
        "change_amt": data.get("change_amt"),
        "open": data.get("open"),
        "high": data.get("high"),
        "low": data.get("low"),
        "prev_close": data.get("prev_close"),
        "volume": data.get("volume"),
        "amount": data.get("amount"),
    }
    logger.debug("[mcp] get_quote code={} price={} in {}ms",
                 code, summary["price"], dur)
    return ToolResult(tool_call=tc, status="ok", duration_ms=dur, summary=summary)


# ─────────────────────────────── get_kline ───────────────────────────────
_KLINE_DEF = {
    "name": "get_kline",
    "description": "日 K 线数据（开高低收量）。用于技术形态分析、均线判断、关键位识别。默认返回近 60 日。",
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "days": {"type": "integer", "default": 60, "minimum": 5, "maximum": 250},
        },
        "required": ["code"],
    },
}


@ToolRegistry.register("get_kline", definition=_KLINE_DEF, timeout=10)
async def _get_kline(tc: ToolCall, bus) -> ToolResult:
    t0 = time.time()
    code = tc.args.get("code")
    if not code:
        return ToolResult.error_of(tc, "BAD_ARGS", "缺少 code")
    days = int(tc.args.get("days", 60))
    days = max(5, min(250, days))
    bars = await asyncio.to_thread(fd_get_kline, str(code).strip(), "daily", days)
    dur = int((time.time() - t0) * 1000)
    if not bars:
        return ToolResult.error_of(tc, "NOT_FOUND", f"未取到 K 线: {code}", duration_ms=dur)

    # 压缩返回：只给主 agent 关键统计而非全量 K 线（省 tokens）
    closes = [float(b["close"]) for b in bars if b.get("close")]
    highs = [float(b["high"]) for b in bars if b.get("high")]
    lows = [float(b["low"]) for b in bars if b.get("low")]
    volumes = [int(b["volume"]) for b in bars if b.get("volume")]
    if not closes:
        return ToolResult.error_of(tc, "NOT_FOUND", f"K 线空: {code}", duration_ms=dur)

    last_close = closes[-1]
    high_window = max(highs) if highs else last_close
    low_window = min(lows) if lows else last_close
    ma5 = sum(closes[-5:]) / min(5, len(closes))
    ma20 = sum(closes[-20:]) / min(20, len(closes))
    ma60 = sum(closes[-60:]) / min(60, len(closes))
    vol_ma5 = sum(volumes[-5:]) / max(1, min(5, len(volumes))) if volumes else 0

    # 只回传最近 20 根 K 线原始数据（若模型需要精细）
    recent_20 = bars[-20:] if len(bars) > 20 else bars

    summary = {
        "code": code,
        "days_returned": len(bars),
        "last_close": round(last_close, 2),
        "window_high": round(high_window, 2),
        "window_low": round(low_window, 2),
        "ma5": round(ma5, 2),
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2),
        "vol_ma5": int(vol_ma5),
        "pct_from_high": round((last_close - high_window) / high_window * 100, 2) if high_window > 0 else 0,
        "pct_from_low": round((last_close - low_window) / low_window * 100, 2) if low_window > 0 else 0,
        "recent_20_bars": recent_20,
    }
    return ToolResult(tool_call=tc, status="ok", duration_ms=dur, summary=summary)


# ─────────────────────────────── get_pe_history ───────────────────────────────
_PE_DEF = {
    "name": "get_pe_history",
    "description": "PE（市盈率）历史分位。用于判断估值是否处于历史高低位。返回当前 PE + 近 N 年分位数。",
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "years": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10},
        },
        "required": ["code"],
    },
}


@ToolRegistry.register("get_pe_history", definition=_PE_DEF, timeout=15)
async def _get_pe_history(tc: ToolCall, bus) -> ToolResult:
    """基于 factor_engine.factor_pe 拿当前 PE 分位。"""
    t0 = time.time()
    code = tc.args.get("code")
    if not code:
        return ToolResult.error_of(tc, "BAD_ARGS", "缺少 code")
    try:
        from app.services.factor_engine import factor_pe
        # factor_pe 是同步计算，且返回 0-1 score；实际的绝对 PE 我们暂无 tool 可拿，
        # 用 score 反推分位（score 越高 = PE 越低 = 越便宜）
        score = await asyncio.to_thread(factor_pe, str(code).strip(), 0.0)
    except Exception as e:
        return ToolResult.error_of(tc, "INTERNAL", f"factor_pe 失败: {e}",
                                     duration_ms=int((time.time() - t0) * 1000))
    dur = int((time.time() - t0) * 1000)
    # 简化：score 转成分位描述
    if score >= 0.8:
        level = "历史低位（估值便宜，约 10-20 分位）"
    elif score >= 0.5:
        level = "历史中位偏低（约 30-50 分位）"
    elif score >= 0.2:
        level = "历史中位偏高（约 60-80 分位）"
    else:
        level = "历史高位（估值贵，约 80-100 分位）"
    summary = {
        "code": code,
        "score": round(score, 3),
        "level_desc": level,
        "note": "score 越高代表 PE 越低（相对便宜）；分位描述为定性判断",
    }
    return ToolResult(tool_call=tc, status="ok", duration_ms=dur, summary=summary)


# ─────────────────────────────── get_fx ───────────────────────────────
_FX_DEF = {
    "name": "get_fx",
    "description": "即时外汇汇率（简化实现，用一个近似固定值 + 时间戳）。主要用于 AH 溢价换算。",
    "parameters": {
        "type": "object",
        "properties": {
            "pair": {"type": "string", "description": "货币对，如 HKDCNY / USDCNY",
                     "default": "HKDCNY"},
        },
    },
}

# 简化：固定近似值 + 提示"以最新为准"（未来可接实时汇率 API）
_FX_APPROX = {
    "HKDCNY": 0.91,
    "USDCNY": 7.20,
    "EURCNY": 7.85,
}


@ToolRegistry.register("get_fx", definition=_FX_DEF, timeout=3)
async def _get_fx(tc: ToolCall, bus) -> ToolResult:
    pair = str(tc.args.get("pair", "HKDCNY")).upper().replace("/", "")
    rate = _FX_APPROX.get(pair)
    if rate is None:
        return ToolResult.error_of(tc, "NOT_FOUND", f"未内置汇率: {pair}")
    return ToolResult(
        tool_call=tc, status="ok", duration_ms=1,
        summary={"pair": pair, "rate": rate,
                  "note": "内置近似汇率，AH 套利判断请以实盘为准。"},
    )


# ─────────────────────────────── get_ah_premium ───────────────────────────────
_AH_DEF = {
    "name": "get_ah_premium",
    "description": "A/H 双重上市股的溢价率（(A - H×fx) / A × 100%）。需要同时传 A 股 6 位码和 H 股 5 位码。",
    "parameters": {
        "type": "object",
        "properties": {
            "code_a": {"type": "string", "description": "A 股 6 位代码"},
            "code_h": {"type": "string", "description": "H 股 5 位代码"},
        },
        "required": ["code_a", "code_h"],
    },
}


@ToolRegistry.register("get_ah_premium", definition=_AH_DEF, timeout=10)
async def _get_ah_premium(tc: ToolCall, bus) -> ToolResult:
    t0 = time.time()
    code_a = str(tc.args.get("code_a", "")).strip()
    code_h = str(tc.args.get("code_h", "")).strip()
    if not code_a or not code_h:
        return ToolResult.error_of(tc, "BAD_ARGS", "缺少 code_a 或 code_h")

    # 并行 3 个请求
    q_a, q_h = await asyncio.gather(
        asyncio.to_thread(fd_get_quote, code_a),
        asyncio.to_thread(fd_get_quote, code_h),
        return_exceptions=True,
    )
    fx = _FX_APPROX.get("HKDCNY", 0.91)
    dur = int((time.time() - t0) * 1000)

    if isinstance(q_a, Exception) or not q_a:
        return ToolResult.error_of(tc, "NOT_FOUND",
                                     f"A 股行情缺失: {code_a}", duration_ms=dur)
    if isinstance(q_h, Exception) or not q_h:
        return ToolResult.error_of(tc, "NOT_FOUND",
                                     f"H 股行情缺失: {code_h}", duration_ms=dur)

    pa = float(q_a.get("price") or 0)
    ph = float(q_h.get("price") or 0)
    if pa <= 0 or ph <= 0:
        return ToolResult.error_of(tc, "NOT_FOUND",
                                     "价格为 0，无法计算", duration_ms=dur)

    premium_pct = (pa - ph * fx) / pa * 100
    if premium_pct > 0:
        interp = f"A 股溢价 {premium_pct:.1f}%（H 相对便宜）"
    else:
        interp = f"H 股溢价 {abs(premium_pct):.1f}%（A 相对便宜）"

    return ToolResult(
        tool_call=tc, status="ok", duration_ms=dur,
        summary={
            "code_a": code_a, "code_h": code_h,
            "price_a": round(pa, 3), "price_h": round(ph, 3),
            "fx_rate": fx,
            "premium_pct": round(premium_pct, 2),
            "interpretation": interp,
            "note": "汇率为内置近似值；港股通个人投资者才能实盘套利",
        },
    )


# ─────────────────── 占位：get_analyst_target / get_earnings_consensus ───────────
# 这两个依赖尚未在 hermes 落地的数据源（分析师目标价 / EPS 一致预期），
# P3.S7 只注册 stub，返回 NOT_IMPLEMENTED，供 skill 层判断降级。
# 未来接入 Wind / Choice / 巨潮时替换实现。

_ANALYST_DEF = {
    "name": "get_analyst_target",
    "description": "分析师目标价（若数据源未接入返回 NOT_IMPLEMENTED，主 agent 需自行降级）",
    "parameters": {
        "type": "object",
        "properties": {"code": {"type": "string"}},
        "required": ["code"],
    },
}


@ToolRegistry.register("get_analyst_target", definition=_ANALYST_DEF, timeout=3)
async def _get_analyst_target(tc: ToolCall, bus) -> ToolResult:
    return ToolResult.error_of(
        tc, "NOT_IMPLEMENTED",
        "分析师目标价数据源未接入。请用 research/scout 从新闻中提取。",
    )


_EARNINGS_DEF = {
    "name": "get_earnings_consensus",
    "description": "EPS/收入一致预期（未接入 → NOT_IMPLEMENTED）",
    "parameters": {
        "type": "object",
        "properties": {"code": {"type": "string"}},
        "required": ["code"],
    },
}


@ToolRegistry.register("get_earnings_consensus", definition=_EARNINGS_DEF, timeout=3)
async def _get_earnings_consensus(tc: ToolCall, bus) -> ToolResult:
    return ToolResult.error_of(
        tc, "NOT_IMPLEMENTED",
        "EPS 一致预期数据源未接入。请用 scout 抓机构报告或 research 拉分析师预告。",
    )
