"""6 大维度 90 项基本面体检指标字典（`apps/api/app/services/fundamentals/catalog.py`）。

严格按照详细开发方案 §4.3 与任务书要求，划分为 6 大维度共 90 项指标：
1. 盈利能力（Profitability，18 项）
2. 成长能力（Growth，15 项）
3. 偿债与安全性（Solvency & Safety，16 项）
4. 现金流质量（Cash Flow Quality，15 项）
5. 估值水平（Valuation，14 项）
6. 公司治理与股东（Governance & Shareholders，12 项）
合计：18 + 15 + 16 + 15 + 14 + 12 = 90 项。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

FundamentalDimension = Literal[
    "盈利能力",
    "成长能力",
    "偿债与安全性",
    "现金流质量",
    "估值水平",
    "公司治理与股东",
]


@dataclass(frozen=True)
class FundamentalMetricSpec:
    """单项基本面指标元数据定义。"""

    code: str
    name: str
    dimension: FundamentalDimension
    unit: str
    direction: Literal["higher_better", "lower_better", "neutral"]
    formula_desc: str


FUNDAMENTAL_DIMENSIONS_ORDER: list[FundamentalDimension] = [
    "盈利能力",
    "成长能力",
    "偿债与安全性",
    "现金流质量",
    "估值水平",
    "公司治理与股东",
]

FUNDAMENTAL_CATALOG: list[FundamentalMetricSpec] = [
    # ── 1. 盈利能力（18 项）─────────────────────────────────────────────────
    FundamentalMetricSpec("ROE_WEIGHTED", "加权净资产收益率(ROE)", "盈利能力", "%", "higher_better", "归母净利润 / 加权平均净资产"),
    FundamentalMetricSpec("ROE_DILUTED", "摊薄净资产收益率", "盈利能力", "%", "higher_better", "归母净利润 / 期末归母净资产"),
    FundamentalMetricSpec("ROE_DEDUCTED", "扣非加权ROE", "盈利能力", "%", "higher_better", "扣非归母净利润 / 加权平均净资产"),
    FundamentalMetricSpec("ROA", "总资产报酬率(ROA)", "盈利能力", "%", "higher_better", "净利润 / 平均总资产"),
    FundamentalMetricSpec("ROIC", "投入资本回报率(ROIC)", "盈利能力", "%", "higher_better", "NOPAT / 投入资本"),
    FundamentalMetricSpec("ROCE", "已动用资本回报率(ROCE)", "盈利能力", "%", "higher_better", "EBIT / (总资产 - 流动负债)"),
    FundamentalMetricSpec("GROSS_MARGIN", "销售毛利率", "盈利能力", "%", "higher_better", "(营业收入 - 营业成本) / 营业收入"),
    FundamentalMetricSpec("NET_MARGIN", "销售净利率", "盈利能力", "%", "higher_better", "净利润 / 营业收入"),
    FundamentalMetricSpec("DEDUCTED_NET_MARGIN", "扣非销售净利率", "盈利能力", "%", "higher_better", "扣非归母净利润 / 营业收入"),
    FundamentalMetricSpec("OPERATING_MARGIN", "营业利润率", "盈利能力", "%", "higher_better", "营业利润 / 营业收入"),
    FundamentalMetricSpec("EBIT_MARGIN", "息税前利润率(EBIT Margin)", "盈利能力", "%", "higher_better", "EBIT / 营业收入"),
    FundamentalMetricSpec("EBITDA_MARGIN", "息税折旧摊销前利润率(EBITDA Margin)", "盈利能力", "%", "higher_better", "EBITDA / 营业收入"),
    FundamentalMetricSpec("COST_TO_INCOME_RATIO", "营业总成本率", "盈利能力", "%", "lower_better", "营业总成本 / 营业收入"),
    FundamentalMetricSpec("SELLING_EXPENSE_RATIO", "销售费用率", "盈利能力", "%", "lower_better", "销售费用 / 营业收入"),
    FundamentalMetricSpec("ADMIN_EXPENSE_RATIO", "管理费用率", "盈利能力", "%", "lower_better", "管理费用 / 营业收入"),
    FundamentalMetricSpec("RD_EXPENSE_RATIO", "研发费用率", "盈利能力", "%", "higher_better", "研发费用 / 营业收入"),
    FundamentalMetricSpec("FIN_EXPENSE_RATIO", "财务费用率", "盈利能力", "%", "lower_better", "财务费用 / 营业收入"),
    FundamentalMetricSpec("CORE_PROFIT_RATIO", "核心利润占总利润比", "盈利能力", "%", "higher_better", "扣非净利润 / 归母净利润"),

    # ── 2. 成长能力（15 项）─────────────────────────────────────────────────
    FundamentalMetricSpec("REV_YOY", "营业收入同比增长率", "成长能力", "%", "higher_better", "(本期营收 - 去年同期营收) / 去年同期营收"),
    FundamentalMetricSpec("REV_QOQ", "单季营业收入环比增长率", "成长能力", "%", "higher_better", "(本季营收 - 上季营收) / 上季营收"),
    FundamentalMetricSpec("REV_CAGR_3Y", "营业收入3年复合增速(CAGR)", "成长能力", "%", "higher_better", "(本期营收 / 3年前营收)^(1/3) - 1"),
    FundamentalMetricSpec("REV_CAGR_5Y", "营业收入5年复合增速(CAGR)", "成长能力", "%", "higher_better", "(本期营收 / 5年前营收)^(1/5) - 1"),
    FundamentalMetricSpec("NET_PROFIT_YOY", "归母净利润同比增长率", "成长能力", "%", "higher_better", "(本期归母净利 - 去年同期) / 去年同期"),
    FundamentalMetricSpec("NET_PROFIT_QOQ", "单季归母净利润环比增长率", "成长能力", "%", "higher_better", "(本季归母净利 - 上季) / 上季"),
    FundamentalMetricSpec("DEDUCTED_NP_YOY", "扣非归母净利润同比增长率", "成长能力", "%", "higher_better", "(本期扣非净利 - 去年同期) / 去年同期"),
    FundamentalMetricSpec("DEDUCTED_NP_CAGR_3Y", "扣非归母净利润3年复合增速", "成长能力", "%", "higher_better", "(本期扣非净利 / 3年前)^(1/3) - 1"),
    FundamentalMetricSpec("OPERATING_PROFIT_YOY", "营业利润同比增长率", "成长能力", "%", "higher_better", "(本期营业利润 - 去年同期) / 去年同期"),
    FundamentalMetricSpec("EPS_YOY", "基本每股收益(EPS)同比增长率", "成长能力", "%", "higher_better", "(本期EPS - 去年同期EPS) / 去年同期EPS"),
    FundamentalMetricSpec("OCF_YOY", "经营活动现金流净额同比增长率", "成长能力", "%", "higher_better", "(本期经营现金流 - 去年同期) / 去年同期"),
    FundamentalMetricSpec("TOTAL_ASSETS_YOY", "总资产同比增长率", "成长能力", "%", "higher_better", "(期末总资产 - 去年同期) / 去年同期"),
    FundamentalMetricSpec("NET_ASSETS_YOY", "归母净资产同比增长率", "成长能力", "%", "higher_better", "(期末归母净资产 - 去年同期) / 去年同期"),
    FundamentalMetricSpec("SUSTAINABLE_GROWTH_RATE", "可持续增长率(SGR)", "成长能力", "%", "higher_better", "ROE * (1 - 分红率) / (1 - ROE * (1 - 分红率))"),
    FundamentalMetricSpec("RD_EXPENSE_YOY", "研发投入同比增长率", "成长能力", "%", "higher_better", "(本期研发费用 - 去年同期) / 去年同期"),

    # ── 3. 偿债与安全性（16 项）─────────────────────────────────────────────
    FundamentalMetricSpec("DEBT_TO_ASSET", "资产负债率", "偿债与安全性", "%", "lower_better", "总负债 / 总资产"),
    FundamentalMetricSpec("EQUITY_MULTIPLIER", "权益乘数(财务杠杆)", "偿债与安全性", "倍", "lower_better", "总资产 / 归母净资产"),
    FundamentalMetricSpec("CURRENT_RATIO", "流动比率", "偿债与安全性", "倍", "higher_better", "流动资产 / 流动负债"),
    FundamentalMetricSpec("QUICK_RATIO", "速动比率", "偿债与安全性", "倍", "higher_better", "(流动资产 - 存货 - 预付) / 流动负债"),
    FundamentalMetricSpec("CASH_RATIO", "现金比率", "偿债与安全性", "倍", "higher_better", "(货币资金 + 交易性金融资产) / 流动负债"),
    FundamentalMetricSpec("INTEREST_COVERAGE", "利息保障倍数(EBIT/利息支出)", "偿债与安全性", "倍", "higher_better", "EBIT / 利息费用"),
    FundamentalMetricSpec("NET_DEBT_TO_EQUITY", "净负债率(Net Gearing)", "偿债与安全性", "%", "lower_better", "(有息负债 - 货币资金) / 净资产"),
    FundamentalMetricSpec("INTEREST_DEBT_RATIO", "有息负债占总负债比", "偿债与安全性", "%", "lower_better", "有息负债 / 总负债"),
    FundamentalMetricSpec("SHORT_DEBT_TO_CASH", "现金短债比", "偿债与安全性", "倍", "higher_better", "货币资金 / 短期有息负债"),
    FundamentalMetricSpec("GOODWILL_TO_EQUITY", "商誉占净资产比例", "偿债与安全性", "%", "lower_better", "商誉 / 归母净资产"),
    FundamentalMetricSpec("INTANGIBLE_TO_ASSETS", "无形资产占总资产比", "偿债与安全性", "%", "lower_better", "无形资产 / 总资产"),
    FundamentalMetricSpec("AR_TO_REVENUE", "应收账款占营业收入比", "偿债与安全性", "%", "lower_better", "应收账款 / 营业收入"),
    FundamentalMetricSpec("INVENTORY_TO_ASSETS", "存货占总资产比", "偿债与安全性", "%", "lower_better", "存货 / 总资产"),
    FundamentalMetricSpec("CONTINGENT_LIABILITY_RATIO", "或有负债及对外担保占净资产比", "偿债与安全性", "%", "lower_better", "对外担保总额 / 归母净资产"),
    FundamentalMetricSpec("NON_CURRENT_LIAB_RATIO", "非流动负债占总负债比", "偿债与安全性", "%", "higher_better", "非流动负债 / 总负债"),
    FundamentalMetricSpec("ALTMAN_Z_SCORE", "Altman Z-Score 财务稳健指数", "偿债与安全性", "分", "higher_better", "多变量破产风险预警Z值(>2.99安全)"),

    # ── 4. 现金流质量（15 项）───────────────────────────────────────────────
    FundamentalMetricSpec("OCF_TO_NET_PROFIT", "净现比(经营现金流/净利润)", "现金流质量", "%", "higher_better", "经营活动现金流净额 / 净利润"),
    FundamentalMetricSpec("OCF_TO_REVENUE", "销售现金比率(经营现金流/营收)", "现金流质量", "%", "higher_better", "经营活动现金流净额 / 营业收入"),
    FundamentalMetricSpec("CASH_RECEIVED_TO_REV", "收现比(销售商品收现/营收)", "现金流质量", "%", "higher_better", "销售商品提供劳务收到的现金 / 营业收入"),
    FundamentalMetricSpec("FREE_CASH_FLOW", "企业自由现金流(FCFF)", "现金流质量", "亿元", "higher_better", "经营现金流净额 - 资本开支(CapEx)"),
    FundamentalMetricSpec("FCF_TO_NET_PROFIT", "自由现金流占净利润比", "现金流质量", "%", "higher_better", "自由现金流 / 归母净利润"),
    FundamentalMetricSpec("CAPEX_TO_OCF", "资本开支占经营现金流比", "现金流质量", "%", "lower_better", "购建固定无形资产支付现金 / 经营现金流净额"),
    FundamentalMetricSpec("CAPEX_TO_REVENUE", "资本开支占营收比(CapEx Intensity)", "现金流质量", "%", "neutral", "资本开支 / 营业收入"),
    FundamentalMetricSpec("AR_TURNOVER_DAYS", "应收账款周转天数(DSO)", "现金流质量", "天", "lower_better", "365 * 平均应收账款 / 营业收入"),
    FundamentalMetricSpec("INVENTORY_TURNOVER_DAYS", "存货周转天数(DIO)", "现金流质量", "天", "lower_better", "365 * 平均存货 / 营业成本"),
    FundamentalMetricSpec("AP_TURNOVER_DAYS", "应付账款周转天数(DPO)", "现金流质量", "天", "higher_better", "365 * 平均应付账款 / 营业成本"),
    FundamentalMetricSpec("CASH_CONVERSION_CYCLE", "现金转换周期(CCC)", "现金流质量", "天", "lower_better", "DSO + DIO - DPO"),
    FundamentalMetricSpec("TOTAL_ASSET_TURNOVER", "总资产周转率", "现金流质量", "次/年", "higher_better", "营业收入 / 平均总资产"),
    FundamentalMetricSpec("FIXED_ASSET_TURNOVER", "固定资产周转率", "现金流质量", "次/年", "higher_better", "营业收入 / 平均固定资产"),
    FundamentalMetricSpec("OCF_TO_CURRENT_LIAB", "现金流动负债比", "现金流质量", "%", "higher_better", "经营现金流净额 / 流动负债"),
    FundamentalMetricSpec("ACCRUAL_RATIO", "应计利润占比(Accruals/Assets)", "现金流质量", "%", "lower_better", "(净利润 - 经营现金流净额) / 总资产"),

    # ── 5. 估值水平（14 项）─────────────────────────────────────────────────
    FundamentalMetricSpec("PE_TTM", "滚动市盈率(PE-TTM)", "估值水平", "倍", "lower_better", "总市值 / 近12个月归母净利润"),
    FundamentalMetricSpec("PE_DYNAMIC", "动态市盈率(PE-Dynamic)", "估值水平", "倍", "lower_better", "总市值 / 年化预估归母净利润"),
    FundamentalMetricSpec("PE_PERCENTILE_5Y", "PE-TTM近5年历史分位数", "估值水平", "%", "lower_better", "当前PE在过去5年交易日中的百分位"),
    FundamentalMetricSpec("PB_MRQ", "最新市净率(PB-MRQ)", "估值水平", "倍", "lower_better", "总市值 / 最新报告期归母净资产"),
    FundamentalMetricSpec("PB_PERCENTILE_5Y", "PB-MRQ近5年历史分位数", "估值水平", "%", "lower_better", "当前PB在过去5年交易日中的百分位"),
    FundamentalMetricSpec("PS_TTM", "滚动市销率(PS-TTM)", "估值水平", "倍", "lower_better", "总市值 / 近12个月营业收入"),
    FundamentalMetricSpec("PCF_OCF_TTM", "市现率(P/OCF-TTM)", "估值水平", "倍", "lower_better", "总市值 / 近12个月经营现金流净额"),
    FundamentalMetricSpec("EV_TO_EBITDA", "企业价值倍数(EV/EBITDA)", "估值水平", "倍", "lower_better", "企业价值EV / EBITDA"),
    FundamentalMetricSpec("PEG", "市盈率相对盈利增长比率(PEG)", "估值水平", "倍", "lower_better", "PE-TTM / 归母净利润复合增长率"),
    FundamentalMetricSpec("DIVIDEND_YIELD_TTM", "滚动股息率(TTM)", "估值水平", "%", "higher_better", "近12个月每股现金分红 / 当前股价"),
    FundamentalMetricSpec("EARNINGS_YIELD", "盈利收益率(E/P)", "估值水平", "%", "higher_better", "1 / PE-TTM * 100"),
    FundamentalMetricSpec("FCF_YIELD", "自由现金流收益率(FCF Yield)", "估值水平", "%", "higher_better", "自由现金流 / 总市值 * 100"),
    FundamentalMetricSpec("TOTAL_MARKET_CAP", "总市值", "估值水平", "亿元", "neutral", "总股本 * 最新收盘价"),
    FundamentalMetricSpec("FREE_FLOAT_CAP", "自由流通市值", "估值水平", "亿元", "neutral", "自由流通股本 * 最新收盘价"),

    # ── 6. 公司治理与股东（12 项）───────────────────────────────────────────
    FundamentalMetricSpec("TOP1_HOLDING_PCT", "第一大股东持股比例", "公司治理与股东", "%", "neutral", "第一大股东持股数 / 总股本"),
    FundamentalMetricSpec("TOP10_HOLDING_PCT", "前十大股东合计持股比例", "公司治理与股东", "%", "higher_better", "前十大股东持股数合计 / 总股本"),
    FundamentalMetricSpec("TOP10_FLOAT_HOLDING_PCT", "前十大流通股东合计持股比例", "公司治理与股东", "%", "higher_better", "前十大流通股东持股数合计 / 流通股本"),
    FundamentalMetricSpec("SHAREHOLDER_COUNT", "最新股东总户数", "公司治理与股东", "户", "neutral", "报告期末普通股股东总数"),
    FundamentalMetricSpec("SHAREHOLDER_COUNT_QOQ", "股东户数较上季变动率", "公司治理与股东", "%", "lower_better", "(本期股东户数 - 上期) / 上期(负值代表筹码集中)"),
    FundamentalMetricSpec("INSTITUTIONAL_HOLDING_PCT", "机构投资者合计持股比例", "公司治理与股东", "%", "higher_better", "公募/社保/险资/北向等机构持股占流通股比例"),
    FundamentalMetricSpec("PLEDGE_RATIO_PCT", "大股东股权质押占总股本比例", "公司治理与股东", "%", "lower_better", "质押股数合计 / 总股本"),
    FundamentalMetricSpec("DIVIDEND_PAYOUT_RATIO", "现金分红率(股利支付率)", "公司治理与股东", "%", "higher_better", "年度现金分红总额 / 归母净利润"),
    FundamentalMetricSpec("CONSECUTIVE_DIVIDEND_YEARS", "连续现金分红年数", "公司治理与股东", "年", "higher_better", "连续实施现金派息的会计年度数"),
    FundamentalMetricSpec("UNLOCK_RATIO_NEXT_12M", "未来12个月限售解禁占总股本比", "公司治理与股东", "%", "lower_better", "未来12个月待解禁限售股 / 总股本"),
    FundamentalMetricSpec("AUDIT_OPINION_SCORE", "最近年度审计意见合规分", "公司治理与股东", "分", "higher_better", "标准无保留意见=100,带强调事项=70,保留/无法表示=0"),
    FundamentalMetricSpec("BUYBACK_RATIO_TTM", "近12个月股份回购占总股本比", "公司治理与股东", "%", "higher_better", "近12个月已实施回购股份数 / 总股本"),
]

FUNDAMENTAL_BY_CODE: dict[str, FundamentalMetricSpec] = {
    spec.code: spec for spec in FUNDAMENTAL_CATALOG
}


def get_fundamental_catalog_summary() -> dict[str, Any]:
    """返回 6 大维度 90 项基本面指标分类统计与目录清单。"""
    by_dim: dict[str, list[dict[str, Any]]] = {dim: [] for dim in FUNDAMENTAL_DIMENSIONS_ORDER}
    for spec in FUNDAMENTAL_CATALOG:
        by_dim[spec.dimension].append(
            {
                "code": spec.code,
                "name": spec.name,
                "dimension": spec.dimension,
                "unit": spec.unit,
                "direction": spec.direction,
                "formula_desc": spec.formula_desc,
            }
        )
    return {
        "total_count": len(FUNDAMENTAL_CATALOG),
        "dimension_count": len(FUNDAMENTAL_DIMENSIONS_ORDER),
        "counts_by_dimension": {dim: len(items) for dim, items in by_dim.items()},
        "dimensions": by_dim,
    }
