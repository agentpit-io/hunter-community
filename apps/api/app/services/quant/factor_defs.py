"""因子定义 · 20 因子清单(Phase A 只启用 3 个)
(见 doc/开源hunter-community/参考/11量化策略/quant-strategy-tech-plan.md §4)

Phase A 启用:pe_inv / roe / momentum_12m_1m
Phase B 补齐:剩下 17 个
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal


Cat = Literal["价值", "质量", "成长", "动量", "ML", "技术", "资金", "波动"]


@dataclass
class FactorDef:
    key: str
    cat: Cat
    name: str
    icon: str
    desc: str
    reverse: bool = False       # 反向因子(值越小越好 · 如 momentum_1m 反转)
    enabled: bool = True        # Phase B 默认全启用 · 未来接的因子放 False


ALL_FACTORS: list[FactorDef] = [
    # ── 价值 ──
    FactorDef("pe_inv",         "价值", "市盈率倒数",     "💰", "1 / TTM PE · 低估值分数高", enabled=True),
    FactorDef("pb_inv",         "价值", "市净率倒数",     "💰", "1 / PB · 破净股偏防御"),
    FactorDef("dividend_yield", "价值", "股息率",         "💰", "近 12 月现金分红 / 当日 close(A 股派息单位元/10股)"),
    FactorDef("ev_ebitda_inv",  "价值", "EV/EBITDA 倒数", "💰", "企业价值 / 息税折旧摊销前利润 · Phase C 跳过 · Phase D 补(见 05 §13.1)", enabled=False),

    # ── 质量 ──
    FactorDef("roe",            "质量", "ROE",           "🏆", "TTM 净利润 / 平均归母权益", enabled=True),
    FactorDef("roa",            "质量", "ROA",           "🏆", "剔除杠杆的经营效率"),
    FactorDef("gross_margin",   "质量", "毛利率",        "🏆", "(营收 - 成本) / 营收"),
    FactorDef("debt_ratio_inv", "质量", "1/负债率",      "🏆", "低杠杆抗风险"),

    # ── 成长 ──
    FactorDef("revenue_growth_yoy",  "成长", "营收同比", "🚀", "最近季营收 vs 去年"),
    FactorDef("earnings_growth_yoy", "成长", "净利同比", "🚀", "最近季归母净利 vs 去年"),

    # ── 动量 ──
    FactorDef("momentum_1m",     "动量", "1 月动量",    "📈", "近 20 日涨幅 · 短反转", reverse=True),
    FactorDef("momentum_6m",     "动量", "6 月动量",    "📈", "近 120 日涨幅"),
    FactorDef("momentum_12m_1m", "动量", "12M-1M 动量", "📈", "剔除最近 1 月的 11 月涨幅 · 学术经典", enabled=True),

    # ── ML / 技术 / 资金 ──
    FactorDef("kronos",    "ML",   "Kronos 技术",     "🧠", "清华 Kronos 时序大模型未来 5 日预测收益率"),
    FactorDef("ma_align",  "技术", "均线趋势",        "📉", "MA5/10/20/60 多头排列打分"),
    FactorDef("macd",      "技术", "MACD 动量",       "📉", "MACD_bar / ATR14"),
    FactorDef("main_flow", "资金", "主力净流入",      "💵", "近 5 日超大单+大单净流入 / 5 日总资金流"),
    FactorDef("rsi",       "技术", "RSI 超买卖",      "📉", "RSI14 分段映射"),

    # ── 波动 / 其他 ──
    FactorDef("vol_20d_inv", "波动", "低波动",       "🛡", "1 / 20 日收益标准差"),
    FactorDef("candle_5d",   "技术", "近 5 日 K 线", "📉", "近 5 日阳线比例"),
]


def get_factor(key: str) -> FactorDef | None:
    for f in ALL_FACTORS:
        if f.key == key:
            return f
    return None


def enabled_factors() -> list[FactorDef]:
    return [f for f in ALL_FACTORS if f.enabled]


# 分类展示顺序
CAT_ORDER: list[Cat] = ["价值", "质量", "成长", "动量", "ML", "技术", "资金", "波动"]
