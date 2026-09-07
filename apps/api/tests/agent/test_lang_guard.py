"""语言守卫 · 猎鹿人对用户可见文本里不许出现英文短语/句子/段落

2026-09-07 事故：AI 短评跑出
    "let's analyze the user's request and the provided stock data for
     Focus Media (分众传媒, 002027). **User Request:** * **Role:** ..."
旧守卫的判据是"整段是否含中文"，而这段夹着中文股票名，所以全程放行。
判据必须是"有没有英文散文"，这组用例就是钉死这条线的。
"""
import pytest

from app.services.lang_guard import (
    has_english_prose,
    sanitize_llm_text,
    sanitize_json_values,
)


# ── 必须判为英文散文 ──────────────────────────────────────────────
BAD = [
    "let's analyze the user's request and the provided stock data for Focus Media "
    "(分众传媒, 002027). **User Request:** * **Role:** Hunter-gatherer Short Review "
    "Assistant (猎鹿人短评助手). * **Input",
    "Okay, I will analyze the stock and provide a concise Chinese comment based on the data.",
    "Based on the provided data, the stock is trading near the bottom of its 52-week range.",
    "分众传媒当前偏弱。The stock is trading near the bottom of its 52-week range and "
    "shows no clear reversal signal yet.",
]

# ── 不许误伤：正常中文分析（含金融英文术语 / 代码 / 电报体英文标题）──
GOOD = [
    "分众传媒处于传媒板块，股价 4.88 元，位于 52 周区间 12% 分位，估值偏低但资金面偏弱。",
    "PE(TTM) 15.2x，ROE 18%，Free Cash Flow 同比转正，MACD 金叉。",
    "受益于 AI 与 5G 需求，Q3 EPS 超预期，NVDA 领涨 NASDAQ。",
    "触发底部反转，主力净流入 8500 万",
    "参考 Sharpe / IR / Alpha 指标，年化超额 3.2%。",
    "Kronos 模型给出 5 日预测 +1.8%，置信度 0.62，动量因子 IR 0.31。",
    "该股 A/H 溢价 42%，港股 H 股折价明显，可关注 AH 套利窗口。",
    "以上为通识分析，非投资建议，请以最新公告为准。",
]

# 线上实测的漏网形态（首版判据要 5 词 + 2 功能词，这些都溜过去了）
BAD_SHORT = [
    "Here is the output:",
    "Here is the analysis:" + chr(10) + "- Current Price: 7.97, Down 1.97%",
    "I will provide a concise and professional comment in Simplified Chinese.",
]


@pytest.mark.parametrize("text", BAD)
def test_english_prose_detected(text):
    assert has_english_prose(text) is True


@pytest.mark.parametrize("text", GOOD)
def test_chinese_analysis_not_flagged(text):
    assert has_english_prose(text) is False


@pytest.mark.parametrize("text", BAD)
def test_sanitize_drops_english(text):
    cleaned = sanitize_llm_text(text)
    assert not has_english_prose(cleaned)
    # 整段英文（含夹带的中文词）净化后要么空、要么只剩纯中文句子
    assert cleaned == "" or "the" not in cleaned.lower()


@pytest.mark.parametrize("text", BAD_SHORT)
def test_short_english_lines_detected(text):
    """短英文行也要抓 —— 它们同样是整段跑偏的开头。"""
    assert has_english_prose(text) is True
    assert sanitize_llm_text(text) == ""


def test_english_news_title_survives_via_key_whitelist():
    """纯英文新闻标题在正文语境下判为不合格是对的；
    它靠 title/url/source 的键白名单保护，不靠判据放行。"""
    title = "Focus Media reports strong Q2 results"
    assert has_english_prose(title) is True          # 作为"正文"不合格
    out = sanitize_json_values({"title": title, "ai_note": title})
    assert out["title"] == title                      # 外部原始数据原样保留
    assert out["ai_note"] == ""                       # 模型写的正文被净化


def test_sanitize_keeps_pure_chinese():
    text = "结论：短期观望。估值 PE(TTM) 15.2x 处于 12% 分位，等放量确认。"
    assert sanitize_llm_text(text) == text


def test_code_fence_is_not_touched():
    text = "示例如下：\n```sql\nSELECT * FROM t WHERE a IS NOT NULL AND b > 0;\n```\n以上为建表语句。"
    assert has_english_prose(text) is False
    assert sanitize_llm_text(text) == text


def test_json_guard_keeps_struct_and_external_fields():
    """结构键（type/code/impact）与外部原始数据（title/url/source）不许被净化。"""
    summary = {
        "type": "stock_quickview",
        "code": "002027",
        "name": "分众传媒",
        "ai_comment": "Based on the provided data, the stock is trading near the bottom "
                      "of its 52-week range.",
        "items": [{
            "title": "Focus Media to buy back shares worth 500 million",
            "source": "Reuters",
            "url": "https://example.com/a?b=the+and+of",
            "impact": "positive",
            "ai_note": "回购增强市场信心，短期股价有支撑。",
        }],
    }
    hits = []
    out = sanitize_json_values(summary, on_hit=lambda k, raw: hits.append(k))

    assert out["type"] == "stock_quickview"
    assert out["code"] == "002027"
    assert out["items"][0]["title"] == summary["items"][0]["title"]
    assert out["items"][0]["url"] == summary["items"][0]["url"]
    assert out["items"][0]["impact"] == "positive"
    assert out["items"][0]["ai_note"] == summary["items"][0]["ai_note"]
    # 英文短评被清空（前端对空值有 && 守卫，整块不渲染 —— 空的比英文的好）
    assert out["ai_comment"] == ""
    assert hits == ["ai_comment"]
