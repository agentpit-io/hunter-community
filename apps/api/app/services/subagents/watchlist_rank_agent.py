"""自选股排序 · 多时段横向对比 tool(方案 A)

用户问『把我的自选股排序 / 谁最好 / 分 3M/6M/1Y/3Y 前景』时,
opencode LLM 直接调这个 tool 一次完成 · 不再逐股跑 stock_deep_analysis
(那样 7 只 × 30-60s 串行,而且数据缺失时会把"数据未 seed"糊到用户脸上)。

设计:
- 数据:每股拉 quote + 近 252 交易日 kline(52 周高低 + 均线)· 现有稳定字段
- 打分:3M / 6M **本地公式**打分(纯技术面) · LLM 不参与打分
- 1Y / 3Y:finance-data 目前未接入 ROE/研报等长期基本面 · **不量化**,
  只出一个定性标签("技术偏强/偏弱")+ 一句方法学声明,告知用户
  真要长期视角就对个别股单独跑深度分析
- LLM:只做一次"结构化打分表 → markdown 排序报告"的合成

对应交接:doc/开源hunter-community/02开发工作交接/2026-08-18_方案A-自选股排序.md
"""
from __future__ import annotations
import asyncio
import json
import os
import time
from typing import Optional

from loguru import logger

from app.services.agent.tool_registry import ToolCall, ToolRegistry, ToolResult
from app.services.online_analysis.llm_client import get_client
from app.services import finance_data_client as fd
from app.services.database import get_all_stocks_by_user
from app.services.subagents.watchlist_agent import (
    _get_a_quote_from_redis, _hk_quote_sync, _us_quote_sync,
)


# 与 watchlist_agent 用同一个 sub-agent env(短评归因和排序同属自选股域)
_MODEL = os.getenv("AGENT_SUB_WL_MODEL", "gemini-3-flash-preview")

_HORIZONS_ALL = ["3M", "6M", "1Y", "3Y"]


# ═════════════════════════════════════════════════════════════════
# 打分函数 · 纯本地计算 · 不进 LLM
# ═════════════════════════════════════════════════════════════════

def _ma(kline: list[dict], n: int) -> Optional[float]:
    """n 日简单移动均线 · 数据不足返回 None。"""
    if not kline or len(kline) < n:
        return None
    closes = [b.get("close") for b in kline[-n:] if b.get("close") is not None]
    if len(closes) < n:
        return None
    return sum(closes) / n


def _return_pct(kline: list[dict], days: int) -> Optional[float]:
    """近 days 日的收益率 %。"""
    if not kline or len(kline) < days + 1:
        return None
    old = kline[-days - 1].get("close")
    new = kline[-1].get("close")
    if not old or not new:
        return None
    return (new - old) / old * 100


def _score_3m(quote: dict, kline: list[dict]) -> dict:
    """3M 短期分 · 4 个子项 · 范围 -4~+4 · 缺数据的子项跳过不扣分。"""
    if not quote or not kline:
        return {"score": None, "reason": "数据不足", "sub": {}}
    price = quote.get("price") or quote.get("current")
    if not price:
        return {"score": None, "reason": "无当前价", "sub": {}}

    ma20 = _ma(kline, 20)
    ma60 = _ma(kline, 60)
    ret5 = _return_pct(kline, 5)
    vol5 = [b.get("volume", 0) for b in kline[-5:] if b.get("volume")]
    vol20 = [b.get("volume", 0) for b in kline[-20:] if b.get("volume")]
    vol_ratio = (sum(vol5) / len(vol5)) / (sum(vol20) / len(vol20)) if vol5 and vol20 else None

    sub: dict = {}
    score = 0

    # 1. MA20 vs MA60 多头排列
    if ma20 is not None and ma60 is not None:
        sub["ma20_above_ma60"] = ma20 > ma60
        score += 1 if ma20 > ma60 else -1

    # 2. 当前价 vs MA20
    if ma20 is not None:
        sub["price_above_ma20"] = price > ma20
        score += 1 if price > ma20 else -1

    # 3. 近 5 日量比(vs 20 日均量)
    if vol_ratio is not None:
        sub["vol_ratio_5v20"] = round(vol_ratio, 2)
        if vol_ratio > 1.2:
            score += 1
        elif vol_ratio < 0.7:
            score -= 1

    # 4. 近 5 日动量
    if ret5 is not None:
        sub["return_5d_pct"] = round(ret5, 2)
        if ret5 > 3:
            score += 1
        elif ret5 < -3:
            score -= 1

    return {"score": score, "sub": sub}


def _score_6m(quote: dict, kline: list[dict]) -> dict:
    """6M 中期分 · 3 个子项 · 范围 -3~+3。"""
    if not quote or not kline:
        return {"score": None, "reason": "数据不足", "sub": {}}
    price = quote.get("price") or quote.get("current")
    if not price:
        return {"score": None, "reason": "无当前价", "sub": {}}

    ma60 = _ma(kline, 60)
    ma120 = _ma(kline, 120)
    ret60 = _return_pct(kline, 60)

    # 52 周区间(kline 传进来是 252 日 · 覆盖 1 年)
    highs = [b.get("high", 0) for b in kline if b.get("high")]
    lows = [b.get("low", 0) for b in kline if b.get("low") and b.get("low") > 0]
    high52 = max(highs) if highs else None
    low52 = min(lows) if lows else None

    sub: dict = {}
    score = 0

    # 1. MA60 vs MA120
    if ma60 is not None and ma120 is not None:
        sub["ma60_above_ma120"] = ma60 > ma120
        score += 1 if ma60 > ma120 else -1

    # 2. 52 周分位 · 0.3-0.7 中位健康 · >0.85 高位有回撤压力
    if high52 and low52 and high52 > low52:
        pos = (price - low52) / (high52 - low52)
        sub["pos_52w"] = round(pos, 2)
        if 0.3 <= pos <= 0.7:
            score += 1
        elif pos > 0.85:
            score -= 1

    # 3. 60 日动量
    if ret60 is not None:
        sub["return_60d_pct"] = round(ret60, 2)
        if ret60 > 10:
            score += 1
        elif ret60 < -10:
            score -= 1

    return {"score": score, "sub": sub}


# ═════════════════════════════════════════════════════════════════
# 基本面打分 · 1Y / 3Y · 只对 A 股(port. finance-data 提供 25 期季报)
# 港股 financials 目前拉不到 · 走独立分支 · 标"财务数据未接入"
# ═════════════════════════════════════════════════════════════════

def _f(v) -> float | None:
    """安全 float 转换 · None / '' / 'nan' → None。"""
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN 检测
            return None
        return f
    except (TypeError, ValueError):
        return None


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return sum(xs) / len(xs)


def _std(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5


def _score_1y(financials: list | None) -> dict:
    """1Y 基本面分 · 最近 5 期季报 · 范围 -4~+7 · 归一化到 -4~+4 后展示。

    子项:
    - 最新 ROE: >20% +2 · 15-20% +1 · 8-15% 0 · <8% -1
    - 5 期 ROE 均值: >15% +1
    - ROE 趋势(最新 vs 5 期均值): 上升 +1 · 明显下降 -1
    - 营收同比: >20% +1 · <0% -1
    - 净利同比: >30% +2 · >0% +1 · <-20% -2
    """
    if not financials or not isinstance(financials, list):
        return {"score": None, "reason": "无财务数据(可能是港股或上游未 seed)", "sub": {}}
    recent = financials[-5:]
    if not recent:
        return {"score": None, "reason": "财务数据期数不足", "sub": {}}

    latest = recent[-1]
    sub: dict = {}
    score = 0
    raw = 0  # 原始分(-4~+7) · 用于内部计算,不外露

    roe_latest = _f(latest.get("du_return_on_equity"))
    roe_series = [_f(r.get("du_return_on_equity")) for r in recent]
    rev_yoy = _f(latest.get("inc_revenue_rate"))
    np_yoy = _f(latest.get("inc_net_profit_rate"))
    period = latest.get("m_timetag")

    if period:
        sub["latest_period"] = period

    # 1. 最新 ROE
    if roe_latest is not None:
        sub["roe_latest_pct"] = round(roe_latest, 2)
        if roe_latest > 20:
            raw += 2
        elif roe_latest > 15:
            raw += 1
        elif roe_latest < 8:
            raw -= 1

    # 2. 5 期 ROE 均值
    roe_mean = _mean(roe_series)
    if roe_mean is not None:
        sub["roe_mean_5q_pct"] = round(roe_mean, 2)
        if roe_mean > 15:
            raw += 1

    # 3. ROE 趋势
    if roe_latest is not None and roe_mean is not None and roe_mean != 0:
        trend = (roe_latest - roe_mean) / abs(roe_mean)
        sub["roe_trend"] = round(trend, 2)
        if trend > 0.1:
            raw += 1
        elif trend < -0.3:
            raw -= 1

    # 4. 营收同比
    if rev_yoy is not None:
        sub["revenue_yoy_pct"] = round(rev_yoy, 2)
        if rev_yoy > 20:
            raw += 1
        elif rev_yoy < 0:
            raw -= 1

    # 5. 净利同比
    if np_yoy is not None:
        sub["net_profit_yoy_pct"] = round(np_yoy, 2)
        if np_yoy > 30:
            raw += 2
        elif np_yoy > 0:
            raw += 1
        elif np_yoy < -20:
            raw -= 2

    # 归一化 · 原始 -4~+7 映射到 -4~+4(裁剪)
    score = max(-4, min(4, raw))
    return {"score": score, "raw": raw, "sub": sub}


def _score_3y(financials: list | None) -> dict:
    """3Y 长期基本面 · 需要 ≥12 期(3 年季报) · 范围 0~+4。

    子项(长期只加分不减分 · 缺一项就少 1 分):
    - 12 期 ROE 均值 >15%
    - 12 期 ROE 稳定性(std/mean < 0.4 = 波动小)
    - 12 期营收 CAGR >10%(最新 4 期 TTM vs 3 年前 4 期 TTM)
    - 12 期毛利率均值 >30%(护城河参考)
    """
    if not financials or not isinstance(financials, list) or len(financials) < 12:
        return {"score": None,
                "reason": f"财务期数不足 3 年(当前 {len(financials) if financials else 0}/12 期)",
                "sub": {}}
    recent12 = financials[-12:]
    sub: dict = {}
    score = 0

    # 1. 12 期 ROE 均值
    roe_series = [_f(r.get("du_return_on_equity")) for r in recent12]
    roe_mean = _mean(roe_series)
    if roe_mean is not None:
        sub["roe_mean_12q_pct"] = round(roe_mean, 2)
        if roe_mean > 15:
            score += 1

    # 2. 12 期 ROE 稳定性 · std/|mean| 越小越稳
    roe_std = _std(roe_series)
    if roe_std is not None and roe_mean is not None and abs(roe_mean) > 1e-6:
        cv = roe_std / abs(roe_mean)
        sub["roe_cv_12q"] = round(cv, 2)
        if cv < 0.4:
            score += 1

    # 3. 3 年营收 CAGR · 最新 4 期 vs 最早 4 期 · 用累计营收比较避免季节性
    # 用 s_fa_eps_basic × bps × ??? 不好算 · 直接用 inc_total_revenue_annual 若存在,
    # 或退化到用最新 ROE * 最新 BPS vs 3 年前
    old4 = financials[-16:-12] if len(financials) >= 16 else financials[:4]
    new4 = recent12[-4:]
    # 简化:用 EPS 均值代理营收增速(EPS 是稀释后每股收益 · 累计季度可比)
    eps_old = _mean([_f(r.get("s_fa_eps_basic")) for r in old4])
    eps_new = _mean([_f(r.get("s_fa_eps_basic")) for r in new4])
    if eps_old and eps_new and eps_old > 0:
        # 3 年 CAGR
        cagr = (eps_new / eps_old) ** (1 / 3) - 1
        sub["eps_cagr_3y_pct"] = round(cagr * 100, 2)
        if cagr > 0.10:
            score += 1

    # 4. 12 期毛利率均值
    gp_series = [_f(r.get("sales_gross_profit")) for r in recent12]
    gp_mean = _mean(gp_series)
    if gp_mean is not None:
        sub["gross_margin_mean_12q_pct"] = round(gp_mean, 2)
        if gp_mean > 30:
            score += 1

    return {"score": score, "sub": sub}


# ═════════════════════════════════════════════════════════════════
# 数据拉取 · 每股并行
# ═════════════════════════════════════════════════════════════════

def _fetch_financials_sync(code: str, market: str) -> list | None:
    """拉 A 股财报 · 走共享层 agents.data_sources.akshare_financials · 港股当前无。

    2026-08-20 · 独立运行模式 · fd._get(/api/v1/financial) 已切断
    · 双通道(同花顺 abstract_ths → 东财 abstract)统一维护于共享层
    · _pct_str 归一化、字段映射、按报告期升序都在共享层实现
    """
    from agents.data_sources.akshare_financials import fetch_financials
    return fetch_financials(code, market)


async def _fetch_one(stock: dict) -> dict:
    """并行拉一只股 quote + 252 日 kline + 财务(仅 A 股) · 现场打分。"""
    code = stock.get("code")
    market = (stock.get("market") or "A").upper()
    name = stock.get("name") or code

    # quote · 按市场分派
    if market == "HK":
        q_task = asyncio.to_thread(_hk_quote_sync, code)
    elif market == "US":
        q_task = asyncio.to_thread(_us_quote_sync, code)
    else:
        q_redis = await asyncio.to_thread(_get_a_quote_from_redis, code)
        if q_redis:
            q_task = asyncio.sleep(0, result=q_redis)
        else:
            q_task = asyncio.to_thread(fd.get_quote, code)

    k_task = asyncio.to_thread(fd.get_kline_with_fallback, code, "daily", 252)
    f_task = asyncio.to_thread(_fetch_financials_sync, code, market)

    try:
        quote, kline, financials = await asyncio.gather(
            q_task, k_task, f_task, return_exceptions=True,
        )
    except Exception as e:
        logger.warning("[wl_rank] {} 并行拉数异常: {}", code, e)
        return {"code": code, "name": name, "market": market, "error": str(e),
                "has_data": False,
                "score_3m": {"score": None}, "score_6m": {"score": None},
                "score_1y": {"score": None}, "score_3y": {"score": None}}

    if isinstance(quote, Exception):
        logger.warning("[wl_rank] {} quote 失败: {}", code, quote)
        quote = None
    if isinstance(kline, Exception):
        logger.warning("[wl_rank] {} kline 失败: {}", code, kline)
        kline = None
    if isinstance(financials, Exception):
        logger.warning("[wl_rank] {} financial 失败: {}", code, financials)
        financials = None

    s3m = _score_3m(quote or {}, kline or [])
    s6m = _score_6m(quote or {}, kline or [])
    s1y = _score_1y(financials)
    s3y = _score_3y(financials)

    price = None
    change_pct = None
    if quote:
        price = quote.get("price") or quote.get("current")
        change_pct = quote.get("change_pct")

    return {
        "code": code,
        "name": name,
        "market": market,
        "price": round(float(price), 3) if price else None,
        "change_pct": round(float(change_pct), 2) if change_pct is not None else None,
        "score_3m": s3m,
        "score_6m": s6m,
        "score_1y": s1y,
        "score_3y": s3y,
        "kline_len": len(kline or []),
        "fin_periods": len(financials or []),
        "has_data": bool(quote and kline),
    }


# ═════════════════════════════════════════════════════════════════
# Markdown 渲染 · **本地生成,不依赖 LLM**
# ------------------------------------------------------------------
# 排序表结构是完全确定的,LLM 只会带来:1) 不确定的失败率 2) 推理模型 tokens 耗尽风险
# 3) 上下文越长越贵。所以主体 markdown 全在服务端就地拼好,LLM 只出一句可选的总结,
# 失败也不影响用户看到完整排序。
# ═════════════════════════════════════════════════════════════════

def _composite_short(s: dict) -> float | None:
    """短中期综合分 · 3M×0.4 + 6M×0.6 · 用于总排序(不含基本面)。"""
    s3 = (s.get("score_3m") or {}).get("score")
    s6 = (s.get("score_6m") or {}).get("score")
    if s3 is None and s6 is None:
        return None
    if s3 is None:
        return s6 * 0.6
    if s6 is None:
        return s3 * 0.4
    return s3 * 0.4 + s6 * 0.6


def _composite(s: dict) -> float | None:
    """总综合分 · 短中期(权重 0.5)+ 1Y 基本面(0.3)+ 3Y 长期(0.2)。

    基本面数据缺失时(如港股)· 分子分母都按可用维度重新归一化 ·
    避免"缺基本面数据的股一律排最后"的误伤。
    """
    parts: list[tuple[float, float]] = []  # (value, weight)
    cs = _composite_short(s)
    if cs is not None:
        parts.append((cs, 0.5))
    s1y = (s.get("score_1y") or {}).get("score")
    if s1y is not None:
        parts.append((float(s1y), 0.3))
    s3y = (s.get("score_3y") or {}).get("score")
    if s3y is not None:
        # 3Y 只有 0~+4 · 转 -2~+2 便于加权(减去中位 2)
        parts.append((float(s3y) - 2, 0.2))
    if not parts:
        return None
    total_w = sum(w for _, w in parts)
    return sum(v * w for v, w in parts) / total_w if total_w else None


def _horizon_1y(s: dict) -> str:
    """1Y 展望 · 有基本面数据 → 显示分数 · 无 → 明确标"仅技术外推"。"""
    s1y = (s.get("score_1y") or {}).get("score")
    if s1y is not None:
        # 基本面分 · 转成直观描述
        if s1y >= 3:
            return f"基本面强 ({s1y:+d})"
        if s1y >= 1:
            return f"基本面稳健 ({s1y:+d})"
        if s1y <= -2:
            return f"基本面偏弱 ({s1y:+d})"
        return f"基本面中性 ({s1y:+d})"
    # 无财务数据 · 按短中期外推
    cs = _composite_short(s)
    if cs is None:
        return "数据不足"
    label = "技术偏强" if cs >= 1.5 else ("技术偏弱" if cs <= -1.5 else "技术中性")
    return f"{label} · 仅技术"


def _horizon_3y(s: dict) -> str:
    """3Y 展望 · 需 ≥3 年财报 · 显示 0~+4 分."""
    s3y = (s.get("score_3y") or {}).get("score")
    if s3y is not None:
        # 3Y 分 · 0~+4 · 每项占 1 分(ROE 均值 / 稳定性 / EPS CAGR / 毛利率)
        if s3y >= 3:
            return f"长期优质 ({s3y}/4)"
        if s3y >= 2:
            return f"长期稳健 ({s3y}/4)"
        if s3y >= 1:
            return f"长期一般 ({s3y}/4)"
        return f"长期弱 (0/4)"
    # 缺长期财报 · 走短中期外推 · 明确注明
    cs = _composite_short(s)
    if cs is None:
        return "数据不足"
    return "需长期财报" if cs is not None else "数据不足"


def _short_comment(s: dict) -> str:
    """一句 20-60 字点评 · 完全基于打分子项 · 不用 LLM · 优先展示基本面亮点。"""
    if not s.get("has_data"):
        return f"数据不足({s.get('error') or '拉取失败'})"
    sub3 = (s.get("score_3m") or {}).get("sub") or {}
    sub6 = (s.get("score_6m") or {}).get("sub") or {}
    sub1y = (s.get("score_1y") or {}).get("sub") or {}
    parts: list[str] = []

    # 基本面亮点(有的话先说 · 用户最关心)
    roe = sub1y.get("roe_latest_pct")
    np_yoy = sub1y.get("net_profit_yoy_pct")
    rev_yoy = sub1y.get("revenue_yoy_pct")
    if isinstance(np_yoy, (int, float)) and np_yoy > 30:
        parts.append(f"净利+{np_yoy:.0f}%")
    elif isinstance(np_yoy, (int, float)) and np_yoy < -20:
        parts.append(f"净利{np_yoy:.0f}%")
    if isinstance(roe, (int, float)) and roe > 15:
        parts.append(f"ROE {roe:.1f}%")
    if isinstance(rev_yoy, (int, float)) and rev_yoy > 20 and not (np_yoy and np_yoy > 30):
        parts.append(f"营收+{rev_yoy:.0f}%")

    # 短期:均线 · 动量 · 量能
    ma20_up = sub3.get("ma20_above_ma60")
    ret5 = sub3.get("return_5d_pct")
    vol_r = sub3.get("vol_ratio_5v20")
    if ma20_up is True:
        parts.append("短期多头")
    elif ma20_up is False:
        parts.append("短期空头")
    if isinstance(ret5, (int, float)):
        if ret5 <= -3:
            parts.append(f"5日跌{abs(ret5):.1f}%")
        elif ret5 >= 3:
            parts.append(f"5日涨{ret5:.1f}%")
    if isinstance(vol_r, (int, float)) and vol_r > 1.2:
        parts.append(f"放量({vol_r:.1f}x)")

    # 中期:52 周分位 · 60 日动量
    pos = sub6.get("pos_52w")
    ret60 = sub6.get("return_60d_pct")
    if isinstance(pos, (int, float)):
        if pos > 0.85:
            parts.append(f"52周高位({pos:.0%})")
        elif pos < 0.2:
            parts.append(f"52周低位({pos:.0%})")
    if isinstance(ret60, (int, float)):
        if ret60 <= -15:
            parts.append(f"60日跌{abs(ret60):.0f}%")
        elif ret60 >= 15:
            parts.append(f"60日涨{ret60:.0f}%")

    if not parts:
        return "各项指标居中"
    return " · ".join(parts)[:100]


def _fmt_score(v: int | None) -> str:
    """打分显示 · None → — · 正数带 + 号。"""
    if v is None:
        return "—"
    return f"{v:+d}"


def _render_markdown(scored: list[dict], horizons: list[str]) -> str:
    """全部本地拼 · 排序表 + 说明 · 不需要 LLM。"""
    ranked = sorted(
        scored,
        key=lambda s: (_composite(s) is None, -(_composite(s) or 0)),
    )

    # 整体气氛判断
    valid = [_composite(s) for s in ranked if _composite(s) is not None]
    if not valid:
        overall = "所有自选股数据拉取失败,请稍后重试"
    else:
        avg = sum(valid) / len(valid)
        up_n = sum(1 for c in valid if c > 0)
        dn_n = sum(1 for c in valid if c < 0)
        if avg > 0.8:
            mood = "整体偏强"
        elif avg < -0.8:
            mood = "整体偏弱"
        else:
            mood = "整体分化"
        overall = f"{mood} · {up_n} 只正分 / {dn_n} 只负分 · 综合分均值 {avg:+.2f}"

    # 覆盖率统计 · 告知用户数据完整度
    fin_ok = sum(1 for s in scored if (s.get("score_1y") or {}).get("score") is not None)
    fin_3y_ok = sum(1 for s in scored if (s.get("score_3y") or {}).get("score") is not None)
    hk_or_us = sum(1 for s in scored if s.get("market") in ("HK", "US"))

    lines: list[str] = []
    lines.append(f"### 我的自选股排序 · {' / '.join(horizons)}")
    lines.append("")
    lines.append(overall)
    lines.append("")

    cols = ["排名", "股票"]
    if "3M" in horizons:
        cols.append("3M")
    if "6M" in horizons:
        cols.append("6M")
    cols.append("综合")
    if "1Y" in horizons:
        cols.append("1Y 展望")
    if "3Y" in horizons:
        cols.append("3Y 展望")
    cols.append("关键信号")
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("|" + "|".join(["---"] * len(cols)) + "|")

    for i, s in enumerate(ranked, 1):
        row = [f"#{i}", f"{s.get('name')} ({s.get('code')}·{s.get('market')})"]
        if "3M" in horizons:
            row.append(_fmt_score((s.get("score_3m") or {}).get("score")))
        if "6M" in horizons:
            row.append(_fmt_score((s.get("score_6m") or {}).get("score")))
        c = _composite(s)
        row.append(f"{c:+.1f}" if c is not None else "—")
        if "1Y" in horizons:
            row.append(_horizon_1y(s))
        if "3Y" in horizons:
            row.append(_horizon_3y(s))
        row.append(_short_comment(s))
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")
    lines.append("**打分口径**:")
    lines.append("- **3M**(-4~+4):MA20 vs MA60 · 价 vs MA20 · 5 日量比 · 5 日动量")
    lines.append("- **6M**(-3~+3):MA60 vs MA120 · 52 周分位 · 60 日动量")
    lines.append("- **1Y**(-4~+4):最新 ROE + 5 期 ROE 均值 + ROE 趋势 + 营收/净利同比 · 基于财报")
    lines.append("- **3Y**(0~+4):12 期 ROE 均值 · ROE 稳定性 · 3 年 EPS CAGR · 12 期毛利率均值")
    lines.append("- **综合分**:短中期(0.5) + 1Y 基本面(0.3) + 3Y 长期(0.2)· 缺失维度按可用权重归一化")
    lines.append("")
    lines.append("**数据覆盖**:")
    lines.append(f"- 基本面(1Y):{fin_ok}/{len(scored)} 只有财报数据{f' · 港股 {hk_or_us} 只暂无财务数据接入,走技术外推' if hk_or_us else ''}")
    lines.append(f"- 长期(3Y):{fin_3y_ok}/{len(scored)} 只满足 ≥12 期季报,不足的走短中期外推")
    lines.append("")
    lines.append("**要更深入**:对表里最感兴趣的 1-2 只跑 `stock_deep_analysis`(单股 30-60s · 拉龙虎榜/新闻/研报)· 不建议批量跑。**本报告不给买卖建议**。")
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════
# LLM 一句总结(可选 · 失败不影响主体)
# ═════════════════════════════════════════════════════════════════

_SUMMARY_SYS = (
    "你是猎鹿人分析师。给你一份自选股排序打分表,只需要输出**一段 60-100 字**的口语化总结,"
    "涵盖:整体气氛 / 最强的 1-2 只 / 最弱的 1 只 / 一句给用户的关注建议。"
    "不给买卖评级。中文。直接出正文,不要标题不要 markdown 结构。"
)


async def _llm_one_liner(scored: list[dict]) -> str:
    """让 LLM 出一段口语化总结附在排序表后 · 失败静默返回空串。"""
    client = get_client()
    if client is None:
        return ""
    ranked = sorted(scored, key=lambda s: (_composite(s) is None, -(_composite(s) or 0)))
    brief = [
        {
            "name": s.get("name"),
            "code": s.get("code"),
            "market": s.get("market"),
            "composite": round(_composite(s), 2) if _composite(s) is not None else None,
            "3M": (s.get("score_3m") or {}).get("score"),
            "6M": (s.get("score_6m") or {}).get("score"),
            "1Y": (s.get("score_1y") or {}).get("score"),
            "3Y": (s.get("score_3y") or {}).get("score"),
            "roe": (s.get("score_1y") or {}).get("sub", {}).get("roe_latest_pct"),
            "np_yoy": (s.get("score_1y") or {}).get("sub", {}).get("net_profit_yoy_pct"),
            "rev_yoy": (s.get("score_1y") or {}).get("sub", {}).get("revenue_yoy_pct"),
            "change_today_pct": s.get("change_pct"),
        }
        for s in ranked
    ]
    prompt = f"打分表(已排序 · 综合分从高到低):\n{json.dumps(brief, ensure_ascii=False)}\n\n输出一段总结。"
    try:
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SUMMARY_SYS},
                {"role": "user", "content": prompt},
            ],
            # DeepSeek pro 是推理模型 · reasoning_tokens 常吃 1000+ · 给 2500 保底
            # 非推理模型(flash / openai / gemini)也不会浪费,只按实际生成算
            max_tokens=2500,
            temperature=0.4,
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            finish = resp.choices[0].finish_reason if resp.choices else "?"
            usage = getattr(resp, "usage", None)
            logger.warning(
                "[wl_rank] LLM 一句总结空返回 · finish={} usage={}",
                finish,
                {"prompt": getattr(usage, "prompt_tokens", None),
                 "completion": getattr(usage, "completion_tokens", None),
                 "reasoning": getattr(getattr(usage, "completion_tokens_details", None),
                                       "reasoning_tokens", None)} if usage else None,
            )
        return text[:400]
    except Exception as e:
        logger.warning("[wl_rank] LLM 总结失败 · 静默跳过: {}", e)
        return ""


# ═════════════════════════════════════════════════════════════════
# 主入口
# ═════════════════════════════════════════════════════════════════

async def _rank(user_id: str, horizons: Optional[list[str]] = None) -> dict:
    if not user_id:
        return {"type": "watchlist_rank", "error": "需要登录后才能对自选股排序"}

    horizons = [h for h in (horizons or _HORIZONS_ALL) if h in _HORIZONS_ALL] or _HORIZONS_ALL

    try:
        stocks = await asyncio.to_thread(get_all_stocks_by_user, user_id)
    except Exception as e:
        logger.warning("[wl_rank] 拉自选失败: {}", e)
        stocks = []

    if not stocks:
        return {
            "type": "watchlist_rank",
            "empty": True,
            "hint": "你还没有自选股。去自选股页面加几只再来问排序。",
        }

    # 并行拉每股数据 · 就地打分
    t0 = time.time()
    scored = await asyncio.gather(*[_fetch_one(s) for s in stocks])
    fetch_ms = int((time.time() - t0) * 1000)

    # 主体 markdown · 本地渲染 · 永远有输出(不依赖 LLM)
    markdown = _render_markdown(scored, horizons)

    # 附加一段 LLM 口语化总结 · 失败也不影响主体
    t1 = time.time()
    summary = await _llm_one_liner(scored)
    llm_ms = int((time.time() - t1) * 1000)
    if summary:
        markdown += f"\n\n---\n\n**💬 一句话解读:** {summary}"

    return {
        "type": "watchlist_rank",
        "stock_count": len(scored),
        "horizons": horizons,
        "scored": scored,
        "markdown": markdown,
        "fetch_ms": fetch_ms,
        "llm_ms": llm_ms,
        "llm_summary_used": bool(summary),
        "method_note": (
            "3M/6M 基于本地技术面公式打分(均线/分位/量能/动量);"
            "1Y/3Y 因 finance-data 未接入 ROE/增速/研报,未量化 · "
            "需要长期视角建议对具体股票单独跑深度分析。"
        ),
    }


# ═════════════════════════════════════════════════════════════════
# ToolRegistry 注册 · 内部 orchestrator 走这个;MCP 层走 endpoint(见 internal_tools.py)
# ═════════════════════════════════════════════════════════════════

_RANK_DEF = {
    "name": "watchlist_rank",
    "description": (
        "自选股**排序 / 横向对比**。用户问『把我的自选排序 / 谁最好 / 分 X 月/年前景 / "
        "哪几只最强』时用。一次调用完成 N 只 × 4 时段(3M/6M/1Y/3Y)排序 · "
        "秒级返回 · 不要为每只股逐一调 stock_deep_analysis(那样慢且不能横向对比)。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "horizons": {
                "type": "array",
                "items": {"type": "string", "enum": ["3M", "6M", "1Y", "3Y"]},
                "description": "要排序的时段 · 默认 4 个全展开",
            },
            "user_id": {"type": "string", "description": "内部字段 · orchestrator 注入"},
        },
        "required": [],
    },
}


@ToolRegistry.register("watchlist_rank", definition=_RANK_DEF, timeout=60)
async def _rank_tool(tc: ToolCall, bus) -> ToolResult:
    t0 = time.time()
    user_id = tc.args.get("user_id") or ""
    horizons = tc.args.get("horizons") or None
    try:
        summary = await _rank(str(user_id), horizons)
    except Exception as e:
        return ToolResult.error_of(
            tc, "INTERNAL", f"watchlist_rank 失败: {type(e).__name__}: {e}",
            duration_ms=int((time.time() - t0) * 1000),
        )
    return ToolResult(
        tool_call=tc, status="ok",
        duration_ms=int((time.time() - t0) * 1000),
        summary=summary,
    )
