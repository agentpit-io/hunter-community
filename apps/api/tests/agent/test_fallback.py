"""fallback 关键词路由测试"""
from app.services.agent.fallback import keyword_route, keyword_route_to_tool


def test_hold_intent():
    mode, matched = keyword_route("茅台还能拿吗")
    assert mode == "hold" and matched


def test_kpred_intent():
    mode, matched = keyword_route("什么时候买入好")
    assert mode == "kpred" and matched


def test_scout_intent():
    mode, matched = keyword_route("最近北向净流入多少")
    assert mode == "scout" and matched


def test_event_intent():
    mode, matched = keyword_route("最近有什么新闻影响")
    assert mode == "event" and matched


def test_research_intent_default():
    mode, matched = keyword_route("宁德时代怎么样")
    assert mode == "research" and matched


def test_no_match_falls_back_to_research():
    mode, matched = keyword_route("我想吃火锅")
    assert mode == "research" and not matched


def test_priority_hold_beats_research():
    # "还能拿" 应该先命中 hold，而不是被 "怎么样" 兜到 research
    mode, matched = keyword_route("怎么样，茅台还能拿吗")
    assert mode == "hold"


def test_route_to_tool_mapping():
    tool, matched = keyword_route_to_tool("茅台还能拿吗")
    assert tool == "hold_judge" and matched
    tool2, _ = keyword_route_to_tool("宁德什么时候买")
    assert tool2 == "quant_predict"
