"""function_call 全链路失败时的降级：关键词路由 → 单一 sub-agent

对应 03 §2.3 场景 F、§4.5。Python 复刻 web/app/wx/home/intentRouter.ts 的规则。

新增（2026-08 · 持仓建议 S0）：
- stress    → portfolio_stress   (情景模拟 · 「如果 X 跌 Y%」)
- rebalance → portfolio_rebalance (组合建议 · 「怎么调仓」)
"""
from __future__ import annotations
from typing import Literal


TargetMode = Literal["research", "scout", "kpred", "hold", "event",
                     "stress", "rebalance"]

_INTENT_KEYWORDS: dict[TargetMode, tuple[str, ...]] = {
    # 更具体的组合级意图放前面
    "stress":   ("如果", "假设", "万一", "极端", "情景", "崩了", "崩盘", "跌 ",
                 "跌20", "跌30", "亏多少", "会亏", "跌20%", "跌30%"),
    "rebalance":("调仓", "加减仓", "怎么分", "该分多少", "配置比例", "仓位",
                 "组合建议", "再平衡", "怎么调", "该买多少", "该卖多少",
                 "目标权重", "重新平衡"),
    "hold":     ("还能拿", "该不该拿", "撤不撤", "止损", "要不要卖",
                 "继续拿", "拿不拿"),
    "event":    ("新闻", "事件", "公告", "利好", "利空", "影响", "最近发生",
                 "什么消息", "突发"),
    "kpred":    ("买点", "卖点", "时机", "什么时候", "预测", "走势", "入场",
                 "几号", "啥时候"),
    "scout":    ("机构", "调研", "北向", "研发", "一手", "资金流", "主力", "大单"),
    "research": ("怎么样", "值不值", "分析", "看看", "了解", "基本面", "业绩"),
}

# 优先级：更具体的意图先命中（组合级 > 单股 > 通用）
_PRIORITY: tuple[TargetMode, ...] = (
    "stress", "rebalance",
    "hold", "event", "kpred", "scout", "research",
)

# TargetMode → 对应 sub-agent tool name
_MODE_TO_TOOL: dict[TargetMode, str] = {
    "research":  "research",
    "scout":     "scout",
    "kpred":     "quant_predict",
    "hold":      "hold_judge",
    "event":     "event_interpret",
    "stress":    "portfolio_stress",
    "rebalance": "portfolio_rebalance",
}


def keyword_route(query: str) -> tuple[TargetMode, bool]:
    """返回 (target_mode, matched)。未命中则兜底 research。"""
    q = (query or "").strip().lower()
    if not q:
        return "research", False
    for mode in _PRIORITY:
        for kw in _INTENT_KEYWORDS[mode]:
            if kw.lower() in q:
                return mode, True
    return "research", False


def keyword_route_to_tool(query: str) -> tuple[str, bool]:
    """便捷版本：直接返回 tool_name"""
    mode, matched = keyword_route(query)
    return _MODE_TO_TOOL[mode], matched
