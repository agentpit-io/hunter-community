"""多智能体分析状态容器"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DebateState:
    history: str = ""
    bull_history: str = ""
    bear_history: str = ""
    current_response: str = ""
    count: int = 0


@dataclass
class EnhancedAgentState:
    # 基本信息
    ticker: str = ""           # e.g. "600519.SH"
    stock_name: str = ""
    trade_date: str = ""
    change_pct: float = 0.0
    trigger_desc: str = ""
    thesis_text: str = ""
    kill_conditions: list = field(default_factory=list)

    # 技术面
    market_report: str = ""

    # Sentinel 新闻情报
    sentinel_report: str = ""
    sentinel_opinion: str = "中性"
    sentinel_confidence: float = 0.5
    verified_facts: list = field(default_factory=list)
    filtered_facts: list = field(default_factory=list)
    kill_condition_triggered: bool = False
    kill_condition_desc: str = ""
    debate_mode: str = "normal"   # "normal" | "exit_strategy"

    # 辩论状态
    debate_state: DebateState = field(default_factory=DebateState)
    investment_plan: str = ""

    # kill 模式
    exit_strategy: str = ""

    # 最终输出
    final_decision: str = ""
    decision_label: str = "HOLD"    # BUY / SELL / HOLD
    confidence: float = 0.5
    stop_loss: Optional[float] = None
    key_reason: str = ""
    bull_summary: str = ""
    bear_summary: str = ""
