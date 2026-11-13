"""6 大维度 90 项基本面体检计算引擎（`apps/api/app/services/fundamentals`）。"""
from .calculator import calculate_all_90_fundamentals, default_fundamental_raw_input
from .catalog import (
    FUNDAMENTAL_BY_CODE,
    FUNDAMENTAL_CATALOG,
    FUNDAMENTAL_DIMENSIONS_ORDER,
    FundamentalDimension,
    FundamentalMetricSpec,
    get_fundamental_catalog_summary,
)

__all__ = [
    "FUNDAMENTAL_BY_CODE",
    "FUNDAMENTAL_CATALOG",
    "FUNDAMENTAL_DIMENSIONS_ORDER",
    "FundamentalDimension",
    "FundamentalMetricSpec",
    "get_fundamental_catalog_summary",
    "calculate_all_90_fundamentals",
    "default_fundamental_raw_input",
]
