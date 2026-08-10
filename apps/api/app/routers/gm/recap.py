"""隔夜复盘(gm端) —— GET /api/gm/recap
聚合用户美股自选隔夜真实表现+临近财报 → Gemini中文复盘。按用户+日期缓存12h。"""
import os
import json
import logging
from datetime import date
from fastapi import APIRouter, HTTPException, Request
from openai import OpenAI

from app.services.database import get_conn
from app.services.gm import findata_db, earnings_cal, yahoo_hk
from app.services.gm.yahoo_hk import _cache_get, _cache_set

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/recap")
async def gm_recap(request: Request):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "需要登录")
    return build_recap(user_id)


def build_recap(user_id: str) -> dict:
    """生成(或取当日缓存)该用户的隔夜复盘。HTTP端点与每日08:00定时推送共用。"""
    ck = f"gm:recap:{user_id}:{date.today().isoformat()}"
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT code, name, market FROM stocks WHERE enabled = TRUE AND user_id = %s AND market IN ('US','HK')",
                (user_id,))
    watch = [{"code": r[0], "name": r[1], "market": r[2]} for r in cur.fetchall()]
    conn.close()
    if not watch:
        return {"error": "no_us_watchlist", "hint": "先在持仓中心添加美股/港股自选"}

    # 1) 真实表现: 美股=隔夜(最新日线 vs 前一日); 港股=最近交易日
    perf = []
    for s in watch:
        if s["market"] == "US":
            bars = findata_db.us_kline(s["code"], "1d", 2)
        else:
            bars = findata_db.hk_kline_db(s["code"], "1d", 2)
            if len(bars) < 2:   # 池外冷门股库里没有, 回退Yahoo实时
                bars = yahoo_hk.hk_kline(s["code"], "1d", 2)
        if len(bars) < 2:
            continue
        chg = (bars[-1]["close"] - bars[-2]["close"]) / bars[-2]["close"] * 100
        perf.append({"code": s["code"], "name": s["name"], "market": s["market"],
                     "currency": "USD" if s["market"] == "US" else "HKD",
                     "close": bars[-1]["close"], "chg": round(chg, 2),
                     "date": bars[-1]["ts"][:10]})
    if not perf:
        return {"error": "no_data"}
    perf.sort(key=lambda x: -abs(x["chg"]))
    ups = sum(1 for p in perf if p["chg"] > 0)
    session_date = perf[0]["date"]

    # 2) 临近财报
    codes = {p["code"] for p in perf}
    upcoming = []
    for day in earnings_cal.week_earnings():
        for it in day["items"]:
            if it["symbol"] in codes:
                upcoming.append(f"{it['symbol']} {day['weekday']}{day['date'][5:]}"
                                f"{it.get('when', '')} 预期EPS{it.get('eps_forecast') or '--'}")

    perf_lines = "\n".join(
        f"- {'[美]' if p['market'] == 'US' else '[港]'}{p['name']}({p['code']}): 收{p['close']}{p['currency']} {p['chg']:+.2f}%"
        for p in perf)
    prompt = f"""你是投研助理。基于以下真实数据, 为用户写一份简明的美港股复盘(美股为隔夜表现, 港股为最近交易日, 数据日{session_date})。

【自选股表现】{ups}涨{len(perf) - ups}跌
{perf_lines}
【临近财报】{'; '.join(upcoming) or '无'}

要求: 只用给定数据, 严禁编造; 中文口语化但专业; 严格按JSON输出:
{{"headline": "一句话总结(<=30字)", "portfolio": "组合表现一句话",
  "highlights": ["要点1(点名最大异动及幅度)", "要点2", "要点3"],
  "watch_today": ["今日关注1", "今日关注2"]}}"""

    api_key = os.getenv("ONE_API_KEY", "")
    ai = None
    if api_key:
        try:
            client = OpenAI(api_key=api_key,
                            base_url=os.getenv("ONE_API_BASE_URL", "http://104.197.139.51:3000/v1"),
                            timeout=60)
            resp = client.chat.completions.create(
                model=os.getenv("ONE_API_MODEL", "gemini-3-flash-preview"),
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}, temperature=0.3, max_tokens=800)
            ai = json.loads(resp.choices[0].message.content or "{}")
        except Exception as e:
            log.warning("recap llm failed: %s", e)
    if not ai or "headline" not in ai:
        # LLM失败时降级为规则文本, 数据仍真实
        top = perf[0]
        ai = {"headline": f"隔夜{ups}涨{len(perf) - ups}跌, 最大异动{top['name']}{top['chg']:+.1f}%",
              "portfolio": f"{ups}涨{len(perf) - ups}跌",
              "highlights": [f"{p['name']} {p['chg']:+.2f}%" for p in perf[:3]],
              "watch_today": upcoming[:2] or ["暂无重点日程"]}

    result = {"date": session_date, "stocks": perf, "ai": ai,
              "upcoming_earnings": upcoming,
              "disclaimer": "AI生成 · 仅供研究 · 不构成投资建议"}
    _cache_set(ck, result, 43200)
    return result
