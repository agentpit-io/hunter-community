"""PriceAlertGraph — 价格异动多智能体分析引擎（主入口）

流程：
  Phase 1 (并行): MarketAnalyst + SentinelNewsAgent
    ↓
  条件路由: kill_condition?
    YES → ExitStrategyAdvisor → RiskJudge → FinalDecision
    NO  → BullResearcher ⇄ BearResearcher (2轮) → ComprehensiveJudge → RiskJudge → FinalDecision

纯 Python asyncio 实现，无需 LangGraph/LangChain 外部依赖。
"""
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from loguru import logger

# 将 hermes 根目录加入 sys.path，使 agents.* 可被外部模块导入
_HERMES_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERMES_ROOT not in sys.path:
    sys.path.insert(0, _HERMES_ROOT)

from agents.state import EnhancedAgentState, DebateState
from agents.market_analyst import run_market_analyst
from agents.sentinel_news_agent import run_sentinel_news_agent
from agents.bull_researcher import run_bull_researcher
from agents.bear_researcher import run_bear_researcher
from agents.comprehensive_judge import run_comprehensive_judge
from agents.exit_strategy_advisor import run_exit_strategy_advisor
from agents.risk_judge import run_risk_judge

_CST          = timezone(timedelta(hours=8))
_DEBATE_ROUNDS = 2    # 每方辩论轮数


async def run_price_alert_graph(
    ticker: str,
    stock_name: str,
    change_pct: float,
    trigger_desc: str,
    thesis_text: str = "",
    kill_conditions: list = None,
    current_price: float | None = None,
) -> dict:
    """
    主入口。价格预警触发时调用。

    Args:
        ticker:          股票代码（"600519.SH" 或 "600519"）
        stock_name:      股票名称
        change_pct:      当日涨跌幅（%）
        trigger_desc:    触发描述（如 "跌破 1650 止损线"）
        thesis_text:     用户持仓逻辑（可空）
        kill_conditions: [{text, type, source}, ...] 止损条件列表
        current_price:   TrueSource 当前价格兜底，akshare 失败时使用

    Returns:
        FinalDecisionResult dict
    """
    if kill_conditions is None:
        kill_conditions = []

    ticker     = _normalize_ticker(ticker)
    trade_date = datetime.now(_CST).strftime("%Y-%m-%d")

    state = EnhancedAgentState(
        ticker=ticker,
        stock_name=stock_name,
        trade_date=trade_date,
        change_pct=change_pct,
        trigger_desc=trigger_desc,
        thesis_text=thesis_text,
        kill_conditions=kill_conditions,
    )

    logger.info(
        "PriceAlertGraph 启动: {} ({}) {:+.2f}% | {}",
        stock_name, ticker, change_pct, trigger_desc,
    )

    # ── Phase 1: 并行分析 ──────────────────────────────────────────────
    market_task   = run_market_analyst(ticker, stock_name, change_pct, trade_date, current_price=current_price)
    sentinel_task = run_sentinel_news_agent(ticker, stock_name, change_pct, thesis_text, kill_conditions)

    market_report, sentinel_result = await asyncio.gather(
        market_task, sentinel_task, return_exceptions=True
    )

    if isinstance(market_report, Exception):
        logger.warning("market_analyst failed: {}", market_report)
        market_report = f"{stock_name} 技术数据获取失败"

    if isinstance(sentinel_result, Exception):
        logger.warning("sentinel_news_agent failed: {}", sentinel_result)
        sentinel_result = _empty_sentinel()

    state.market_report            = market_report
    state.sentinel_report          = sentinel_result["sentinel_report"]
    state.sentinel_opinion         = sentinel_result["sentinel_opinion"]
    state.sentinel_confidence      = sentinel_result["sentinel_confidence"]
    state.verified_facts           = sentinel_result["verified_facts"]
    state.filtered_facts           = sentinel_result["filtered_facts"]
    state.kill_condition_triggered = sentinel_result["kill_condition_triggered"]
    state.kill_condition_desc      = sentinel_result["kill_condition_desc"]
    state.debate_mode              = sentinel_result["debate_mode"]

    logger.info(
        "Phase 1 完成: Sentinel={} {:.0%} kill={}",
        state.sentinel_opinion, state.sentinel_confidence,
        state.kill_condition_triggered,
    )

    # ── Phase 2: 路由 ─────────────────────────────────────────────────
    if state.kill_condition_triggered:
        judgment = await asyncio.to_thread(run_exit_strategy_advisor, state)
        logger.info("ExitStrategyAdvisor 完成: option={}", judgment.get("recommended_option"))
    else:
        debate = DebateState()
        for round_n in range(_DEBATE_ROUNDS):
            debate = await asyncio.to_thread(run_bull_researcher, state, debate)
            debate = await asyncio.to_thread(run_bear_researcher, state, debate)
            logger.info("辩论第 {} 轮完成 (count={})", round_n + 1, debate.count)
        state.debate_state = debate

        judgment = await asyncio.to_thread(run_comprehensive_judge, state)
        logger.info("ComprehensiveJudge 完成: decision={}", judgment.get("decision"))

    # ── Phase 3: 风险裁判 ─────────────────────────────────────────────
    final = await asyncio.to_thread(run_risk_judge, state, judgment)

    logger.info(
        "PriceAlertGraph 完成: {} → {} {:.0%} | {}",
        ticker, final["decision"], final["confidence"], final["key_reason"][:60],
    )
    return final


def _normalize_ticker(ticker: str) -> str:
    ticker = ticker.strip()
    if "." in ticker:
        return ticker
    if ticker.startswith("6"):
        return f"{ticker}.SH"
    if ticker.startswith(("0", "3")):
        return f"{ticker}.SZ"
    if ticker.startswith(("4", "8", "9")):
        return f"{ticker}.BJ"
    return ticker


def _empty_sentinel() -> dict:
    return {
        "sentinel_report":          "新闻情报暂不可用",
        "sentinel_opinion":         "中性",
        "sentinel_confidence":      0.0,
        "verified_facts":           [],
        "filtered_facts":           [],
        "kill_condition_triggered": False,
        "kill_condition_desc":      "",
        "debate_mode":              "normal",
    }
