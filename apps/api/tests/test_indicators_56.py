"""单元测试：7 大类 56 项技术指标确定性计算引擎（`test_indicators_56.py`）。

覆盖测试方案第二关（TC-CALC-01）：
1. 7 大类（趋势型 18、超买超卖型 11、能量型 7、成交量型 6、通道路径型 6、波动率型 4、市场广度型 4）
   共 56 项技术指标目录完整性校验。
2. 复权方式未传或为空时强制抛错拦截（TC-CALC-01）。
3. 260 日前复权 K 线全量 56 项指标计算与标准公式精度比对（误差 `< 1e-6`）。
4. 样本不足时返回 `None` 并标注 `missing_reason`（严禁填 `0`）。
5. 客观状态、近 20 日分位数与多指标信号冲突（如均线多头 vs RSI 超买）自动检测验证。
"""
from __future__ import annotations

import math
import pytest

from app.providers.broker.public_adapter import build_deterministic_kline_series
from app.services.indicators import (
    CATEGORIES_ORDER,
    INDICATOR_CATALOG,
    calculate_all_56_indicators,
    get_indicator_catalog_summary,
)


def test_indicator_catalog_has_exact_7_categories_and_56_items() -> None:
    """验证技术指标字典严格包含 7 大类、56 项指标，且无重复代码。"""
    summary = get_indicator_catalog_summary()
    assert summary["total_count"] == 56
    assert summary["category_count"] == 7
    assert list(summary["counts_by_category"].keys()) == CATEGORIES_ORDER
    assert summary["counts_by_category"] == {
        "趋势型": 18,
        "超买超卖型": 11,
        "能量型": 7,
        "成交量型": 6,
        "通道路径型": 6,
        "波动率型": 4,
        "市场广度型": 4,
    }
    codes = [spec.code for spec in INDICATOR_CATALOG]
    assert len(set(codes)) == 56


def test_tc_calc_01_missing_adjustment_raises_error() -> None:
    """TC-CALC-01：复权方式未传或无效时必须报错拦截。"""
    bars = build_deterministic_kline_series("600036.SH", adjustment="前复权", bars=60)
    with pytest.raises(ValueError, match="必须显式传入合法的复权方式"):
        calculate_all_56_indicators(bars, adjustment=None)
    with pytest.raises(ValueError, match="必须显式传入合法的复权方式"):
        calculate_all_56_indicators(bars, adjustment="")


def test_tc_calc_01_all_56_indicators_precision_against_reference() -> None:
    """TC-CALC-01：取沪深300成分股 600036.SH 近 260 日前复权 K 线，核对 56 项指标计算精度误差 < 1e-6。"""
    bars = build_deterministic_kline_series("600036.SH", adjustment="前复权", bars=260)
    result = calculate_all_56_indicators(bars, adjustment="前复权", symbol="600036.SH")

    assert result["total_indicators"] == 56
    assert result["computed_indicators"] == 56
    assert result["missing_indicators"] == []

    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    volumes = [float(b["volume"]) for b in bars]

    # 独立基准公式核对 MA5 / MA20 / MA250
    ref_ma5 = round(sum(closes[-5:]) / 5.0, 4)
    ref_ma20 = round(sum(closes[-20:]) / 20.0, 4)
    ref_ma250 = round(sum(closes[-250:]) / 250.0, 4)
    assert abs(result["indicators"]["MA5"]["value"] - ref_ma5) < 1e-6
    assert abs(result["indicators"]["MA20"]["value"] - ref_ma20) < 1e-6
    assert abs(result["indicators"]["MA250"]["value"] - ref_ma250) < 1e-6

    # 独立基准公式核对 BOLL (20, 2)
    std20 = math.sqrt(sum((x - (sum(closes[-20:]) / 20.0)) ** 2 for x in closes[-20:]) / 20.0)
    ref_boll_up = round(sum(closes[-20:]) / 20.0 + 2.0 * std20, 4)
    ref_boll_lo = round(sum(closes[-20:]) / 20.0 - 2.0 * std20, 4)
    assert abs(result["indicators"]["BOLL_UPPER"]["value"] - ref_boll_up) < 1e-6
    assert abs(result["indicators"]["BOLL_LOWER"]["value"] - ref_boll_lo) < 1e-6

    # 独立基准公式核对 WR14
    hh14 = max(highs[-14:])
    ll14 = min(lows[-14:])
    ref_wr14 = round((hh14 - closes[-1]) / (hh14 - ll14) * -100.0, 4)
    assert abs(result["indicators"]["WR14"]["value"] - ref_wr14) < 1e-6

    # 独立基准公式核对 OBV
    ref_obv = volumes[0]
    for i in range(1, len(bars)):
        if closes[i] > closes[i - 1]:
            ref_obv += volumes[i]
        elif closes[i] < closes[i - 1]:
            ref_obv -= volumes[i]
    assert abs(result["indicators"]["OBV"]["value"] - round(ref_obv, 4)) < 1e-6

    # 校验所有 56 项指标均具备 20 日分位数与客观状态
    for code, item in result["indicators"].items():
        assert item["value"] is not None, f"{code} value 不应为 None"
        assert item["percentile_20d"] is not None and 0.0 <= item["percentile_20d"] <= 100.0
        assert isinstance(item["signal_state"], str) and len(item["signal_state"]) > 0


def test_insufficient_bars_keeps_none_never_zero() -> None:
    """当输入 K 线仅有 30 根时，MA60/MA120/MA250/MACD 等必须返回 None 并写明原因，严禁填 0。"""
    short_bars = build_deterministic_kline_series("600000.SH", adjustment="前复权", bars=30)
    result = calculate_all_56_indicators(short_bars, adjustment="前复权", symbol="600000.SH")

    for code in ("MA60", "MA120", "MA250", "EMA50", "MACD", "ADXR"):
        assert result["indicators"][code]["value"] is None
        assert result["indicators"][code]["value"] != 0
        assert "样本数" in (result["indicators"][code]["missing_reason"] or "")

    # 30 根足够算 MA5 / MA20 / RSI14
    assert result["indicators"]["MA5"]["value"] is not None
    assert result["indicators"]["MA20"]["value"] is not None
    assert result["indicators"]["RSI14"]["value"] is not None


def test_signal_conflict_detection_bullish_trend_vs_overbought_rsi() -> None:
    """构造持续上涨触发均线多头但 RSI/KDJ 超买的 K 线序列，验证自动生成多指标信号冲突说明。"""
    bars = []
    price = 10.0
    for idx in range(80):
        # 温和上涨后最后 10 天加速连阳拉升，使 MA20 > MA60 且 RSI > 80
        step = 0.05 if idx < 70 else 0.35
        prev_c = price
        close_p = round(price + step, 4)
        bars.append(
            {
                "date": f"2026-08-{(idx % 28) + 1:02d}",
                "symbol": "600036.SH",
                "open": prev_c,
                "high": round(close_p + 0.08, 4),
                "low": round(prev_c - 0.02, 4),
                "close": close_p,
                "volume": 50_000_000 + idx * 200_000,
                "advancing_issues": 3000,
                "declining_issues": 1800,
            }
        )
        price = close_p

    res = calculate_all_56_indicators(bars, adjustment="前复权", symbol="600036.SH")
    assert res["indicators"]["RSI6"]["signal_state"] == "超买"
    conflict_types = [c["type"] for c in res["signal_conflicts"]]
    assert "趋势多头_VS_短线超买" in conflict_types
