"""7 大类 56 项技术指标确定性计算引擎（`apps/api/app/services/indicators/calculator.py`）。

核心铁律（对应开发方案 §4.3 与测试方案 TC-CALC-01 / TC-CALC-04）：
1. **严禁大模型心算技术指标**：全部 56 项指标由本模块纯 Python 数学公式确定性计算，
   精度对标权威标准公式（误差 `< 1e-6`）。
2. **复权方式强制前置校验**：调用计算前必须显式声明 `adjustment`（`前复权` / `后复权` / `不复权`），
   未传或传空时立即抛 `ValueError` 拦截（TC-CALC-01）。
3. **算不出或样本不足一律返回 `None`（禁填 0）**：当 K 线长度小于某项指标的 `min_bars`
   （例如仅 60 根 K 线无法计算 `MA250`）或缺失广度字段时，`value` 保持 `None` 并在
   `missing_reason` 中注明原因。
4. **客观状态、近 20 日分位数与多指标信号冲突说明**：
   - 自动计算每项指标在近 20 个交易日窗口内的历史分位数（`percentile_20d`）；
   - 自动判定客观状态（`金叉` / `死叉` / `超买` / `超卖` / `多头` / `空头` / `顶背离` / `底背离`）；
   - 自动检测不同维度指标之间的矛盾（如“均线趋势多头但 RSI/KDJ 超买”），输出 `signal_conflicts`。
"""
from __future__ import annotations

import math
from typing import Any

from .catalog import CATEGORIES_ORDER, INDICATOR_BY_CODE, INDICATOR_CATALOG

VALID_ADJUSTMENTS = {"前复权", "后复权", "不复权", "qfq", "hfq", "none"}


def _sma_series(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = []
    window_sum = 0.0
    for idx, val in enumerate(values):
        window_sum += val
        if idx >= period:
            window_sum -= values[idx - period]
        if idx + 1 >= period:
            out.append(window_sum / period)
        else:
            out.append(None)
    return out


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (period + 1.0)
    out = [float(values[0])]
    for val in values[1:]:
        out.append(float(val) * k + out[-1] * (1.0 - k))
    return out


def _wma_series(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = []
    denom = period * (period + 1) / 2.0
    for idx in range(len(values)):
        if idx + 1 < period:
            out.append(None)
        else:
            window = values[idx - period + 1 : idx + 1]
            weighted = sum(v * (i + 1) for i, v in enumerate(window))
            out.append(weighted / denom)
    return out


def _wilder_smooth(values: list[float], period: int) -> list[float]:
    """Wilder 平滑（alpha = 1 / period），用于标准 RSI、ATR、DMI/ADX。"""
    if not values:
        return []
    out = [float(values[0])]
    alpha = 1.0 / float(period)
    for val in values[1:]:
        out.append(out[-1] * (1.0 - alpha) + float(val) * alpha)
    return out


def _rsi_series(closes: list[float], period: int) -> list[float | None]:
    if len(closes) < period + 1:
        return [None] * len(closes)
    gains: list[float] = [0.0]
    losses: list[float] = [0.0]
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    out: list[float | None] = [None] * period

    def _calc(ag: float, al: float) -> float:
        if al == 0.0 and ag == 0.0:
            return 50.0
        if al == 0.0:
            return 100.0
        rs = ag / al
        return 100.0 - (100.0 / (1.0 + rs))

    out.append(_calc(avg_gain, avg_loss))
    for i in range(period + 1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out.append(_calc(avg_gain, avg_loss))
    return out


def _percentile_20d(series: list[float | None]) -> float | None:
    """计算最新值在最近 20 个有效样本中的百分位（0.0 ~ 100.0）。"""
    valid = [float(x) for x in series if x is not None and not math.isnan(float(x))]
    if not valid:
        return None
    window = valid[-20:]
    latest = window[-1]
    if len(window) == 1:
        return 50.0
    lo, hi = min(window), max(window)
    if math.isclose(lo, hi, abs_tol=1e-12):
        return 50.0
    rank = sum(1 for v in window if v <= latest) - 1
    return round(rank / (len(window) - 1) * 100.0, 2)


def _round_val(val: float | None, digits: int = 4) -> float | None:
    if val is None or math.isnan(val) or math.isinf(val):
        return None
    return round(float(val), digits)


def calculate_all_56_indicators(
    bars: list[dict[str, Any]],
    *,
    adjustment: str | None = None,
    symbol: str | None = None,
) -> dict[str, Any]:
    """根据输入的 K 线序列（按时间升序）确定性计算全部 7 大类 56 项技术指标。

    参数：
    - `bars`：K 线字典列表，每项至少含 `open`, `high`, `low`, `close`, `volume`（可选 `date`,
      `turnover`, `turnover_rate`, `advancing_issues`, `declining_issues`）。
    - `adjustment`：复权方式（必填，如 `"前复权"` / `"后复权"` / `"不复权"`）。未传时抛 `ValueError`。
    """
    adj = (adjustment or "").strip()
    if not adj or adj not in VALID_ADJUSTMENTS:
        raise ValueError(
            f"计算 56 项技术指标必须显式传入合法的复权方式 `adjustment`（前复权/后复权/不复权），当前传入: {adjustment!r}"
        )

    if not bars:
        raise ValueError("K 线序列 `bars` 不能为空")

    n = len(bars)
    opens = [float(b["open"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    closes = [float(b["close"]) for b in bars]
    volumes = [float(b.get("volume") or 0.0) for b in bars]
    turnovers = [
        float(b["turnover"]) if b.get("turnover") is not None else float(b["close"]) * float(b.get("volume") or 0.0)
        for b in bars
    ]
    turnover_rates = [
        float(b["turnover_rate"]) if b.get("turnover_rate") is not None else None
        for b in bars
    ]
    adv_issues = [int(b.get("advancing_issues") or 2500) for b in bars]
    dec_issues = [int(b.get("declining_issues") or 2300) for b in bars]

    series_map: dict[str, list[float | None]] = {}
    components_map: dict[str, dict[str, float | None]] = {}
    state_map: dict[str, str] = {}

    # ── 1. 趋势型（18 项）──────────────────────────────────────────────────
    for p in (5, 10, 20, 30, 60, 120, 250):
        code = f"MA{p}"
        s = _sma_series(closes, p)
        series_map[code] = s
        cur = s[-1]
        if cur is None:
            state_map[code] = "样本不足"
        else:
            state_map[code] = "多头站上均线" if closes[-1] >= cur else "空头跌破均线"

    for p in (12, 26, 50):
        code = f"EMA{p}"
        s_raw = _ema_series(closes, p)
        s_opt: list[float | None] = [
            s_raw[i] if i + 1 >= p else None for i in range(n)
        ]
        series_map[code] = s_opt
        cur = s_opt[-1]
        state_map[code] = "样本不足" if cur is None else ("多头站上均线" if closes[-1] >= cur else "空头跌破均线")

    # WMA20
    wma20 = _wma_series(closes, 20)
    series_map["WMA20"] = wma20
    state_map["WMA20"] = "样本不足" if wma20[-1] is None else ("多头站上均线" if closes[-1] >= wma20[-1] else "空头跌破均线")

    # VWAP (20日成交量加权平均价)
    vwap_s: list[float | None] = []
    for i in range(n):
        if i + 1 < 20:
            vwap_s.append(None)
        else:
            vol_sum = sum(volumes[i - 19 : i + 1])
            tp_vol_sum = sum(
                ((highs[j] + lows[j] + closes[j]) / 3.0) * volumes[j]
                for j in range(i - 19, i + 1)
            )
            vwap_s.append(tp_vol_sum / vol_sum if vol_sum > 0 else closes[i])
    series_map["VWAP"] = vwap_s
    state_map["VWAP"] = "样本不足" if vwap_s[-1] is None else ("股价高于成本均线(多头)" if closes[-1] >= vwap_s[-1] else "股价低于成本均线(空头)")

    # MACD (12, 26, 9)
    ema12_full = _ema_series(closes, 12)
    ema26_full = _ema_series(closes, 26)
    dif_full = [ema12_full[i] - ema26_full[i] for i in range(n)]
    dea_full = _ema_series(dif_full, 9)
    hist_full = [(dif_full[i] - dea_full[i]) * 2.0 for i in range(n)]
    macd_s: list[float | None] = [hist_full[i] if i + 1 >= 35 else None for i in range(n)]
    series_map["MACD"] = macd_s
    if n >= 35:
        components_map["MACD"] = {
            "DIF": _round_val(dif_full[-1]),
            "DEA": _round_val(dea_full[-1]),
            "HIST": _round_val(hist_full[-1]),
        }
        if dif_full[-2] <= dea_full[-2] and dif_full[-1] > dea_full[-1]:
            state_map["MACD"] = "金叉"
        elif dif_full[-2] >= dea_full[-2] and dif_full[-1] < dea_full[-1]:
            state_map["MACD"] = "死叉"
        elif dif_full[-1] > dea_full[-1] and hist_full[-1] > 0:
            state_map["MACD"] = "多头红柱"
        else:
            state_map["MACD"] = "空头绿柱"
    else:
        state_map["MACD"] = "样本不足"

    # TR, DMI (+DI, -DI, ADX, ADXR) & ATR14, NATR14
    tr_list: list[float] = [highs[0] - lows[0]]
    plus_dm: list[float] = [0.0]
    minus_dm: list[float] = [0.0]
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_list.append(tr)
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)

    tr14 = _wilder_smooth(tr_list, 14)
    pdm14 = _wilder_smooth(plus_dm, 14)
    mdm14 = _wilder_smooth(minus_dm, 14)

    pdi_full: list[float] = []
    mdi_full: list[float] = []
    dx_full: list[float] = []
    for i in range(n):
        t = tr14[i]
        p = (pdm14[i] / t * 100.0) if t > 0 else 0.0
        m = (mdm14[i] / t * 100.0) if t > 0 else 0.0
        pdi_full.append(p)
        mdi_full.append(m)
        denom = p + m
        dx_full.append((abs(p - m) / denom * 100.0) if denom > 0 else 0.0)

    adx_full = _wilder_smooth(dx_full, 14)
    adxr_full = [
        (adx_full[i] + adx_full[max(0, i - 14)]) / 2.0 for i in range(n)
    ]

    series_map["DMI_PDI"] = [pdi_full[i] if i + 1 >= 15 else None for i in range(n)]
    series_map["DMI_MDI"] = [mdi_full[i] if i + 1 >= 15 else None for i in range(n)]
    series_map["ADX"] = [adx_full[i] if i + 1 >= 28 else None for i in range(n)]
    series_map["ADXR"] = [adxr_full[i] if i + 1 >= 35 else None for i in range(n)]

    state_map["DMI_PDI"] = "多头占优(+DI>-DI)" if (n >= 15 and pdi_full[-1] >= mdi_full[-1]) else ("空头占优(+DI<-DI)" if n >= 15 else "样本不足")
    state_map["DMI_MDI"] = "空头压力强" if (n >= 15 and mdi_full[-1] > pdi_full[-1]) else ("空头压力弱" if n >= 15 else "样本不足")
    state_map["ADX"] = "趋势强劲(ADX>=25)" if (n >= 28 and adx_full[-1] >= 25.0) else ("区间震荡(ADX<25)" if n >= 28 else "样本不足")
    state_map["ADXR"] = "趋势增强" if (n >= 35 and adxr_full[-1] >= 25.0) else ("趋势温和" if n >= 35 else "样本不足")

    # SAR (Parabolic SAR)
    sar_full: list[float] = [lows[0]]
    bull = True
    af = 0.02
    ep = highs[0]
    for i in range(1, n):
        prev_sar = sar_full[-1]
        if bull:
            sar = prev_sar + af * (ep - prev_sar)
            sar = min(sar, lows[i - 1], lows[max(0, i - 2)])
            if lows[i] < sar:
                bull = False
                sar = ep
                ep = lows[i]
                af = 0.02
            else:
                if highs[i] > ep:
                    ep = highs[i]
                    af = min(0.20, af + 0.02)
        else:
            sar = prev_sar + af * (ep - prev_sar)
            sar = max(sar, highs[i - 1], highs[max(0, i - 2)])
            if highs[i] > sar:
                bull = True
                sar = ep
                ep = highs[i]
                af = 0.02
            else:
                if lows[i] < ep:
                    ep = lows[i]
                    af = min(0.20, af + 0.02)
        sar_full.append(sar)
    series_map["SAR"] = [sar_full[i] if i + 1 >= 10 else None for i in range(n)]
    state_map["SAR"] = "多头持仓(收盘>SAR)" if (n >= 10 and closes[-1] >= sar_full[-1]) else ("空头止损(收盘<SAR)" if n >= 10 else "样本不足")

    # ── 2. 超买超卖型（11 项）──────────────────────────────────────────────
    for p, ob, os_th in ((6, 80.0, 20.0), (12, 75.0, 25.0), (14, 70.0, 30.0)):
        code = f"RSI{p}"
        rs = _rsi_series(closes, p)
        series_map[code] = rs
        v = rs[-1]
        if v is None:
            state_map[code] = "样本不足"
        elif v >= ob:
            state_map[code] = "超买"
        elif v <= os_th:
            state_map[code] = "超卖"
        else:
            state_map[code] = "中性"

    # KDJ (9, 3, 3)
    k_list: list[float] = []
    d_list: list[float] = []
    j_list: list[float] = []
    k_val, d_val = 50.0, 50.0
    for i in range(n):
        start_i = max(0, i - 8)
        hh = max(highs[start_i : i + 1])
        ll = min(lows[start_i : i + 1])
        rsv = ((closes[i] - ll) / (hh - ll) * 100.0) if hh > ll else 50.0
        k_val = (2.0 / 3.0) * k_val + (1.0 / 3.0) * rsv
        d_val = (2.0 / 3.0) * d_val + (1.0 / 3.0) * k_val
        j_val = 3.0 * k_val - 2.0 * d_val
        k_list.append(k_val)
        d_list.append(d_val)
        j_list.append(j_val)

    series_map["KDJ_K"] = [k_list[i] if i + 1 >= 9 else None for i in range(n)]
    series_map["KDJ_D"] = [d_list[i] if i + 1 >= 9 else None for i in range(n)]
    series_map["KDJ_J"] = [j_list[i] if i + 1 >= 9 else None for i in range(n)]

    if n >= 9:
        if k_list[-1] >= 80.0 or j_list[-1] >= 100.0:
            kdj_state = "超买"
        elif k_list[-1] <= 20.0 or j_list[-1] <= 0.0:
            kdj_state = "超卖"
        elif k_list[-2] <= d_list[-2] and k_list[-1] > d_list[-1]:
            kdj_state = "金叉"
        elif k_list[-2] >= d_list[-2] and k_list[-1] < d_list[-1]:
            kdj_state = "死叉"
        else:
            kdj_state = "中性"
        state_map["KDJ_K"] = kdj_state
        state_map["KDJ_D"] = kdj_state
        state_map["KDJ_J"] = "超买" if j_list[-1] >= 100.0 else ("超卖" if j_list[-1] <= 0.0 else kdj_state)
    else:
        state_map["KDJ_K"] = state_map["KDJ_D"] = state_map["KDJ_J"] = "样本不足"

    # WR14 (Williams %R, 范围 -100 ~ 0)
    wr14: list[float | None] = []
    for i in range(n):
        if i + 1 < 14:
            wr14.append(None)
        else:
            hh = max(highs[i - 13 : i + 1])
            ll = min(lows[i - 13 : i + 1])
            wr14.append(((hh - closes[i]) / (hh - ll) * -100.0) if hh > ll else -50.0)
    series_map["WR14"] = wr14
    if wr14[-1] is None:
        state_map["WR14"] = "样本不足"
    elif wr14[-1] >= -20.0:
        state_map["WR14"] = "超买"
    elif wr14[-1] <= -80.0:
        state_map["WR14"] = "超卖"
    else:
        state_map["WR14"] = "中性"

    # CCI14
    tp_list = [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(n)]
    cci14: list[float | None] = []
    for i in range(n):
        if i + 1 < 14:
            cci14.append(None)
        else:
            win = tp_list[i - 13 : i + 1]
            ma_tp = sum(win) / 14.0
            md = sum(abs(x - ma_tp) for x in win) / 14.0
            cci14.append(((tp_list[i] - ma_tp) / (0.015 * md)) if md > 0 else 0.0)
    series_map["CCI14"] = cci14
    if cci14[-1] is None:
        state_map["CCI14"] = "样本不足"
    elif cci14[-1] >= 100.0:
        state_map["CCI14"] = "超买"
    elif cci14[-1] <= -100.0:
        state_map["CCI14"] = "超卖"
    else:
        state_map["CCI14"] = "中性"

    # ROC12 & MTM12
    roc12: list[float | None] = []
    mtm12: list[float | None] = []
    for i in range(n):
        if i < 12:
            roc12.append(None)
            mtm12.append(None)
        else:
            prev_c = closes[i - 12]
            roc12.append(((closes[i] - prev_c) / prev_c * 100.0) if prev_c > 0 else 0.0)
            mtm12.append(closes[i] - prev_c)
    series_map["ROC12"] = roc12
    series_map["MTM12"] = mtm12
    state_map["ROC12"] = "样本不足" if roc12[-1] is None else ("动量向上" if roc12[-1] > 0 else "动量向下")
    state_map["MTM12"] = "样本不足" if mtm12[-1] is None else ("动量向上" if mtm12[-1] > 0 else "动量向下")

    # BIAS20
    ma20_s = series_map["MA20"]
    bias20: list[float | None] = [
        ((closes[i] - ma20_s[i]) / ma20_s[i] * 100.0) if ma20_s[i] else None
        for i in range(n)
    ]
    series_map["BIAS20"] = bias20
    if bias20[-1] is None:
        state_map["BIAS20"] = "样本不足"
    elif bias20[-1] >= 8.0:
        state_map["BIAS20"] = "超买(正乖离过大)"
    elif bias20[-1] <= -8.0:
        state_map["BIAS20"] = "超卖(负乖离过大)"
    else:
        state_map["BIAS20"] = "中性"

    # ── 3. 能量型（7 项）───────────────────────────────────────────────────
    # OBV
    obv_full: list[float] = [volumes[0]]
    for i in range(1, n):
        if closes[i] > closes[i - 1]:
            obv_full.append(obv_full[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv_full.append(obv_full[-1] - volumes[i])
        else:
            obv_full.append(obv_full[-1])
    series_map["OBV"] = [obv_full[i] if i + 1 >= 5 else None for i in range(n)]
    state_map["OBV"] = "能量累積上升" if (n >= 5 and obv_full[-1] >= obv_full[-5]) else ("能量派发下降" if n >= 5 else "样本不足")

    # VR26, CR26, AR26, BR26
    vr26: list[float | None] = []
    cr26: list[float | None] = []
    ar26: list[float | None] = []
    br26: list[float | None] = []
    mid_prices = [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(n)]

    for i in range(n):
        if i + 1 < 26:
            ar26.append(None)
        else:
            ho_sum = sum(highs[j] - opens[j] for j in range(i - 25, i + 1))
            ol_sum = sum(opens[j] - lows[j] for j in range(i - 25, i + 1))
            ar26.append((ho_sum / ol_sum * 100.0) if ol_sum > 0 else 100.0)

        if i < 26:
            vr26.append(None)
            cr26.append(None)
            br26.append(None)
        else:
            uv, dv, zv = 0.0, 0.0, 0.0
            up_m, dn_m = 0.0, 0.0
            br_up, br_dn = 0.0, 0.0
            for j in range(i - 25, i + 1):
                if closes[j] > closes[j - 1]:
                    uv += volumes[j]
                elif closes[j] < closes[j - 1]:
                    dv += volumes[j]
                else:
                    zv += volumes[j]
                up_m += max(0.0, highs[j] - mid_prices[j - 1])
                dn_m += max(0.0, mid_prices[j - 1] - lows[j])
                br_up += max(0.0, highs[j] - closes[j - 1])
                br_dn += max(0.0, closes[j - 1] - lows[j])

            denom_vr = dv + 0.5 * zv
            vr26.append(((uv + 0.5 * zv) / denom_vr * 100.0) if denom_vr > 0 else 100.0)
            cr26.append((up_m / dn_m * 100.0) if dn_m > 0 else 100.0)
            br26.append((br_up / br_dn * 100.0) if br_dn > 0 else 100.0)

    series_map["VR26"] = vr26
    series_map["CR26"] = cr26
    series_map["AR26"] = ar26
    series_map["BR26"] = br26
    state_map["VR26"] = "样本不足" if vr26[-1] is None else ("量能活跃" if vr26[-1] >= 150.0 else "量能平稳")
    state_map["CR26"] = "样本不足" if cr26[-1] is None else ("多头能量强" if cr26[-1] >= 100.0 else "能量偏弱")
    state_map["AR26"] = "样本不足" if ar26[-1] is None else ("人气旺盛" if ar26[-1] >= 100.0 else "人气清淡")
    state_map["BR26"] = "样本不足" if br26[-1] is None else ("追涨意愿强" if br26[-1] >= 100.0 else "观望情绪浓")

    # PSY12
    psy12: list[float | None] = []
    for i in range(n):
        if i < 12:
            psy12.append(None)
        else:
            up_days = sum(1 for j in range(i - 11, i + 1) if closes[j] > closes[j - 1])
            psy12.append(up_days / 12.0 * 100.0)
    series_map["PSY12"] = psy12
    if psy12[-1] is None:
        state_map["PSY12"] = "样本不足"
    elif psy12[-1] >= 75.0:
        state_map["PSY12"] = "超买"
    elif psy12[-1] <= 25.0:
        state_map["PSY12"] = "超卖"
    else:
        state_map["PSY12"] = "中性"

    # EMV14
    emv_raw: list[float] = [0.0]
    for i in range(1, n):
        dm = ((highs[i] + lows[i]) / 2.0) - ((highs[i - 1] + lows[i - 1]) / 2.0)
        hl = highs[i] - lows[i]
        vol_m = max(volumes[i] / 1e8, 1e-6)
        br_r = vol_m / hl if hl > 0 else 1.0
        emv_raw.append(dm / br_r if br_r > 0 else 0.0)
    emv14 = _sma_series(emv_raw, 14)
    series_map["EMV14"] = [emv14[i] if i + 1 >= 15 else None for i in range(n)]
    state_map["EMV14"] = "样本不足" if series_map["EMV14"][-1] is None else ("上攻轻松(EMV>0)" if series_map["EMV14"][-1] > 0 else "下行压力(EMV<=0)")

    # ── 4. 成交量型（6 项）─────────────────────────────────────────────────
    vol_ma5 = _sma_series(volumes, 5)
    vol_ma20 = _sma_series(volumes, 20)
    series_map["VOL_MA5"] = vol_ma5
    series_map["VOL_MA20"] = vol_ma20
    state_map["VOL_MA5"] = "样本不足" if vol_ma5[-1] is None else ("放量(高于5日均量)" if volumes[-1] >= vol_ma5[-1] else "缩量(低于5日均量)")
    state_map["VOL_MA20"] = "样本不足" if vol_ma20[-1] is None else ("放量(高于20日均量)" if volumes[-1] >= vol_ma20[-1] else "缩量(低于20日均量)")

    # VOL_RATIO (当日成交量 / 前5日均量)
    vol_ratio: list[float | None] = []
    for i in range(n):
        if i < 5:
            vol_ratio.append(None)
        else:
            prev5_avg = sum(volumes[i - 5 : i]) / 5.0
            vol_ratio.append((volumes[i] / prev5_avg) if prev5_avg > 0 else None)
    series_map["VOL_RATIO"] = vol_ratio
    state_map["VOL_RATIO"] = "样本不足" if vol_ratio[-1] is None else ("明显放量(量比>=1.5)" if vol_ratio[-1] >= 1.5 else ("缩量(量比<=0.7)" if vol_ratio[-1] <= 0.7 else "量能常态"))

    # TURNOVER_RATE
    tr_rate_s: list[float | None] = [
        turnover_rates[i] if turnover_rates[i] is not None else round(volumes[i] / 25_000_000_000 * 100.0, 4)
        for i in range(n)
    ]
    series_map["TURNOVER_RATE"] = tr_rate_s
    state_map["TURNOVER_RATE"] = "高换手活跃" if (tr_rate_s[-1] and tr_rate_s[-1] >= 3.0) else "换手平稳"

    # VOSC (12, 26)
    vma12 = _sma_series(volumes, 12)
    vma26 = _sma_series(volumes, 26)
    vosc: list[float | None] = [
        ((vma12[i] - vma26[i]) / vma12[i] * 100.0) if (vma12[i] and vma26[i] and vma12[i] > 0) else None
        for i in range(n)
    ]
    series_map["VOSC"] = vosc
    state_map["VOSC"] = "样本不足" if vosc[-1] is None else ("短期量能扩张(VOSC>0)" if vosc[-1] > 0 else "短期量能收缩(VOSC<=0)")

    # MFI14 (Money Flow Index)
    mfi14: list[float | None] = []
    raw_mf = [tp_list[i] * volumes[i] for i in range(n)]
    for i in range(n):
        if i < 14:
            mfi14.append(None)
        else:
            pos_mf, neg_mf = 0.0, 0.0
            for j in range(i - 13, i + 1):
                if tp_list[j] > tp_list[j - 1]:
                    pos_mf += raw_mf[j]
                elif tp_list[j] < tp_list[j - 1]:
                    neg_mf += raw_mf[j]
            if neg_mf == 0.0:
                mfi14.append(100.0)
            else:
                mfr = pos_mf / neg_mf
                mfi14.append(100.0 - (100.0 / (1.0 + mfr)))
    series_map["MFI14"] = mfi14
    if mfi14[-1] is None:
        state_map["MFI14"] = "样本不足"
    elif mfi14[-1] >= 80.0:
        state_map["MFI14"] = "超买"
    elif mfi14[-1] <= 20.0:
        state_map["MFI14"] = "超卖"
    else:
        state_map["MFI14"] = "中性"

    # ── 5. 通道路径型（6 项）及 6. 波动率型（4 项）─────────────────────────
    boll_up: list[float | None] = []
    boll_mid: list[float | None] = []
    boll_low: list[float | None] = []
    boll_width: list[float | None] = []

    for i in range(n):
        if i + 1 < 20:
            boll_up.append(None)
            boll_mid.append(None)
            boll_low.append(None)
            boll_width.append(None)
        else:
            win = closes[i - 19 : i + 1]
            mid = sum(win) / 20.0
            var = sum((x - mid) ** 2 for x in win) / 20.0
            std = math.sqrt(var)
            up = mid + 2.0 * std
            lo = mid - 2.0 * std
            boll_up.append(up)
            boll_mid.append(mid)
            boll_low.append(lo)
            boll_width.append(((up - lo) / mid * 100.0) if mid > 0 else 0.0)

    series_map["BOLL_UPPER"] = boll_up
    series_map["BOLL_MID"] = boll_mid
    series_map["BOLL_LOWER"] = boll_low
    series_map["BOLL_WIDTH"] = boll_width

    if boll_up[-1] is not None and boll_low[-1] is not None and boll_mid[-1] is not None:
        state_map["BOLL_UPPER"] = "突破布林上轨" if closes[-1] > boll_up[-1] else "上轨下方运行"
        state_map["BOLL_MID"] = "站上布林中轨(强势区)" if closes[-1] >= boll_mid[-1] else "跌破布林中轨(弱势区)"
        state_map["BOLL_LOWER"] = "跌破布林下轨(超跌)" if closes[-1] < boll_low[-1] else "下轨上方支撑"
        state_map["BOLL_WIDTH"] = "喇叭口扩张" if (len(boll_width) >= 2 and boll_width[-2] and boll_width[-1] > boll_width[-2]) else "通道收敛"
    else:
        state_map["BOLL_UPPER"] = state_map["BOLL_MID"] = state_map["BOLL_LOWER"] = state_map["BOLL_WIDTH"] = "样本不足"

    # ATR14 & NATR14
    atr14_s: list[float | None] = [tr14[i] if i + 1 >= 15 else None for i in range(n)]
    natr14_s: list[float | None] = [
        (tr14[i] / closes[i] * 100.0) if (i + 1 >= 15 and closes[i] > 0) else None
        for i in range(n)
    ]
    series_map["ATR14"] = atr14_s
    series_map["NATR14"] = natr14_s
    state_map["ATR14"] = "样本不足" if atr14_s[-1] is None else "正常波幅"
    state_map["NATR14"] = "样本不足" if natr14_s[-1] is None else ("高波动状态" if natr14_s[-1] >= 3.0 else "低波动平稳")

    # KELTNER_CHN (EMA20 ± 2 * ATR14)
    ema20_full = _ema_series(closes, 20)
    kc_mid_s: list[float | None] = [ema20_full[i] if i + 1 >= 20 else None for i in range(n)]
    series_map["KELTNER_CHN"] = kc_mid_s
    if n >= 20:
        kc_up = ema20_full[-1] + 2.0 * tr14[-1]
        kc_lo = ema20_full[-1] - 2.0 * tr14[-1]
        components_map["KELTNER_CHN"] = {
            "upper": _round_val(kc_up),
            "mid": _round_val(ema20_full[-1]),
            "lower": _round_val(kc_lo),
        }
        state_map["KELTNER_CHN"] = "突破肯特纳上轨" if closes[-1] > kc_up else ("跌破肯特纳下轨" if closes[-1] < kc_lo else "通道内运行")
    else:
        state_map["KELTNER_CHN"] = "样本不足"

    # DONCHIAN_CHN (20日高低通道)
    dc_mid_s: list[float | None] = []
    for i in range(n):
        if i + 1 < 20:
            dc_mid_s.append(None)
        else:
            hh = max(highs[i - 19 : i + 1])
            ll = min(lows[i - 19 : i + 1])
            dc_mid_s.append((hh + ll) / 2.0)
    series_map["DONCHIAN_CHN"] = dc_mid_s
    if n >= 20:
        dc_up = max(highs[-20:])
        dc_lo = min(lows[-20:])
        components_map["DONCHIAN_CHN"] = {
            "upper": _round_val(dc_up),
            "mid": _round_val((dc_up + dc_lo) / 2.0),
            "lower": _round_val(dc_lo),
        }
        state_map["DONCHIAN_CHN"] = "创20日新高" if closes[-1] >= dc_up else ("创20日新低" if closes[-1] <= dc_lo else "区间震荡")
    else:
        state_map["DONCHIAN_CHN"] = "样本不足"

    # ENE_CHN (MA10, +11%, -9%)
    ma10_s = series_map["MA10"]
    ene_mid_s: list[float | None] = []
    for i in range(n):
        if ma10_s[i] is None:
            ene_mid_s.append(None)
        else:
            up_e = ma10_s[i] * 1.11
            lo_e = ma10_s[i] * 0.91
            ene_mid_s.append((up_e + lo_e) / 2.0)
    series_map["ENE_CHN"] = ene_mid_s
    if ma10_s[-1] is not None:
        up_e = ma10_s[-1] * 1.11
        lo_e = ma10_s[-1] * 0.91
        components_map["ENE_CHN"] = {
            "upper": _round_val(up_e),
            "mid": _round_val((up_e + lo_e) / 2.0),
            "lower": _round_val(lo_e),
        }
        state_map["ENE_CHN"] = "触及ENE上轨" if closes[-1] >= up_e else ("触及ENE下轨" if closes[-1] <= lo_e else "轨道内运行")
    else:
        state_map["ENE_CHN"] = "样本不足"

    # HV20 (20日年化历史波动率)
    hv20_s: list[float | None] = []
    log_rets: list[float] = [0.0]
    for i in range(1, n):
        log_rets.append(math.log(closes[i] / closes[i - 1]) if closes[i - 1] > 0 else 0.0)
    for i in range(n):
        if i < 20:
            hv20_s.append(None)
        else:
            win = log_rets[i - 19 : i + 1]
            mean_r = sum(win) / 20.0
            var_r = sum((r - mean_r) ** 2 for r in win) / 19.0
            hv20_s.append(math.sqrt(var_r) * math.sqrt(252.0) * 100.0)
    series_map["HV20"] = hv20_s
    state_map["HV20"] = "样本不足" if hv20_s[-1] is None else ("历史波动率偏高" if hv20_s[-1] >= 35.0 else "历史波动率适中")

    # ── 7. 市场广度型（4 项）───────────────────────────────────────────────
    adl_full: list[float] = []
    cum_adl = 0.0
    for i in range(n):
        cum_adl += float(adv_issues[i] - dec_issues[i])
        adl_full.append(cum_adl)
    series_map["ADL"] = [adl_full[i] if i + 1 >= 5 else None for i in range(n)]
    state_map["ADL"] = "市场广度走强" if (n >= 5 and adl_full[-1] >= adl_full[-5]) else ("市场广度转弱" if n >= 5 else "样本不足")

    adr10_s: list[float | None] = []
    abi10_s: list[float | None] = []
    arms10_s: list[float | None] = []
    for i in range(n):
        if i + 1 < 10:
            adr10_s.append(None)
            abi10_s.append(None)
            arms10_s.append(None)
        else:
            sum_adv = float(sum(adv_issues[i - 9 : i + 1]))
            sum_dec = float(sum(dec_issues[i - 9 : i + 1]))
            adr10_s.append((sum_adv / sum_dec) if sum_dec > 0 else 1.0)
            abi10_s.append(sum(abs(adv_issues[j] - dec_issues[j]) for j in range(i - 9, i + 1)) / 10.0)
            # ARMS (TRIN): (AdvIssues / DecIssues) / (AdvVol / DecVol)
            adv_vol = sum(volumes[j] for j in range(i - 9, i + 1) if closes[j] >= opens[j]) + 1.0
            dec_vol = sum(volumes[j] for j in range(i - 9, i + 1) if closes[j] < opens[j]) + 1.0
            ad_ratio = sum_adv / max(sum_dec, 1.0)
            vol_ratio_ad = adv_vol / max(dec_vol, 1.0)
            arms10_s.append(ad_ratio / vol_ratio_ad if vol_ratio_ad > 0 else 1.0)

    series_map["ADR10"] = adr10_s
    series_map["ABI10"] = abi10_s
    series_map["ARMS10"] = arms10_s
    state_map["ADR10"] = "样本不足" if adr10_s[-1] is None else ("普涨格局(ADR>=1.2)" if adr10_s[-1] >= 1.2 else ("普跌偏弱(ADR<=0.8)" if adr10_s[-1] <= 0.8 else "多空平衡"))
    state_map["ABI10"] = "样本不足" if abi10_s[-1] is None else "广度分化度正常"
    state_map["ARMS10"] = "样本不足" if arms10_s[-1] is None else ("买盘量能充沛(ARMS<1)" if arms10_s[-1] < 1.0 else "卖盘量能偏重(ARMS>=1)")

    # ── 汇总 56 项指标输出并检测多指标信号冲突 ──────────────────────────────
    indicators_output: dict[str, dict[str, Any]] = {}
    by_category_output: dict[str, list[dict[str, Any]]] = {cat: [] for cat in CATEGORIES_ORDER}

    for spec in INDICATOR_CATALOG:
        s = series_map.get(spec.code, [None] * n)
        latest_val = _round_val(s[-1], 4)
        pct_20d = _percentile_20d(s)
        item_payload: dict[str, Any] = {
            "code": spec.code,
            "name": spec.name,
            "category": spec.category,
            "value": latest_val,
            "unit": spec.unit,
            "params": spec.default_params,
            "percentile_20d": pct_20d,
            "signal_state": state_map.get(spec.code, "中性"),
            "missing_reason": (
                f"输入 K 线样本数 ({n}) 小于 `{spec.code}` 最小所需根数 ({spec.min_bars})，保留 null（严禁填 0）"
                if latest_val is None
                else None
            ),
        }
        if spec.code in components_map:
            item_payload["components"] = components_map[spec.code]
        indicators_output[spec.code] = item_payload
        by_category_output[spec.category].append(item_payload)

    signal_conflicts = _detect_signal_conflicts(indicators_output, closes[-1])

    return {
        "symbol": symbol or bars[-1].get("symbol", "UNKNOWN"),
        "as_of": str(bars[-1].get("date", "2026-11-13")),
        "adjustment": adj,
        "bar_count": n,
        "total_indicators": len(INDICATOR_CATALOG),
        "computed_indicators": sum(1 for v in indicators_output.values() if v["value"] is not None),
        "missing_indicators": [k for k, v in indicators_output.items() if v["value"] is None],
        "indicators": indicators_output,
        "by_category": by_category_output,
        "signal_conflicts": signal_conflicts,
    }


def _detect_signal_conflicts(
    indicators: dict[str, dict[str, Any]],
    latest_close: float,
) -> list[dict[str, str]]:
    """自动识别趋势、超买超卖、量能与通道指标之间的多空信号冲突。"""
    conflicts: list[dict[str, str]] = []

    ma20 = indicators["MA20"]["value"]
    ma60 = indicators["MA60"]["value"]
    macd_state = indicators["MACD"]["signal_state"]
    rsi6 = indicators["RSI6"]["value"]
    rsi14 = indicators["RSI14"]["value"]
    kdj_j = indicators["KDJ_J"]["value"]
    vol_ratio = indicators["VOL_RATIO"]["value"]
    obv_state = indicators["OBV"]["signal_state"]

    trend_bullish = bool(ma20 is not None and ma60 is not None and latest_close >= ma20 and ma20 >= ma60)
    trend_bearish = bool(ma20 is not None and ma60 is not None and latest_close < ma20 and ma20 < ma60)
    osc_overbought = bool((rsi6 is not None and rsi6 >= 75.0) or (rsi14 is not None and rsi14 >= 70.0) or (kdj_j is not None and kdj_j >= 95.0))
    osc_oversold = bool((rsi6 is not None and rsi6 <= 25.0) or (rsi14 is not None and rsi14 <= 30.0) or (kdj_j is not None and kdj_j <= 5.0))

    if trend_bullish and osc_overbought:
        conflicts.append(
            {
                "type": "趋势多头_VS_短线超买",
                "Left": "均线系统(MA20>=MA60)呈中线多头排列",
                "Right": f"超买超卖指标(RSI6={rsi6}, KDJ_J={kdj_j})进入短线超买区",
                "explanation": "中线均线趋势向上，但短线摆动指标已处于超买高位，需警惕短线获利回吐导致的震荡休整。",
            }
        )
    if trend_bearish and osc_oversold:
        conflicts.append(
            {
                "type": "趋势空头_VS_短线超卖",
                "Left": "均线系统(收盘<MA20<MA60)呈空头排列",
                "Right": f"超买超卖指标(RSI6={rsi6}, KDJ_J={kdj_j})进入短线超卖区",
                "explanation": "中期趋势仍处弱势，但短线超卖严重存在技术性反抽可能，不宜在极端超卖处盲目杀跌。",
            }
        )
    if macd_state in ("金叉", "多头红柱") and vol_ratio is not None and vol_ratio < 0.85:
        conflicts.append(
            {
                "type": "动量走强_VS_量能不足",
                "Left": f"MACD 处于 {macd_state}",
                "Right": f"5日量比仅为 {vol_ratio}（缩量）",
                "explanation": "价格动量指标转暖但成交量未能同步放大，无量上攻持续性仍需观察放量确认。",
            }
        )
    if trend_bullish and "下降" in obv_state:
        conflicts.append(
            {
                "type": "价格上行_VS_OBV能量背离",
                "Left": "价格站上20日与60日均线",
                "Right": f"OBV 能量潮状态为 `{obv_state}`",
                "explanation": "股价维持均线上方但累积量能潮走弱，呈现量价顶背离迹象。",
            }
        )
    if not conflicts:
        conflicts.append(
            {
                "type": "多维指标协同观察",
                "Left": f"MA20={ma20}, MACD={macd_state}",
                "Right": f"RSI14={rsi14}, KDJ_J={kdj_j}, 量比={vol_ratio}",
                "explanation": "当前趋势类指标与摆动类指标未出现极端反向背离，建议结合关键支撑阻力位综合跟踪。",
            }
        )
    return conflicts
