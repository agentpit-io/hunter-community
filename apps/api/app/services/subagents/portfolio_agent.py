"""组合级 · 3 tool sub-agent（P2 + Sprint 1）

- portfolio_rebalance   : 组合级建议（当前% vs 目标%，出加/减仓动作）
- portfolio_stress      : 情景模拟（若 X 跌 Y%，含行业联动，估算组合总损失）
- update_risk_profile   : 读/改风险画像（现金 / 单票上限 / 风险偏好）· 2026-08 新增

数据源：
- 持仓 = position_thesis (shares + cost_price + target_weight_pct)
- 画像 = user_risk_profile (cash_balance + max_position + max_hk_ratio)
- 行业 = 硬编码 INDUSTRY_MAP（P2 MVP · P3 会接 stock_industry 表动态）
- β    = 硬编码 INDUSTRY_BETA（P2 MVP）
"""
from __future__ import annotations
import asyncio
import os
import time
from typing import Optional

from loguru import logger

from app.services.agent.tool_registry import ToolCall, ToolRegistry, ToolResult
from app.services.online_analysis.llm_client import get_client
from app.services import finance_data_client as fd
from app.services.database import (
    list_stocks_with_thesis_by_user,
    get_risk_profile,
    upsert_risk_profile,
)


_MODEL = os.getenv("AGENT_SUB_PORT_MODEL", "gemini-3.5-flash")


# ═════════════════════════════════════════════════════════════════
# 硬编码行业映射（P2 MVP · 覆盖主流大市值股）
# 后期可迁到 stock_industry 表 · 但当前 shortcut 也能出好结果
# ═════════════════════════════════════════════════════════════════

INDUSTRY_MAP: dict[str, str] = {
    # 有色
    "601899": "有色", "601168": "有色", "600362": "有色", "600111": "有色",
    "000933": "煤炭", "601088": "煤炭", "601225": "煤炭", "601898": "煤炭",
    "01378": "有色", "01818": "有色", "02600": "有色",
    # 白酒/消费
    "600519": "消费", "000858": "消费", "600809": "消费", "600887": "消费",
    "000568": "消费", "603288": "消费", "000596": "消费",
    # 银行/金融
    "601318": "金融", "600036": "金融", "601166": "金融", "601288": "金融",
    "601398": "金融", "601988": "金融", "601328": "金融", "601601": "金融",
    # 医药
    "600436": "医药", "600276": "医药", "300759": "医药",
    # 新能源/电池
    "300750": "新能源", "002594": "新能源", "601012": "新能源", "300390": "新能源",
    "600438": "新能源", "300274": "新能源",
    # 家电
    "000651": "家电", "000333": "家电", "600690": "家电",
    # 半导体/科技
    "002415": "科技", "300760": "医药", "002230": "科技", "300308": "科技",
    "688981": "半导体", "002371": "半导体", "603501": "半导体",
    "688093": "科技",  # 世华科技 · 电子胶粘剂
    # 港股科技
    "00700": "港股科技", "09988": "港股科技", "03690": "港股科技", "01024": "港股科技",
    # 石化 / 基建
    "601857": "石化", "600028": "石化", "601390": "基建", "601668": "基建",
    # ─── 2026-08 补：覆盖 hermes 主流用户持仓 · S0-2 ───
    # 机械
    "002595": "机械",  # 豪迈科技 · 轮胎模具
    "601633": "机械", "002444": "机械",
    # 化工
    "002001": "化工",  # 新和成 · 维生素/香精
    "600309": "化工", "600346": "化工", "002648": "化工",
    # 汽车
    "02333":  "汽车",  # 长城汽车
    "600104": "汽车", "000625": "汽车", "601633": "汽车",
    "02015":  "汽车",  # 理想汽车
    "09863":  "汽车",  # 零跑汽车
    "09866":  "汽车",  # 蔚来
    # 港股消费/医药（补 HK 常见持仓）
    "09992":  "消费",  # 泡泡玛特
    "00241":  "医药",  # 阿里健康
}

# β 联动矩阵（同板块 β=1 · 跨板块 β=0.15-0.45）
# 2026-08 补：机械/化工/汽车 三行
INDUSTRY_BETA: dict[str, dict[str, float]] = {
    "有色":   {"有色": 1.0, "煤炭": 0.35, "钢铁": 0.30, "化工": 0.20},
    "煤炭":   {"煤炭": 1.0, "有色": 0.30, "电力": 0.25, "钢铁": 0.30},
    "消费":   {"消费": 1.0, "食品": 0.35, "医药": 0.15},
    "金融":   {"金融": 1.0, "地产": 0.40, "券商": 0.55},
    "医药":   {"医药": 1.0, "消费": 0.15},
    "新能源": {"新能源": 1.0, "有色": 0.25, "半导体": 0.25, "电力": 0.20,
                "汽车": 0.45},
    "家电":   {"家电": 1.0, "消费": 0.25},
    "科技":   {"科技": 1.0, "半导体": 0.45, "港股科技": 0.35},
    "半导体": {"半导体": 1.0, "科技": 0.45},
    "港股科技": {"港股科技": 1.0, "科技": 0.35},
    "石化":   {"石化": 1.0, "化工": 0.40, "煤炭": 0.20},
    "基建":   {"基建": 1.0, "钢铁": 0.30, "水泥": 0.35},
    # ─── 2026-08 补 · S0-2 ───
    "机械":   {"机械": 1.0, "汽车": 0.35, "基建": 0.30, "新能源": 0.20},
    "化工":   {"化工": 1.0, "有色": 0.25, "石化": 0.40, "消费": 0.15},
    "汽车":   {"汽车": 1.0, "新能源": 0.45, "机械": 0.35, "港股科技": 0.15},
}


def _industry_of(code: str) -> str:
    return INDUSTRY_MAP.get(code, "其他")


def _sector_beta(sector_a: str, sector_b: str) -> float:
    """A 板块震荡 X% 对 B 板块的传导比例（0-1）。"""
    if sector_a == sector_b:
        return 1.0
    return INDUSTRY_BETA.get(sector_a, {}).get(sector_b, 0.0)


# ═════════════════════════════════════════════════════════════════
# TOOL 1 · portfolio_rebalance
# ═════════════════════════════════════════════════════════════════

_REBALANCE_DEF = {
    "name": "portfolio_rebalance",
    "description": (
        "组合级建议：拉当前用户的持仓（含 shares + cost_price + target_weight_pct），"
        "算当前权重 vs 目标权重的差距，出加/减仓动作。"
        "适合用户问『我持仓怎么调 / 仓位建议 / 该加减哪只』时使用。"
        "如果用户没录入持仓 shares 会返回 empty=true，前端引导去持仓页录入。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "内部用 · 由 orchestrator 上下文注入"},
            "cash_available": {"type": "number", "default": 0, "description": "可用现金 · 用于建议加仓金额"},
        },
        "required": [],
    },
}


async def _rebalance(user_id: str, cash: float = 0) -> dict:
    if not user_id:
        return {"type": "portfolio_rebalance", "error": "需要登录后才能给组合建议"}
    try:
        rows = await asyncio.to_thread(list_stocks_with_thesis_by_user, user_id)
    except Exception as e:
        logger.warning("[port_agent] 拉持仓失败: {}", e)
        return {"type": "portfolio_rebalance", "error": f"拉持仓失败: {e}"}

    # Sprint 1 · 拉用户风险画像 · cash 参数缺省时从 profile.cash_balance 兜底
    profile = await asyncio.to_thread(get_risk_profile, user_id)
    if not cash and profile.get("cash_balance"):
        cash = float(profile["cash_balance"])
    max_position_pct = float(profile.get("max_position", 0.25)) * 100  # 25.0
    max_hk_pct       = float(profile.get("max_hk_ratio", 0.40)) * 100
    max_sector_pct_lim = float(profile.get("max_sector", 0.40)) * 100

    positions_with_shares = [r for r in rows if r.get("shares") and r.get("cost_price")]
    if not positions_with_shares:
        return {
            "type": "portfolio_rebalance",
            "empty": True,
            "total_watchlist": len(rows),
            "hint": (
                "你的持仓里没有股数和成本记录。"
                "去『持仓』页录入 shares + cost_price，再来问我，我就能给出加减仓建议。"
            ),
        }

    # 并发拉最新价
    async def _q(r):
        try:
            q = await asyncio.to_thread(fd.get_quote, r["code"])
            if q:
                return {**r, "current_price": q.get("price", r["cost_price"] or 0)}
        except Exception:
            pass
        return {**r, "current_price": r.get("cost_price") or 0}

    positions_full = await asyncio.gather(*[_q(r) for r in positions_with_shares])

    # 组合总市值
    for p in positions_full:
        p["value"] = float(p["shares"]) * float(p["current_price"])
    total_value = sum(p["value"] for p in positions_full)
    total_with_cash = total_value + max(0, cash)

    # 目标权重：显式或等权
    n = len(positions_full)
    has_target = any(p.get("target_weight_pct") for p in positions_full)
    equal_pct = 100.0 / n if n > 0 else 0

    # 归一化目标权重（若和 != 100 则按比例）
    target_sum = sum((p.get("target_weight_pct") or 0) for p in positions_full)
    if has_target and target_sum > 0:
        norm = 100.0 / target_sum
    else:
        norm = 1.0

    results = []
    sector_exposure_current: dict[str, float] = {}
    for p in positions_full:
        target = ((p.get("target_weight_pct") or 0) * norm) if has_target else equal_pct
        current_pct = (p["value"] / total_with_cash * 100) if total_with_cash > 0 else 0
        gap_pct = target - current_pct
        target_value = total_with_cash * target / 100

        action_value = target_value - p["value"]
        # 换算股数（round to 100）
        action_shares = 0
        price = p["current_price"] or 1
        if price > 0:
            raw_shares = action_value / price
            action_shares = int(round(raw_shares / 100.0)) * 100

        # 若差距 < 1%，视为持有
        if abs(gap_pct) < 1.0 or action_shares == 0:
            action = "hold"
            action_label = "持有"
        elif action_shares > 0:
            action = "buy"
            action_label = f"▲ 加 {action_shares} 股 ≈ ¥{abs(action_value)/1000:.1f}k"
        else:
            action = "sell"
            action_label = f"▼ 减 {abs(action_shares)} 股 ≈ ¥{abs(action_value)/1000:.1f}k"

        sector = _industry_of(p["code"])
        sector_exposure_current[sector] = sector_exposure_current.get(sector, 0) + current_pct

        results.append({
            "code": p["code"], "name": p["name"],
            "current_shares": int(p["shares"]),
            "current_price": round(p["current_price"], 2),
            "current_value": round(p["value"], 2),
            "current_pct": round(current_pct, 1),
            "target_pct": round(target, 1),
            "gap_pct": round(gap_pct, 1),
            "action": action, "action_label": action_label,
            "action_shares": action_shares,
            "action_value": round(action_value, 2),
            "sector": sector,
        })

    # 板块暴露排序
    sector_top = sorted(sector_exposure_current.items(), key=lambda x: -x[1])[:5]
    max_sector_pct = sector_top[0][1] if sector_top else 0

    # Sprint 1 · 约束违反检查 · 用 profile.max_* 而不是硬编码
    warnings: list[str] = []
    if max_sector_pct > max_sector_pct_lim:
        warnings.append(
            f"{sector_top[0][0]} 合计 {max_sector_pct:.0f}% > 上限 {max_sector_pct_lim:.0f}%"
        )
    # 单票超上限
    over_single = [p for p in results if p["current_pct"] > max_position_pct]
    if over_single:
        warnings.append(
            "单票超上限（>%.0f%%）：%s" % (
                max_position_pct,
                " · ".join(f"{p['name']} {p['current_pct']:.0f}%" for p in over_single[:3]),
            )
        )
    # 港股合计（识别 HK · 5 位数字 code）
    hk_pct = sum(p["current_pct"] for p in results
                 if len(p["code"]) == 5 and p["code"].isdigit())
    if hk_pct > max_hk_pct:
        warnings.append(f"港股合计 {hk_pct:.0f}% > 上限 {max_hk_pct:.0f}%")

    risk_warning = " · ".join(warnings)

    return {
        "type": "portfolio_rebalance",
        "portfolio_value": round(total_value, 2),
        "cash": max(0, cash),
        "total_with_cash": round(total_with_cash, 2),
        "positions": results,
        "sector_exposure": {k: round(v, 1) for k, v in sector_exposure_current.items()},
        "risk_warning": risk_warning,
        "has_explicit_target": has_target,
        # Sprint 1 · 新增 · 让前端显示「已应用画像」并给出编辑入口
        "profile_applied": {
            "cash_balance":   float(profile.get("cash_balance", 0)),
            "risk_tolerance": profile.get("risk_tolerance", "medium"),
            "max_position":   max_position_pct,
            "max_hk_ratio":   max_hk_pct,
            "max_sector":     max_sector_pct_lim,
            "is_default":     bool(profile.get("is_default")),
        },
    }


@ToolRegistry.register("portfolio_rebalance", definition=_REBALANCE_DEF, timeout=30)
async def _rebalance_tool(tc: ToolCall, bus) -> ToolResult:
    t0 = time.time()
    user_id = tc.args.get("user_id") or ""
    cash = float(tc.args.get("cash_available") or 0)
    try:
        summary = await _rebalance(str(user_id), cash)
    except Exception as e:
        return ToolResult.error_of(
            tc, "INTERNAL", f"portfolio_rebalance 失败: {type(e).__name__}: {e}",
            duration_ms=int((time.time() - t0) * 1000),
        )
    return ToolResult(
        tool_call=tc, status="ok",
        duration_ms=int((time.time() - t0) * 1000),
        summary=summary,
    )


# ═════════════════════════════════════════════════════════════════
# TOOL 2 · portfolio_stress
# ═════════════════════════════════════════════════════════════════

_STRESS_DEF = {
    "name": "portfolio_stress",
    "description": (
        "情景模拟：假设某只股跌/涨 X%，含行业联动效应，估算组合总损益。"
        "适合用户问『如果 {股票} 跌 20% 我组合会亏多少 / 极端情况我怎么样』时使用。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "内部用 · 由 orchestrator 上下文注入"},
            "shock_code": {"type": "string", "description": "冲击源股票代码"},
            "shock_pct": {"type": "number", "description": "冲击幅度百分比 · 例如 -20 表示跌 20%"},
            "sector_pass_through": {"type": "boolean", "default": True, "description": "是否含板块联动"},
        },
        "required": ["shock_code", "shock_pct"],
    },
}


async def _stress(user_id: str, shock_code: str, shock_pct: float,
                  pass_through: bool = True) -> dict:
    if not user_id:
        return {"type": "portfolio_stress", "error": "需要登录后才能做情景模拟"}
    try:
        rows = await asyncio.to_thread(list_stocks_with_thesis_by_user, user_id)
    except Exception as e:
        return {"type": "portfolio_stress", "error": f"拉持仓失败: {e}"}

    positions = [r for r in rows if r.get("shares") and r.get("cost_price")]
    if not positions:
        return {
            "type": "portfolio_stress",
            "empty": True,
            "hint": "需要先在持仓页录入 shares + cost_price 才能做情景模拟",
        }

    # 拿最新价，算当前市值
    async def _q(r):
        try:
            q = await asyncio.to_thread(fd.get_quote, r["code"])
            price = (q or {}).get("price") or r.get("cost_price") or 0
            return {**r, "current_price": price, "value": float(r["shares"]) * float(price)}
        except Exception:
            return {**r, "current_price": r.get("cost_price") or 0,
                    "value": float(r["shares"]) * float(r.get("cost_price") or 0)}

    positions_full = await asyncio.gather(*[_q(r) for r in positions])
    portfolio_value = sum(p["value"] for p in positions_full)

    shock_pct_frac = shock_pct / 100.0  # -0.2 for -20%
    shock_sector = _industry_of(shock_code)

    # 直接损失
    direct_position = next((p for p in positions_full if p["code"] == shock_code), None)
    if direct_position:
        direct_loss = direct_position["value"] * shock_pct_frac
        direct_meta = {
            "value": round(direct_loss, 2),
            "held_shares": int(direct_position["shares"]),
            "held_value": round(direct_position["value"], 2),
            "current_price": round(direct_position["current_price"], 2),
        }
    else:
        direct_loss = 0.0
        direct_meta = {
            "value": 0,
            "note": f"你的持仓里没有 {shock_code} · 直接损失为 0",
        }

    # 板块联动
    affected = []
    sector_loss = 0.0
    if pass_through:
        for p in positions_full:
            if p["code"] == shock_code:
                continue
            p_sector = _industry_of(p["code"])
            beta = _sector_beta(shock_sector, p_sector)
            if beta > 0:
                loss = p["value"] * shock_pct_frac * beta
                sector_loss += loss
                affected.append({
                    "code": p["code"], "name": p["name"],
                    "sector": p_sector, "beta_to_shock": round(beta, 2),
                    "loss": round(loss, 2),
                })
        affected.sort(key=lambda x: x["loss"])  # 亏最多的在前（loss 是负数）

    total_loss = direct_loss + sector_loss
    total_pct = (total_loss / portfolio_value * 100) if portfolio_value > 0 else 0

    # 修复建议：找当前该板块占比最高的仓位 · 建议减半
    sector_positions = [p for p in positions_full if _industry_of(p["code"]) == shock_sector]
    sector_current_pct = sum(p["value"] for p in sector_positions) / portfolio_value * 100 if portfolio_value else 0

    # Sprint 1 · 用 profile.max_sector 作 mitigation 目标（默认 40%）
    profile = await asyncio.to_thread(get_risk_profile, user_id)
    max_sector_pct_lim = float(profile.get("max_sector", 0.40)) * 100

    mitigation = ""
    if sector_current_pct > 20 and total_pct < -1:
        # 目标 = min(用户上限, 当前 × 0.6, 15%) · 取合理的下调点
        target_sector_pct = max(15, min(max_sector_pct_lim * 0.9, sector_current_pct * 0.6))
        halved_pct = total_pct * (target_sector_pct / sector_current_pct)
        mitigation = (
            f"若担心，可将 {shock_sector} 合计从 {sector_current_pct:.0f}% 降到 "
            f"{target_sector_pct:.0f}%，可将该冲击从 {total_pct:.1f}% 降到约 {halved_pct:.1f}%"
        )

    return {
        "type": "portfolio_stress",
        "scenario": f"{direct_position['name'] if direct_position else shock_code} 冲击 {shock_pct:+.0f}%",
        "shock_code": shock_code,
        "shock_pct": shock_pct,
        "shock_sector": shock_sector,
        "portfolio_value": round(portfolio_value, 2),
        "direct_loss": direct_meta,
        "sector_pass_through": {
            "value": round(sector_loss, 2),
            "enabled": pass_through,
            "affected_stocks": affected[:6],
        },
        "total_loss": {
            "value": round(total_loss, 2),
            "pct_of_portfolio": round(total_pct, 2),
        },
        "mitigation": mitigation,
    }


@ToolRegistry.register("portfolio_stress", definition=_STRESS_DEF, timeout=30)
async def _stress_tool(tc: ToolCall, bus) -> ToolResult:
    t0 = time.time()
    user_id = tc.args.get("user_id") or ""
    shock_code = tc.args.get("shock_code")
    shock_pct = tc.args.get("shock_pct")
    if not shock_code:
        return ToolResult.error_of(tc, "BAD_ARGS", "缺少 shock_code")
    if shock_pct is None:
        return ToolResult.error_of(tc, "BAD_ARGS", "缺少 shock_pct")
    pass_through = bool(tc.args.get("sector_pass_through", True))
    try:
        summary = await _stress(str(user_id), str(shock_code).strip(),
                                 float(shock_pct), pass_through)
    except Exception as e:
        return ToolResult.error_of(
            tc, "INTERNAL", f"portfolio_stress 失败: {type(e).__name__}: {e}",
            duration_ms=int((time.time() - t0) * 1000),
        )
    return ToolResult(
        tool_call=tc, status="ok",
        duration_ms=int((time.time() - t0) * 1000),
        summary=summary,
    )


# ═════════════════════════════════════════════════════════════════
# TOOL 3 · update_risk_profile (Sprint 1)
# ═════════════════════════════════════════════════════════════════

_PROFILE_DEF = {
    "name": "update_risk_profile",
    "description": (
        "读取或更新当前用户的风险偏好、可用现金、单票/港股/单行业上限。"
        "适合用户说『我风险偏保守 / 现金还有 5 万 / 单票别超过 20% / "
        "我风险画像是啥』等场景。所有字段都可选：只更新提到的字段，其他保留。"
        "更新后 portfolio_rebalance / portfolio_stress 会自动应用新约束。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "user_id": {
                "type": "string",
                "description": "内部用 · orchestrator 上下文注入",
            },
            "cash_balance": {
                "type": "number",
                "description": "可用现金 CNY · 不给则不改",
            },
            "risk_tolerance": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "风险容忍：保守=low / 稳健=medium / 进取=high",
            },
            "max_position": {
                "type": "number",
                "description": "单票上限 0.05-0.40（例：0.20 = 20%）",
            },
            "max_hk_ratio": {
                "type": "number",
                "description": "港股合计上限 0.0-1.0",
            },
            "max_sector": {
                "type": "number",
                "description": "单行业上限 0.0-1.0",
            },
            "read_only": {
                "type": "boolean",
                "default": False,
                "description": "为 True 时只读回当前 profile · 不写入",
            },
        },
        "required": [],
    },
}


def _fmt_profile_for_card(profile: dict, before: dict | None = None) -> dict:
    """结构化 profile 返回体 · 供 RiskProfileCard 渲染。"""
    def _pct(v):  # 0.25 → "25%"
        try:
            return f"{float(v) * 100:.0f}%"
        except Exception:
            return "-"

    tol_zh = {"low": "保守", "medium": "稳健", "high": "进取"}
    def _diff(k, old, new, formatter=lambda x: x):
        if old is None or before is None:
            return None
        if old == new:
            return None
        return {"field": k, "before": formatter(old), "after": formatter(new)}

    changes = []
    if before and not before.get("is_default"):
        for k in ("cash_balance", "risk_tolerance", "max_position", "max_hk_ratio", "max_sector"):
            d = _diff(k, before.get(k), profile.get(k),
                      formatter=(_pct if k.startswith("max_") else str))
            if d:
                changes.append(d)

    return {
        "type": "update_risk_profile",
        "profile": {
            "cash_balance":       float(profile.get("cash_balance", 0)),
            "cash_balance_label": f"¥{float(profile.get('cash_balance', 0)):,.0f}",
            "risk_tolerance":     profile.get("risk_tolerance", "medium"),
            "risk_tolerance_label": tol_zh.get(profile.get("risk_tolerance", "medium"), "稳健"),
            "max_position":       float(profile.get("max_position", 0.25)),
            "max_position_label": _pct(profile.get("max_position", 0.25)),
            "max_hk_ratio":       float(profile.get("max_hk_ratio", 0.40)),
            "max_hk_ratio_label": _pct(profile.get("max_hk_ratio", 0.40)),
            "max_sector":         float(profile.get("max_sector", 0.40)),
            "max_sector_label":   _pct(profile.get("max_sector", 0.40)),
            "is_default":         bool(profile.get("is_default")),
            "updated_at":         profile.get("updated_at"),
        },
        "changes": changes,
        "hint": (
            "已初始化默认风险画像 · 可对话修改" if profile.get("is_default")
            else ("已更新 · 下次问『我持仓怎么调』/『情景模拟』会自动应用"
                  if changes else "当前风险画像如上 · 可对话修改任意字段")
        ),
    }


@ToolRegistry.register("update_risk_profile", definition=_PROFILE_DEF, timeout=10)
async def _profile_tool(tc: ToolCall, bus) -> ToolResult:
    t0 = time.time()
    user_id = tc.args.get("user_id") or ""
    if not user_id:
        return ToolResult.error_of(tc, "BAD_ARGS", "缺少 user_id（未登录）")

    read_only = bool(tc.args.get("read_only"))
    # 判断是否任何写入字段被给出 · 都没给就当只读
    write_fields = ("cash_balance", "risk_tolerance",
                    "max_position", "max_hk_ratio", "max_sector")
    has_write = any(tc.args.get(k) is not None for k in write_fields)

    try:
        if read_only or not has_write:
            profile = await asyncio.to_thread(get_risk_profile, str(user_id))
            summary = _fmt_profile_for_card(profile, before=None)
        else:
            before = await asyncio.to_thread(get_risk_profile, str(user_id))
            profile = await asyncio.to_thread(
                upsert_risk_profile,
                str(user_id),
                cash_balance=tc.args.get("cash_balance"),
                risk_tolerance=tc.args.get("risk_tolerance"),
                max_position=tc.args.get("max_position"),
                max_hk_ratio=tc.args.get("max_hk_ratio"),
                max_sector=tc.args.get("max_sector"),
            )
            summary = _fmt_profile_for_card(profile, before=before)
    except ValueError as e:
        return ToolResult.error_of(tc, "BAD_ARGS", str(e),
                                    duration_ms=int((time.time() - t0) * 1000))
    except Exception as e:
        return ToolResult.error_of(
            tc, "INTERNAL", f"update_risk_profile 失败: {type(e).__name__}: {e}",
            duration_ms=int((time.time() - t0) * 1000),
        )

    return ToolResult(
        tool_call=tc, status="ok",
        duration_ms=int((time.time() - t0) * 1000),
        summary=summary,
    )
