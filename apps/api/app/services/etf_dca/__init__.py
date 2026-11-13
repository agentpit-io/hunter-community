"""ETF 定投计算器确定性计算引擎（`apps/api/app/services/etf_dca/`）。

覆盖开发方案 §4.3 与测试方案 TC-CALC-03：
1. 正向定投现金流与历史净值回测（算出累计投入本金、总份额、期末市值、持仓成本、内部收益率 IRR、最大回撤）；
2. 目标金额反推（给定未来目标金额与期限，在不同历史年化假设下反推每期需投入金额）；
3. 定投 vs 一次性买入同区间对比；
4. 强制附带历史假设免责声明：“基于历史净值与假设参数测算，历史表现不代表未来收益”。
"""
from .calculator import (
    MANDATORY_DCA_DISCLAIMER,
    calculate_dca_vs_lump_sum,
    calculate_etf_dca_backtest,
    calculate_etf_dca_full_plan,
    calculate_target_reverse_plan,
    compute_xirr,
)

__all__ = [
    "MANDATORY_DCA_DISCLAIMER",
    "calculate_dca_vs_lump_sum",
    "calculate_etf_dca_backtest",
    "calculate_etf_dca_full_plan",
    "calculate_target_reverse_plan",
    "compute_xirr",
]
