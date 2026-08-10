"""MarketAnalyst — 技术面分析节点

用 akshare 拉近 20 日日 K，LLM 生成技术分析报告。
"""
import asyncio
from loguru import logger

from agents.sentinel.llm_client import llm_json_call


async def run_market_analyst(
    ticker: str, stock_name: str, change_pct: float, trade_date: str,
    prefetched_kline: str | None = None,
    current_price: float | None = None,
) -> str:
    """返回市场技术分析报告字符串"""
    market_data_str = prefetched_kline or await _fetch_market_data(ticker, current_price=current_price, change_pct=change_pct)

    system = (
        "你是一位专业的 A 股技术分析师，使用简体中文输出所有内容。\n"
        "分析提供的近期日 K 数据，生成详细技术分析报告，重点关注：\n"
        "1. 价格趋势：近期上涨/下跌趋势、支撑位/阻力位\n"
        "2. 成交量：放量/缩量信号\n"
        "3. 动量：是否超买/超卖，趋势是否反转\n"
        "4. 今日异动含义：结合当日涨跌幅解读\n"
        "最终给出技术面简明结论（看多/中性/看空）和关键价位。"
    )
    user = (
        f"股票：{stock_name}（{ticker}）\n"
        f"分析日期：{trade_date}\n"
        f"当日涨跌幅：{change_pct:+.2f}%\n\n"
        f"近 20 日日 K 数据：\n{market_data_str}\n\n"
        "请回复 JSON 格式：\n"
        '{"report": "详细分析...", "opinion": "看多|中性|看空", "key_levels": "关键价位说明"}'
    )

    # llm_json_call 是同步阻塞调用，放入线程池避免阻塞 asyncio 事件循环
    # （事件循环被阻塞时 SSE 心跳无法发送，移动端会超时断线）
    parsed, _ = await asyncio.to_thread(
        llm_json_call, system, user, max_tokens=2000, temperature=0.3
    )
    if parsed:
        report   = parsed.get("report", "")
        opinion  = parsed.get("opinion", "中性")
        key_levels = parsed.get("key_levels", "")
        return f"{report}\n\n【技术面结论】{opinion} · {key_levels}"

    return f"{stock_name} 当日涨跌 {change_pct:+.2f}%（技术分析 AI 暂不可用）"


async def _fetch_market_data(
    ticker: str,
    current_price: float | None = None,
    change_pct: float | None = None,
) -> str:
    """用 akshare 拉近 20 日数据，失败时用 TrueSource 当前价格兜底"""
    code = ticker.split(".")[0]

    def _blocking():
        try:
            import akshare as ak
            df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
            if df is None or df.empty:
                return None
            df = df.tail(20).copy()
            df.columns = [str(c) for c in df.columns]
            lines = []
            for _, row in df.iterrows():
                d     = row.to_dict()
                date  = str(d.get("日期", d.get("date", "")))
                close = d.get("收盘", d.get("close", 0))
                pct   = d.get("涨跌幅", d.get("pct_chg", 0))
                vol   = d.get("成交量", d.get("volume", 0))
                amt   = d.get("成交额", d.get("amount", 0))
                lines.append(
                    f"{date}: 收盘={float(close):.2f} 涨跌={float(pct):+.2f}% "
                    f"成交量={int(float(vol))} 成交额={float(amt)/1e8:.2f}亿"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.warning("akshare stock_zh_a_hist({}) failed: {}", code, e)
            return None

    result = await asyncio.to_thread(_blocking)
    if result:
        return result

    # akshare 失败兜底：用 TrueSource 实时价格锚定价格区间，防止 LLM 幻觉
    if current_price:
        pct_str = f"{change_pct:+.2f}%" if change_pct is not None else "未知"
        return (
            f"（akshare 历史K线获取失败。当前实时数据：\n"
            f"当前价格：{current_price:.2f} 元\n"
            f"当日涨跌：{pct_str}\n"
            f"请基于此价格水平分析，止损/支撑/阻力位必须以 {current_price:.2f} 为基准合理计算。）"
        )
    return "（行情数据获取失败，请依据其他信息判断）"
