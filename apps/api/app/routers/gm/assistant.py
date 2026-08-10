"""gm投研助手(美港股对话) —— POST /api/gm/assistant/chat
无状态多轮: 前端携带最近历史; 可选股票上下文(自动注入行情/技术面/新闻真实数据)。"""
import os
import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from openai import OpenAI

from app.services.gm import findata_db, yahoo_hk, kpred_rules, news_src

log = logging.getLogger(__name__)
router = APIRouter()


class ChatIn(BaseModel):
    message: str
    market: str | None = None     # 选中的股票上下文(可空)
    code: str | None = None
    history: list[dict] = []      # [{role:'user'|'assistant', content:str}] 最近若干轮


_SYSTEM = """你是猎鹿人Hunter的美港股投研助手。
原则: 1)只基于用户提供与系统注入的真实数据回答, 没有的数字不编造, 可以说"暂无该数据"
2)不做买卖建议, 结论克制, 强调风险 3)中文回答, 简明分段, 适合手机阅读
4)涉及港股提示行情延迟15分钟; 美股提示无涨跌停制度
5)币种表述必须与市场一致(美股→美元/$, 港股→港元/HK$), 禁止使用人民币及"沪深/科创板/涨停/跌停"等A股术语, 不给出具体买卖价格"""


def _stock_context(market: str, code: str) -> str:
    try:
        if market == "US":
            q = findata_db.us_quote(code)
            bars = findata_db.us_kline(code, "1d", 250)
            news = findata_db.us_news_db(code, 5)
            rat = findata_db.analysts_by_symbol(code, 3)
        else:
            q = yahoo_hk.hk_quote(code)
            m = findata_db.hk_master(code)
            if q and m:
                q["name"] = m["name"] or q["name"]
            bars = yahoo_hk.hk_kline(code, "1d", 250)
            news = news_src.hk_news(code, 5)
            rat = []
        if not q:
            return ""
        tech = kpred_rules.score([b["close"] for b in bars]) if len(bars) >= 65 else None
        parts = [f"当前讨论标的: {q.get('name', code)}({code}) 现价{q.get('price')}{q.get('currency')} 涨跌{q.get('change_pct')}%"]
        if tech:
            parts.append(f"技术面: 评分{tech['score']}/100({tech['signal']}); {'; '.join(tech['reasons'])}")
        if q.get("earnings_in_days") is not None:
            parts.append(f"距下次财报{q['earnings_in_days']}天")
        if news:
            parts.append("近期新闻标题: " + "; ".join(n["title"][:50] for n in news))
        if rat:
            parts.append("分析师动向: " + "; ".join(f"{r['date']} {r['firm']} {r['action']}{r['to_grade']}" for r in rat))
        return "\n".join(parts)
    except Exception as e:
        log.warning("assistant ctx %s/%s failed: %s", market, code, e)
        return ""


@router.post("/assistant/chat")
async def gm_assistant_chat(body: ChatIn, request: Request):
    if not getattr(request.state, "user_id", None):
        raise HTTPException(401, "需要登录")
    api_key = os.getenv("ONE_API_KEY", "")
    if not api_key:
        raise HTTPException(500, "LLM未配置")
    msgs = [{"role": "system", "content": _SYSTEM}]
    if body.market and body.code:
        ctx = _stock_context(body.market.upper(),
                             body.code.upper() if body.market.upper() == "US" else body.code.zfill(5))
        if ctx:
            msgs.append({"role": "system", "content": "【系统注入的真实数据】\n" + ctx})
    for h in body.history[-8:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            msgs.append({"role": h["role"], "content": str(h["content"])[:1500]})
    msgs.append({"role": "user", "content": body.message[:1500]})
    # 模型偶尔用英文回答中文问题, 末尾追加强指令(最后指令权重最高)
    msgs.append({"role": "system", "content": "重要: 必须使用简体中文回答, 除股票代码/专有名词外不得输出英文段落。"})
    try:
        client = OpenAI(api_key=api_key,
                        base_url=os.getenv("ONE_API_BASE_URL", "http://104.197.139.51:3000/v1"),
                        timeout=60)
        resp = client.chat.completions.create(
            model=os.getenv("ONE_API_MODEL", "gemini-3-flash-preview"),
            messages=msgs, temperature=0.4, max_tokens=1200)
        reply = resp.choices[0].message.content or ""
    except Exception as e:
        log.warning("assistant llm failed: %s", e)
        raise HTTPException(502, "AI暂时不可用, 请稍后重试")
    return {"reply": reply, "disclaimer": "AI生成 · 仅供研究 · 不构成投资建议"}
