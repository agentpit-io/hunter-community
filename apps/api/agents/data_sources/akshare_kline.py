"""akshare 日 K 多源兜底 · 腾讯 → 东财 · 一处封装 · agents/ + app/routers 共用

内部原来在 app/routers/internal_uzi._akshare_kline 里,只给 UZI 深度分析用。
market_analyst 也需要日 K,但只写了单路(东财 stock_zh_a_hist)· 东财一断线
技术面就整段空。这里提到共享层,两边都用。

未来加网易(163)/新浪(sina)/新版 xueqiu 兜底也放这里。
"""
from __future__ import annotations

from loguru import logger


def _ak_market_prefix(bare: str) -> str:
    """A 股代码 → 交易所前缀 · 给 akshare 腾讯通道用。"""
    if bare.startswith(("60", "68", "69")): return "sh"
    if bare.startswith(("00", "30", "20")): return "sz"
    if bare.startswith(("8", "43", "83", "87", "88")): return "bj"
    return "sh"


def fetch_kline(bare: str, days: int = 30) -> list[dict]:
    """返 list of {ts,open,high,low,close,volume} · 空 list = 全部通道失败。

    通道:
      1. akshare 腾讯 (stock_zh_a_hist_tx) · 响应快 · 少限流
      2. akshare 东财 (stock_zh_a_hist) · 数据更全 · 但连接常抖(RemoteDisconnected)
    未来可继续 append 网易/新浪等。
    """
    import akshare as ak
    from datetime import datetime, timedelta
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")

    # 通道 1 · 腾讯
    try:
        sym = f"{_ak_market_prefix(bare)}{bare}"
        df = ak.stock_zh_a_hist_tx(symbol=sym, start_date=start, end_date=end, adjust="qfq")
        if df is not None and not df.empty:
            out: list[dict] = []
            for r in df.tail(days).to_dict(orient="records"):
                try:
                    out.append({
                        "ts":     str(r.get("date") or "")[:10],
                        "open":   float(r.get("open") or 0),
                        "high":   float(r.get("high") or 0),
                        "low":    float(r.get("low") or 0),
                        "close":  float(r.get("close") or 0),
                        # 腾讯通道 amount 单位=元(不是"手"),这里不换算,只做数量级参考
                        "volume": int(float(r.get("amount") or 0)),
                    })
                except (TypeError, ValueError):
                    continue
            if out:
                return out
    except Exception as e:
        logger.warning("kline tx failed code={} err={}", bare, e)

    # 通道 2 · 东财
    try:
        df = ak.stock_zh_a_hist(symbol=bare, period="daily", start_date=start, end_date=end, adjust="qfq")
    except Exception as e:
        logger.warning("kline em failed code={} err={}", bare, e)
        return []
    if df is None or df.empty:
        return []
    out = []
    for r in df.tail(days).to_dict(orient="records"):
        try:
            out.append({
                "ts":     str(r.get("日期") or "")[:10],
                "open":   float(r.get("开盘") or 0),
                "high":   float(r.get("最高") or 0),
                "low":    float(r.get("最低") or 0),
                "close":  float(r.get("收盘") or 0),
                "volume": int(r.get("成交量") or 0),
            })
        except (TypeError, ValueError):
            continue
    return out


def format_kline_for_llm(rows: list[dict]) -> str:
    """把 fetch_kline 结果格式化成 market_analyst prompt 期望的多行文本。

    示例:
      2026-08-13: 收盘=18.52 涨跌=+1.20% 成交量=123456789 成交额=12.34亿
    涨跌幅由收盘价推导(前一日收盘为基线)· 无前值时留 0.00%。
    成交额 = volume(=元)/ 1e8 · 因为腾讯通道返的是元。东财通道 volume 是手,
    这里统一按元/亿换算 · 数量级参考即可,LLM 只用它判"放量/缩量"。
    """
    if not rows:
        return ""
    lines: list[str] = []
    prev_close: float | None = None
    for r in rows:
        close = float(r.get("close") or 0)
        pct = 0.0
        if prev_close and prev_close > 0:
            pct = (close - prev_close) / prev_close * 100
        vol = int(r.get("volume") or 0)
        lines.append(
            f"{r.get('ts', '')}: 收盘={close:.2f} 涨跌={pct:+.2f}% "
            f"成交量={vol} 成交额={vol / 1e8:.2f}亿"
        )
        prev_close = close
    return "\n".join(lines)
