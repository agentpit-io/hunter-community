"""隔夜复盘(gm端) —— GET /api/gm/recap
聚合用户美股自选隔夜真实表现+临近财报 → Gemini中文复盘。按用户+日期缓存12h。"""
import os
import re
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

# 抽 "名字 ±X.XX%" 二元组, 用于 LLM 编造数字防护
# 2026-08-29 事故: Gemini 把 NVDA -4.57% 写成 +8.74% 全站找不到出处
_NUM_RE = re.compile(r"([一-龥A-Za-z0-9·\.·\- ]+?)\s*([+-]\d+(?:\.\d+)?)\s*%")


def _rule_ai(perf: list, ups: int, upcoming: list) -> dict:
    """规则模板产出 ai 结构, 数字必然来自 perf 真值, 用作 LLM 兜底或润色基线。"""
    if not perf:
        return {"headline": "无自选股数据", "portfolio": "", "highlights": [], "watch_today": []}
    top = perf[0]
    return {
        "headline": f"隔夜{ups}涨{len(perf) - ups}跌, {top['name']}{top['chg']:+.2f}%",
        "portfolio": f"{ups}涨{len(perf) - ups}跌",
        "highlights": [f"{p['name']} {p['chg']:+.2f}%" for p in perf[:3]],
        "watch_today": upcoming[:2] or ["暂无重点日程"],
    }


def _verify_numbers(payload: dict, perf: list) -> tuple[bool, str]:
    """遍历 payload 里所有字符串, 抽 (名字, 百分比) 二元组, 逐一比对 perf。
    返回 (是否全部匹配, 首个失败原因)。误差阈值 0.01 —— 基线本就用 .2f 输入,
    LLM 只要不改数字就能通过; 一旦四舍五入到 .1f 就当作编造(直接落回规则文本更安全)。"""
    name2chg = {p["name"]: p["chg"] for p in perf}
    texts: list[str] = []
    for k in ("headline", "portfolio"):
        v = payload.get(k)
        if isinstance(v, str):
            texts.append(v)
    for k in ("highlights", "watch_today"):
        for s in (payload.get(k) or []):
            if isinstance(s, str):
                texts.append(s)
    for t in texts:
        for name_raw, chg_str in _NUM_RE.findall(t):
            name = name_raw.strip(" ·-")
            try:
                chg = float(chg_str)
            except ValueError:
                return False, f"数字解析失败: '{chg_str}' in '{t}'"
            # 名字须为已知自选(允许前缀/后缀空白+中文标点)
            real = name2chg.get(name)
            if real is None:
                # 尝试宽松匹配: name 可能被 LLM 加了"美股"/"港股"前缀
                for n, c in name2chg.items():
                    if n in name or name.endswith(n):
                        real = c
                        break
            if real is None:
                return False, f"提到未在自选里的股票: '{name}' → {chg:+.2f}%"
            if abs(real - chg) >= 0.01:
                return False, f"数字与真值不符: {name} 真={real:+.2f}% LLM={chg:+.2f}%"
    return True, "ok"


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

    # 基线永远用规则模板产出(数字必真); LLM 只负责润色措辞, 数字须与基线完全一致才采信。
    # 2026-08-29 事故: Gemini 把 NVDA -4.57% 写成 +8.74%, 无任何校验直接推给用户
    ai = _rule_ai(perf, ups, upcoming)

    api_key = os.getenv("ONE_API_KEY", "")
    if api_key:
        polish_prompt = f"""基于以下真实数据, 润色 headline / portfolio / highlights / watch_today 的中文措辞。

【真实数据 · 数据日 {session_date}】
{perf_lines}
【临近财报】{'; '.join(upcoming) or '无'}

【当前文本(数字锁死, 你不能改)】
headline: {ai['headline']}
portfolio: {ai['portfolio']}
highlights: {json.dumps(ai['highlights'], ensure_ascii=False)}
watch_today: {json.dumps(ai['watch_today'], ensure_ascii=False)}

铁律:
1. 每一个百分比数字必须与"真实数据"里对应股票的 chg 完全一致(保留两位小数, 保留正负号), 一个字符都不能改
2. 不许提到不在自选里的股票, 不许编造新数字
3. 允许调整语气/加动词/补一句非数字的行情背景
4. 每支被提及的股票名, 必须直接使用【真实数据】里出现的原始名称
5. 严格返回 JSON, 结构与"当前文本"完全一致

返回: {{"headline":"...", "portfolio":"...", "highlights":["...","...","..."], "watch_today":["...","..."]}}"""
        try:
            client = OpenAI(api_key=api_key,
                            base_url=os.getenv("ONE_API_BASE_URL", "http://104.197.139.51:3000/v1"),
                            timeout=60)
            resp = client.chat.completions.create(
                model=os.getenv("ONE_API_MODEL", "gemini-3.8-flash"),
                messages=[{"role": "user", "content": polish_prompt}],
                response_format={"type": "json_object"}, temperature=0.2, max_tokens=800)
            polished = json.loads(resp.choices[0].message.content or "{}")
            ok, why = _verify_numbers(polished, perf)
            if ok and polished.get("headline"):
                ai = polished
            else:
                log.warning("recap llm polish rejected: %s | raw=%s", why, resp.choices[0].message.content)
        except Exception as e:
            log.warning("recap llm polish failed: %s", e)

    result = {"date": session_date, "stocks": perf, "ai": ai,
              "upcoming_earnings": upcoming,
              "disclaimer": "AI生成 · 仅供研究 · 不构成投资建议"}
    _cache_set(ck, result, 43200)
    return result
