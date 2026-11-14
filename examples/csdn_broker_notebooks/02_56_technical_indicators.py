#!/usr/bin/env python3
"""实战 02：7 大类 56 项技术指标确定性计算与多空信号冲突诊断。

对应 CSDN × 证券公司开发者实战示例包（M5-2）：
- 从券商接口/确定性序列获取 260 根前复权日 K 线；
- 调用 `calculate_all_56_indicators` 纯代码计算趋势型（18项）、超买超卖型（11项）、能量型（7项）、
  成交量型（6项）、通道路径型（6项）、波动率型（4项）、市场广度型（4项）共 56 项指标；
- 输出各维度得分、多空信号汇总以及指标间冲突诊断（如均线多头但 RSI/KDJ 短线超买）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.providers.broker.public_adapter import build_deterministic_kline_series  # noqa: E402
from app.services.indicators import (  # noqa: E402
    calculate_all_56_indicators,
    get_indicator_catalog_summary,
)


def run_demo(symbol: str = "600036.SH") -> dict:
    bars = build_deterministic_kline_series(symbol, adjustment="前复权", bars=260)
    catalog_info = get_indicator_catalog_summary()

    calc_res = calculate_all_56_indicators(
        bars=bars,
        adjustment="前复权",
        symbol=symbol,
    )

    indicators = calc_res["indicators"]
    sample_keys = ["MA20", "MACD", "RSI14", "KDJ_J", "BOLL_UPPER", "ATR14", "OBV", "ADX14"]
    sample_metrics = {k: indicators.get(k) for k in sample_keys if k in indicators}

    return {
        "example": "02_56_technical_indicators",
        "symbol": symbol,
        "adjustment": calc_res["adjustment"],
        "total_catalog_indicators": catalog_info["total_count"],
        "computed_indicators_count": calc_res["computed_indicators"],
        "counts_by_category": catalog_info["counts_by_category"],
        "conflicts_detected": calc_res["signal_conflicts"],
        "sample_indicators": sample_metrics,
        "disclaimer": "本技术指标计算结果全部由后端 Python 引擎确定性算出，仅供研究参考，不构成任何投资建议。",
    }


if __name__ == "__main__":
    output = run_demo()
    print(json.dumps(output, ensure_ascii=False, indent=2))
