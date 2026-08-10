"""AI持仓管家(gm端) —— 多空辩论 + 综合裁判。GET /api/gm/guardian/{market}/{code}

三阶段(全部只喂真实数据, 严禁编造):
  1. 多头研究员: 找最强持有理由
  2. 空头研究员: 找最强撤出理由
  3. 综合裁判: 基于双方观点给出 继续持有/减仓观察/警惕风险 + 风控点
Redis缓存1小时。需登录(LLM成本控制)。
"""
import os
import json
import logging
from fastapi import APIRouter, HTTPException, Request
from openai import OpenAI

from app.services.gm import findata_db, yahoo_hk, kpred_rules, news_src
from app.services.gm.yahoo_hk import _cache_get, _cache_set

log = logging.getLogger(__name__)
router = APIRouter()


def _client():
    api_key = os.getenv("ONE_API_KEY", "")
    if not api_key:
        return None
    return OpenAI(api_key=api_key,
                  base_url=os.getenv("ONE_API_BASE_URL", "http://104.197.139.51:3000/v1"),
                  timeout=60)


def _llm(client, prompt: str, max_tokens: int = 1500) -> dict | None:
    content = ""
    try:
        resp = client.chat.completions.create(
            model=os.getenv("ONE_API_MODEL", "gemini-3-flash-preview"),
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.4, max_tokens=max_tokens)
        content = resp.choices[0].message.content or "{}"
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # 输出被截断/夹杂文字时, 提取首尾大括号之间再试
            s, e = content.find("{"), content.rfind("}")
            if s >= 0 and e > s:
                return json.loads(content[s:e + 1])
            raise
    except Exception as e:
        log.warning("guardian llm failed: %s | tail=%s", e, content[-120:])
        return None


@router.get("/guardian/{market}/{code}")
async def gm_guardian(market: str, code: str, request: Request):
    if not getattr(request.state, "user_id", None):
        raise HTTPException(401, "需要登录")
    market = market.upper()
    code_n = code.upper() if market == "US" else code.zfill(5)
    ck = f"gm:guardian:{market}:{code_n}"
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    # ── 素材聚合(真实数据) ──
    if market == "US":
        quote = findata_db.us_quote(code_n)
        bars = findata_db.us_kline(code_n, "1d", 250)
        news = findata_db.us_news_db(code_n, 6) or news_src.us_news(code_n, 6)
        fil = findata_db.us_filings_db(code_n, 3)
        rat = findata_db.analysts_by_symbol(code_n, 3)
    elif market == "HK":
        quote = yahoo_hk.hk_quote(code_n)
        m = findata_db.hk_master(code_n)
        if quote and m:
            quote["name"] = m["name"] or quote["name"]
        bars = yahoo_hk.hk_kline(code_n, "1d", 250)
        news = news_src.hk_news(code_n, 6)
        fil, rat = [], []
    else:
        raise HTTPException(400, "market 必须是 US / HK")
    if not quote or len(bars) < 65:
        return {"error": "insufficient_data"}
    tech = kpred_rules.score([b["close"] for b in bars]) or {}

    facts = f"""【标的】{quote.get('name', code_n)}({code_n}) 现价{quote.get('price')}{quote.get('currency')} 涨跌{quote.get('change_pct')}%
【技术面】规则评分{tech.get('score')}/100({tech.get('signal')}); {'; '.join(tech.get('reasons', []))}; 近20日{tech.get('chg20d_pct')}%
【近期新闻标题】{'; '.join(n['title'][:60] for n in news) or '无'}
【近期公告】{'; '.join(f"{f['date']} {f['title']}" for f in fil) or '无'}
【分析师动向】{'; '.join(f"{r['date']} {r['firm']} {r['action']}{r['to_grade']}" for r in rat) or '无'}
【财报】{'距下次财报' + str(quote.get('earnings_in_days')) + '天' if quote.get('earnings_in_days') is not None else '无临近财报信息'}"""

    client = _client()
    if client is None:
        return {"error": "llm_not_configured"}

    cur_note = "美元USD/$" if market == "US" else "港元HKD/HK$"
    rule = (f"只基于给定数据, 严禁编造数字; 新闻只有标题, 引用需注明; 中文输出, 严格按JSON结构。"
            f"币种表述必须为{cur_note}; 禁止使用人民币及'沪深/科创板/涨停/跌停'等A股术语; 不给出具体买卖价格。")
    bull = _llm(client, f"""你是多头研究员, 为"继续持有{code_n}"找出最有力的论据。
{facts}
{rule}
{{"points": ["论据1", "论据2", "论据3"], "strongest": "最强一条(<=40字)"}}""")
    bear = _llm(client, f"""你是空头研究员, 为"应该撤出{code_n}"找出最有力的风险论据。
{facts}
{rule}
{{"points": ["风险1", "风险2", "风险3"], "strongest": "最强一条(<=40字)"}}""")
    if not bull or not bear:
        return {"error": "llm_failed", "hint": "AI生成失败, 请稍后重试"}

    judge = _llm(client, f"""你是综合裁判。基于多空双方观点与事实, 对持有{code_n}给出裁决。
【事实】{facts}
【多头观点】{json.dumps(bull.get('points', []), ensure_ascii=False)}
【空头观点】{json.dumps(bear.get('points', []), ensure_ascii=False)}
{rule}裁决stance只能是: 继续持有/减仓观察/警惕风险 三选一。
{{"stance": "三选一", "confidence": 0到100整数, "one_line": "一句话裁决(<=40字)",
  "rationale": ["裁决理由1", "理由2"], "risk_control": "风控建议(<=50字)"}}""")
    if not judge or "stance" not in judge:
        return {"error": "llm_failed", "hint": "裁判生成失败, 请稍后重试"}

    result = {
        "market": market, "code": code_n, "name": quote.get("name", ""),
        "quote": {"price": quote.get("price"), "change_pct": quote.get("change_pct"),
                  "currency": quote.get("currency")},
        "bull": bull, "bear": bear, "judge": judge,
        "sources": ["行情/K线: 自建管线", "技术面: 规则模型",
                    f"新闻{len(news)}条 · 公告{len(fil)}条 · 评级{len(rat)}条"],
        "disclaimer": "AI多空辩论 · 仅供研究 · 不构成投资建议",
    }
    _cache_set(ck, result, 3600)
    return result
