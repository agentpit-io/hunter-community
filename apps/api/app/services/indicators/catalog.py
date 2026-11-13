"""7 大类 56 项标准技术指标字典（`apps/api/app/services/indicators/catalog.py`）。

对标证券公司专业投研终端（如银河证券 WorkBuddy 7 大类 56 项技术指标体系），
将 56 项指标按 7 大类别严格标准化：
1. 趋势型（Trend，18 项）：
   MA5, MA10, MA20, MA30, MA60, MA120, MA250,
   EMA12, EMA26, EMA50, WMA20, VWAP,
   MACD, DMI_PDI, DMI_MDI, ADX, ADXR, SAR
2. 超买超卖型（Oscillator，11 项）：
   RSI6, RSI12, RSI14, KDJ_K, KDJ_D, KDJ_J,
   WR14, CCI14, ROC12, MTM12, BIAS20
3. 能量型（Energy，7 项）：
   OBV, VR26, CR26, AR26, BR26, PSY12, EMV14
4. 成交量型（Volume，6 项）：
   VOL_MA5, VOL_MA20, VOL_RATIO, TURNOVER_RATE, VOSC, MFI14
5. 通道路径型（Channel，6 项）：
   BOLL_UPPER, BOLL_MID, BOLL_LOWER, KELTNER_CHN, DONCHIAN_CHN, ENE_CHN
6. 波动率型（Volatility，4 项）：
   ATR14, NATR14, HV20, BOLL_WIDTH
7. 市场广度型（Breadth，4 项）：
   ADL, ADR10, ABI10,trin_arms (ARMS10)

合计：18 + 11 + 7 + 6 + 6 + 4 + 4 = 56 项。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

IndicatorCategory = Literal[
    "趋势型",
    "超买超卖型",
    "能量型",
    "成交量型",
    "通道路径型",
    "波动率型",
    "市场广度型",
]


@dataclass(frozen=True)
class IndicatorSpec:
    """单项技术指标元数据定义。"""

    code: str
    name: str
    category: IndicatorCategory
    default_params: dict[str, Any]
    unit: str
    min_bars: int
    requires_adjustment: bool = True
    description: str = ""


INDICATOR_CATALOG: list[IndicatorSpec] = [
    # ── 1. 趋势型（18 项）──────────────────────────────────────────────────
    IndicatorSpec("MA5", "5日简单移动平均线", "趋势型", {"period": 5}, "元", 5, True, "短期均线支撑与压力"),
    IndicatorSpec("MA10", "10日简单移动平均线", "趋势型", {"period": 10}, "元", 10, True, "短中期均线趋势"),
    IndicatorSpec("MA20", "20日简单移动平均线", "趋势型", {"period": 20}, "元", 20, True, "月线生命线"),
    IndicatorSpec("MA30", "30日简单移动平均线", "趋势型", {"period": 30}, "元", 30, True, "中期趋势均线"),
    IndicatorSpec("MA60", "60日简单移动平均线", "趋势型", {"period": 60}, "元", 60, True, "季线多空分水岭"),
    IndicatorSpec("MA120", "120日简单移动平均线", "趋势型", {"period": 120}, "元", 120, True, "半年线牛熊参考"),
    IndicatorSpec("MA250", "250日简单移动平均线", "趋势型", {"period": 250}, "元", 250, True, "年线长期趋势"),
    IndicatorSpec("EMA12", "12日指数移动平均线", "趋势型", {"period": 12}, "元", 12, True, "MACD快线基础EMA"),
    IndicatorSpec("EMA26", "26日指数移动平均线", "趋势型", {"period": 26}, "元", 26, True, "MACD慢线基础EMA"),
    IndicatorSpec("EMA50", "50日指数移动平均线", "趋势型", {"period": 50}, "元", 50, True, "中周期指数均线"),
    IndicatorSpec("WMA20", "20日加权移动平均线", "趋势型", {"period": 20}, "元", 20, True, "线性加权移动平均"),
    IndicatorSpec("VWAP", "20日成交量加权平均价", "趋势型", {"period": 20}, "元", 20, True, "机构交易成本均价线"),
    IndicatorSpec("MACD", "平滑异同移动平均线(DIF/DEA/HIST)", "趋势型", {"fast": 12, "slow": 26, "signal": 9}, "点", 35, True, "中短期趋势与动能金叉死叉"),
    IndicatorSpec("DMI_PDI", "动向指标上升方向线(+DI)", "趋势型", {"period": 14}, "%", 15, True, "多方买盘推动力度"),
    IndicatorSpec("DMI_MDI", "动向指标下降方向线(-DI)", "趋势型", {"period": 14}, "%", 15, True, "空方卖盘打压力度"),
    IndicatorSpec("ADX", "平均趋向指数(ADX)", "趋势型", {"period": 14}, "点", 28, True, "趋势强度度量(>25为强趋势)"),
    IndicatorSpec("ADXR", "趋向评估数值(ADXR)", "趋势型", {"period": 14}, "点", 35, True, "ADX平滑稳健趋势指标"),
    IndicatorSpec("SAR", "抛物线转向指标(SAR)", "趋势型", {"step": 0.02, "max_step": 0.2}, "元", 10, True, "止损与趋势反转跟踪点"),

    # ── 2. 超买超卖型（11 项）──────────────────────────────────────────────
    IndicatorSpec("RSI6", "6日相对强弱指标", "超买超卖型", {"period": 6}, "点", 7, True, "短线灵敏超买(>80)超卖(<20)"),
    IndicatorSpec("RSI12", "12日相对强弱指标", "超买超卖型", {"period": 12}, "点", 13, True, "中短线相对强弱"),
    IndicatorSpec("RSI14", "14日标准相对强弱指标", "超买超卖型", {"period": 14}, "点", 15, True, "国际标准RSI(>70超买,<30超卖)"),
    IndicatorSpec("KDJ_K", "随机指标K值", "超买超卖型", {"n": 9, "m1": 3, "m2": 3}, "点", 9, True, "KDJ快线"),
    IndicatorSpec("KDJ_D", "随机指标D值", "超买超卖型", {"n": 9, "m1": 3, "m2": 3}, "点", 9, True, "KDJ慢线"),
    IndicatorSpec("KDJ_J", "随机指标J值", "超买超卖型", {"n": 9, "m1": 3, "m2": 3}, "点", 9, True, "KDJ方向敏感线(>100超买,<0超卖)"),
    IndicatorSpec("WR14", "14日威廉指标(W%R)", "超买超卖型", {"period": 14}, "%", 14, True, "威廉超买(<-20)超卖(>-80)"),
    IndicatorSpec("CCI14", "14日顺势指标(CCI)", "超买超卖型", {"period": 14}, "点", 14, True, "极端常态偏离指标(>100/<-100)"),
    IndicatorSpec("ROC12", "12日变动率指标(ROC)", "超买超卖型", {"period": 12}, "%", 13, True, "价格动量变动百分比"),
    IndicatorSpec("MTM12", "12日动量线(MTM)", "超买超卖型", {"period": 12}, "元", 13, True, "价格绝对动量差"),
    IndicatorSpec("BIAS20", "20日乖离率(BIAS)", "超买超卖型", {"period": 20}, "%", 20, True, "收盘价偏离20日均线百分比"),

    # ── 3. 能量型（7 项）───────────────────────────────────────────────────
    IndicatorSpec("OBV", "能量潮指标(OBV)", "能量型", {"scale": 1.0}, "股", 5, True, "量价累积能量方向"),
    IndicatorSpec("VR26", "26日成交量变异率(VR)", "能量型", {"period": 26}, "%", 27, True, "上涨日与下跌日成交量比值"),
    IndicatorSpec("CR26", "26日带状能量线(CR)", "能量型", {"period": 26}, "点", 27, True, "中间价动量能量指标"),
    IndicatorSpec("AR26", "26日人气指标(AR)", "能量型", {"period": 26}, "点", 26, True, "开盘价位置反映市场买卖人气"),
    IndicatorSpec("BR26", "26日意愿指标(BR)", "能量型", {"period": 26}, "点", 27, True, "昨收位置反映市场追涨意愿"),
    IndicatorSpec("PSY12", "12日心理线指标(PSY)", "能量型", {"period": 12}, "%", 13, True, "近12日上涨天数占比(>75超买,<25超卖)"),
    IndicatorSpec("EMV14", "14日简易波动指标(EMV)", "能量型", {"period": 14}, "点", 15, True, "单位成交量推动价格波动效率"),

    # ── 4. 成交量型（6 项）─────────────────────────────────────────────────
    IndicatorSpec("VOL_MA5", "5日均量线", "成交量型", {"period": 5}, "股", 5, False, "短期成交量均值"),
    IndicatorSpec("VOL_MA20", "20日均量线", "成交量型", {"period": 20}, "股", 20, False, "月度成交量均值"),
    IndicatorSpec("VOL_RATIO", "5日量比指标", "成交量型", {"period": 5}, "倍", 6, False, "当日成交量与前5日均量比值"),
    IndicatorSpec("TURNOVER_RATE", "当日换手率", "成交量型", {}, "%", 1, False, "成交股数占流通总股本比例"),
    IndicatorSpec("VOSC", "成交量振荡器(VOSC 12,26)", "成交量型", {"short": 12, "long": 26}, "%", 26, False, "短长均量差值百分比"),
    IndicatorSpec("MFI14", "14日资金流量指标(MFI)", "成交量型", {"period": 14}, "点", 15, True, "成交量加权的RSI(>80超买,<20超卖)"),

    # ── 5. 通道路径型（6 项）───────────────────────────────────────────────
    IndicatorSpec("BOLL_UPPER", "布林带上轨(BOLL Upper)", "通道路径型", {"period": 20, "std_dev": 2.0}, "元", 20, True, "20日均线+2倍标准差阻力轨"),
    IndicatorSpec("BOLL_MID", "布林带中轨(BOLL Mid)", "通道路径型", {"period": 20, "std_dev": 2.0}, "元", 20, True, "20日均线中枢"),
    IndicatorSpec("BOLL_LOWER", "布林带下轨(BOLL Lower)", "通道路径型", {"period": 20, "std_dev": 2.0}, "元", 20, True, "20日均线-2倍标准差支撑轨"),
    IndicatorSpec("KELTNER_CHN", "肯特纳通道中枢与上下轨(KC20)", "通道路径型", {"period": 20, "atr_mult": 2.0}, "元", 20, True, "EMA20±2倍ATR14波动通道"),
    IndicatorSpec("DONCHIAN_CHN", "唐奇安20日高低通道(DC20)", "通道路径型", {"period": 20}, "元", 20, True, "过去20日最高价与最低价突破通道"),
    IndicatorSpec("ENE_CHN", "轨道线指标(ENE 10,11,9)", "通道路径型", {"period": 10, "upper_pct": 11.0, "lower_pct": 9.0}, "元", 10, True, "均线百分比包络轨道线"),

    # ── 6. 波动率型（4 项）─────────────────────────────────────────────────
    IndicatorSpec("ATR14", "14日真实波幅均值(ATR)", "波动率型", {"period": 14}, "元", 15, True, "海龟法则标准真实波幅"),
    IndicatorSpec("NATR14", "14日归一化真实波幅(NATR)", "波动率型", {"period": 14}, "%", 15, True, "ATR占收盘价百分比"),
    IndicatorSpec("HV20", "20日年化历史波动率(HV20)", "波动率型", {"period": 20, "trading_days": 252}, "%", 21, True, "对数收益率20日标准差年化"),
    IndicatorSpec("BOLL_WIDTH", "布林带带宽百分比(Bandwidth)", "波动率型", {"period": 20, "std_dev": 2.0}, "%", 20, True, "(上轨-下轨)/中轨*100衡量收敛扩张"),

    # ── 7. 市场广度型（4 项）───────────────────────────────────────────────
    IndicatorSpec("ADL", "腾落指数(Advance-Decline Line)", "市场广度型", {"window": 20}, "家", 5, False, "每日上涨家数减下跌家数累计线"),
    IndicatorSpec("ADR10", "10日涨跌比指标(ADR)", "市场广度型", {"period": 10}, "倍", 10, False, "10日上涨家数总和与下跌家数总和之比"),
    IndicatorSpec("ABI10", "10日绝对广度指标(ABI)", "市场广度型", {"period": 10}, "家", 10, False, "10日涨跌家数绝对差均值"),
    IndicatorSpec("ARMS10", "10日阿姆氏指数(TRIN/ARMS)", "市场广度型", {"period": 10}, "点", 10, False, "市场涨跌广度与量能综合平衡度"),
]

INDICATOR_BY_CODE: dict[str, IndicatorSpec] = {spec.code: spec for spec in INDICATOR_CATALOG}

CATEGORIES_ORDER: list[IndicatorCategory] = [
    "趋势型",
    "超买超卖型",
    "能量型",
    "成交量型",
    "通道路径型",
    "波动率型",
    "市场广度型",
]


def get_indicator_catalog_summary() -> dict[str, Any]:
    """返回 7 大类 56 项技术指标分类统计与目录清单。"""
    by_cat: dict[str, list[dict[str, Any]]] = {cat: [] for cat in CATEGORIES_ORDER}
    for spec in INDICATOR_CATALOG:
        by_cat[spec.category].append(
            {
                "code": spec.code,
                "name": spec.name,
                "category": spec.category,
                "default_params": spec.default_params,
                "unit": spec.unit,
                "min_bars": spec.min_bars,
                "requires_adjustment": spec.requires_adjustment,
                "description": spec.description,
            }
        )
    return {
        "total_count": len(INDICATOR_CATALOG),
        "category_count": len(CATEGORIES_ORDER),
        "counts_by_category": {cat: len(items) for cat, items in by_cat.items()},
        "categories": by_cat,
    }
