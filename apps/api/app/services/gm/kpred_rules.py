"""美港股量化择时·规则模型(gm端)。

纯python计算(不依赖pandas): MA5/20/60 + MACD(12,26,9) + RSI(14)
输入日线收盘序列(时间升序) → 输出综合评分(0-100) + 信号 + 依据条目。

规则打分(与A股factor_engine思路一致但独立实现, 满分100):
  均线排列 30分: 多头排列(P>MA5>MA20>MA60)30 / 站上MA20 20 / 站上MA60 10 / 跌破全部 0
  MACD    35分: 金叉且柱扩大 35 / 金叉 25 / 柱收窄(背离迹象) 15 / 死叉 5
  RSI     35分: 40-60中性 20 / 60-70偏强 30 / <30超卖(反弹机会) 25 / >70超买 10 / 30-40偏弱 15
信号: >=70 偏多 / 45-69 中性 / <45 偏空
"""


def _ma(closes: list[float], n: int) -> float | None:
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def _ema_series(closes: list[float], n: int) -> list[float]:
    k = 2 / (n + 1)
    out = [closes[0]]
    for c in closes[1:]:
        out.append(c * k + out[-1] * (1 - k))
    return out


def _macd(closes: list[float]) -> tuple[float, float, float, float] | None:
    """返回 (dif, dea, hist, prev_hist)"""
    if len(closes) < 35:
        return None
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    dif = [a - b for a, b in zip(ema12, ema26)]
    dea = _ema_series(dif, 9)
    hist = [(a - b) * 2 for a, b in zip(dif, dea)]
    return dif[-1], dea[-1], hist[-1], hist[-2]


def _rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(-n, 0):
        d = closes[i] - closes[i - 1]
        if d > 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100 - 100 / (1 + rs)


def score(closes: list[float]) -> dict | None:
    """closes: 时间升序日线收盘价, 至少65根"""
    if len(closes) < 65:
        return None
    price = closes[-1]
    ma5, ma20, ma60 = _ma(closes, 5), _ma(closes, 20), _ma(closes, 60)
    macd = _macd(closes)
    rsi = _rsi(closes)
    if ma5 is None or ma20 is None or ma60 is None or macd is None or rsi is None:
        return None
    dif, dea, hist, prev_hist = macd

    pts = 0
    reasons: list[str] = []

    # 均线 30
    if price > ma5 > ma20 > ma60:
        pts += 30; reasons.append(f"多头排列: 价({price:.2f})>MA5>MA20>MA60")
    elif price > ma20:
        pts += 20; reasons.append(f"站上20日均线({ma20:.2f})")
    elif price > ma60:
        pts += 10; reasons.append(f"跌破MA20但守住60日均线({ma60:.2f})")
    else:
        reasons.append(f"跌破全部均线, MA60={ma60:.2f}")

    # MACD 35
    if dif > dea and hist > prev_hist:
        pts += 35; reasons.append("MACD金叉且红柱扩大, 动能增强")
    elif dif > dea:
        pts += 25; reasons.append("MACD金叉运行中")
    elif hist > prev_hist:
        pts += 15; reasons.append("MACD绿柱收窄, 空头动能减弱")
    else:
        pts += 5; reasons.append("MACD死叉, 空头动能未止")

    # RSI 35
    if 60 <= rsi < 70:
        pts += 30; reasons.append(f"RSI={rsi:.0f} 偏强区间")
    elif 40 <= rsi < 60:
        pts += 20; reasons.append(f"RSI={rsi:.0f} 中性区间")
    elif rsi < 30:
        pts += 25; reasons.append(f"RSI={rsi:.0f} 超卖, 存在技术性反弹条件")
    elif rsi >= 70:
        pts += 10; reasons.append(f"RSI={rsi:.0f} 超买, 追高风险大")
    else:
        pts += 15; reasons.append(f"RSI={rsi:.0f} 偏弱")

    signal = "偏多" if pts >= 70 else ("中性" if pts >= 45 else "偏空")
    # 近20日涨跌与波动
    chg20 = (price - closes[-21]) / closes[-21] * 100 if len(closes) >= 21 else None
    return {
        "score": pts, "signal": signal, "reasons": reasons,
        "price": round(price, 3),
        "ma5": round(ma5, 3), "ma20": round(ma20, 3), "ma60": round(ma60, 3),
        "macd_dif": round(dif, 4), "macd_dea": round(dea, 4), "macd_hist": round(hist, 4),
        "rsi14": round(rsi, 1),
        "chg20d_pct": round(chg20, 2) if chg20 is not None else None,
        "model": "规则模型 v1 (MA+MACD+RSI)",
        "disclaimer": "技术面规则信号, 仅供研究, 不构成投资建议",
    }
