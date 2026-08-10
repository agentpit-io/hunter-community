"""gm消息中心(只读事件流) —— GET /api/gm/messages
按用户自选聚合: 异动(|涨跌|>=3%) + 7天内财报 + 最新官方公告。告警规则CRUD后置。"""
from fastapi import APIRouter, HTTPException, Request
from app.services.database import get_conn
from app.services.gm import findata_db, yahoo_hk, earnings_cal, filings

router = APIRouter()


def _watch(user_id: str) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT code, name, market FROM stocks "
                "WHERE enabled = TRUE AND user_id = %s AND market IN ('US','HK')", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return [{"code": r[0], "name": r[1], "market": r[2]} for r in rows]


@router.get("/messages")
async def gm_messages(request: Request):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "需要登录")
    watch = _watch(user_id)
    feed = []

    # 0) 告警触发历史(近3天)
    try:
        from app.routers.gm.alerts import ensure_tables
        ensure_tables()
        conn = get_conn(); cur = conn.cursor()
        cur.execute("""SELECT title, detail, created_at FROM gm_alert_hits
                       WHERE user_id = %s AND created_at > NOW() - INTERVAL '3 days'
                       ORDER BY created_at DESC LIMIT 10""", (user_id,))
        for t, d, ts in cur.fetchall():
            feed.append({"level": "high", "icon": "🚨", "title": t, "desc": d,
                         "ts": ts.isoformat(), "type": "告警"})
        conn.close()
    except Exception:
        pass

    # 1) 异动
    for s in watch:
        q = findata_db.us_quote(s["code"]) if s["market"] == "US" else yahoo_hk.hk_quote(s["code"])
        if not q or q.get("change_pct") is None:
            continue
        chg = q["change_pct"]
        if abs(chg) >= 3:
            feed.append({
                "level": "high" if abs(chg) >= 5 else "mid",
                "icon": "📉" if chg < 0 else "📈",
                "title": f"{s['name']} {'大涨' if chg > 0 else '大跌'} {abs(chg):.1f}%",
                "desc": f"现价 {q.get('price')} {q.get('currency')} · {q.get('market_state_label', '')}",
                "ts": q.get("ts") or "",
                "type": "异动",
            })

    # 2) 财报临近(美股)
    us_codes = {s["code"] for s in watch if s["market"] == "US"}
    if us_codes:
        for day in earnings_cal.week_earnings():
            for it in day["items"]:
                if it["symbol"] in us_codes:
                    feed.append({
                        "level": "mid", "icon": "📅",
                        "title": f"{it['symbol']} 财报 · {day['weekday']} {day['date'][5:]}",
                        "desc": f"{it.get('when', '')} 预期EPS {it.get('eps_forecast') or '--'}",
                        "ts": day["date"], "type": "财报",
                    })

    # 3) 最新公告(每只1条)
    for s in watch[:10]:
        items = (filings.us_filings(s["code"], 1) if s["market"] == "US"
                 else filings.hk_filings(s["code"], 1))
        if items:
            f = items[0]
            feed.append({
                "level": "watch", "icon": "📄",
                "title": f"{s['name']} · {f['title']}",
                "desc": f"{f['date']} · 官方公告", "ts": f["date"],
                "type": "公告", "url": f.get("url", ""),
            })

    # 同级内最新在顶: 先按时间倒序, 再按级别稳定排序
    order = {"high": 0, "mid": 1, "watch": 2}
    feed.sort(key=lambda x: str(x.get("ts", "")), reverse=True)
    feed.sort(key=lambda x: order.get(x["level"], 9))
    return {"items": feed, "count": len(feed), "watch_count": len(watch)}
