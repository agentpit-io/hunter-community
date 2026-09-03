"""美港股深度研究(gm端) —— GET /api/gm/research/{market}/{code}

聚合行情+技术面(规则模型)+近期新闻标题 → Gemini(OneAPI) 生成结构化AI结论卡:
{headline, confidence, supporting[], opposing[], risk_boundary, invalidation, key_numbers[]}
Redis缓存1小时(同标的重复查询不重复烧LLM)。
"""
import os
import json
import logging
from fastapi import APIRouter, HTTPException
from openai import OpenAI

from app.services.gm import findata_db, yahoo_hk, kpred_rules, news_src
from app.services.gm.yahoo_hk import _cache_get, _cache_set

log = logging.getLogger(__name__)
router = APIRouter()

_SCHEMA_HINT = """{
  "headline": "一句话结论(<=40字)",
  "confidence": 0到100整数,
  "supporting": ["支持证据1", "支持证据2", "支持证据3"],
  "opposing": ["反对/风险证据1", "反对/风险证据2"],
  "key_numbers": [{"label": "指标名", "value": "数值", "context": "参照说明"}],
  "risk_boundary": "风险边界描述(<=50字)",
  "invalidation": "什么变化会推翻该结论(<=50字)"
}"""


def _llm_json(prompt: str) -> dict | None:
    api_key = os.getenv("ONE_API_KEY", "")
    if not api_key:
        return None
    client = OpenAI(api_key=api_key,
                    base_url=os.getenv("ONE_API_BASE_URL", "http://104.197.139.51:3000/v1"),
                    timeout=60)
    model = os.getenv("ONE_API_MODEL", "gemini-3.8-flash")
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3, max_tokens=1200,
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        log.warning("gm research llm failed: %s", e)
        return None


@router.get("/research/{market}/{code}")
async def gm_research(market: str, code: str):
    market = market.upper()
    code_n = code.upper() if market == "US" else code.zfill(5)
    cache_key = f"gm:research:{market}:{code_n}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # 1) 聚合素材(全部真实数据)
    if market == "US":
        quote = findata_db.us_quote(code_n)
        bars = findata_db.us_kline(code_n, "1d", 250)
        news = news_src.us_news(code_n, 8)
    elif market == "HK":
        quote = yahoo_hk.hk_quote(code_n)
        m = findata_db.hk_master(code_n)
        if quote is not None and m:
            quote["name"] = m["name"] or quote["name"]
        bars = yahoo_hk.hk_kline(code_n, "1d", 250)
        news = news_src.hk_news(code_n, 8)
    else:
        raise HTTPException(400, "market 必须是 US / HK")
    if not quote or len(bars) < 65:
        return {"error": "insufficient_data", "market": market, "code": code_n}

    tech = kpred_rules.score([b["close"] for b in bars]) or {}
    news_lines = "\n".join(f"- {n['title']} ({n['source']})" for n in news) or "(近期无新闻)"

    # 2) 组prompt(只喂真实数据, 明确禁止编造)
    mk_note = ("美股无涨跌停制度, 注意极端波动; 财报窗口±48小时波动放大"
               if market == "US" else "港股: 注意每手股数与流动性; 免费行情延迟约15分钟")
    prompt = f"""你是专业的{'美股' if market == 'US' else '港股'}分析师。基于下面给出的真实数据对 {quote.get('name', code_n)}({code_n}) 做投前研究结论。

【行情】现价 {quote.get('price')} {quote.get('currency')}, 涨跌 {quote.get('change_pct')}%, 市场状态: {quote.get('market_state_label', '')}
【技术面·规则模型】评分{tech.get('score')}/100({tech.get('signal')}); 依据: {'; '.join(tech.get('reasons', []))}; 近20日涨跌 {tech.get('chg20d_pct')}%
【近期新闻标题】
{news_lines}
【市场制度提示】{mk_note}

要求:
1. 只基于上述给定数据推理, 数据里没有的数字严禁编造
2. 新闻仅有标题, 引用时说明"据新闻标题"
3. 结论克制, 不做买卖建议, 不给出具体买卖价格
4. 币种表述必须为{'美元USD/$' if market == 'US' else '港元HKD/HK$'}, 禁止使用人民币及"沪深/科创板/涨停/跌停"等A股术语
5. 用简体中文, 严格按此JSON结构输出:
{_SCHEMA_HINT}"""

    ai = _llm_json(prompt)
    if not ai or "headline" not in ai:
        return {"error": "llm_failed", "market": market, "code": code_n,
                "hint": "AI生成失败, 请稍后重试"}

    result = {
        "market": market, "code": code_n, "name": quote.get("name", ""),
        "quote": {"price": quote.get("price"), "change_pct": quote.get("change_pct"),
                  "currency": quote.get("currency")},
        "tech": {"score": tech.get("score"), "signal": tech.get("signal")},
        "ai": ai,
        "news_used": len(news),
        "sources": ["行情/K线: 自建数据管线", "技术面: 规则模型v1",
                    f"新闻: {'Alpaca/Benzinga' if market == 'US' else 'Yahoo'}({len(news)}条标题)"],
        "disclaimer": "AI生成 · 仅供研究 · 不构成投资建议",
    }
    _cache_set(cache_key, result, 3600)
    return result
