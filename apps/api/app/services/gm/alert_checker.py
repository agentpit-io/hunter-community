"""gm告警触发检测后台循环 —— main.py lifespan 启动, 每5分钟一轮。

规则评估(只在对应市场交易时段附近检查, 降低无效轮询):
  change_abs: |当日涨跌%| >= threshold → 触发
  earnings_soon: 距财报天数 <= threshold(仅美股, 每日最多一次)
降噪: 冷却时间(默认240分钟) + 北京时间0-8点仅推高风险(≥5%异动)
触发动作: 微信模板推送(复用wx_push.broadcast) + 写 gm_alert_hits 供消息页展示
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))
CHECK_INTERVAL = 300


def _check_once_sync() -> list[dict]:
    """同步评估所有启用规则, 返回触发列表 [{user_id, alert_id, title, detail, url}]"""
    from app.services.database import get_conn
    from app.services.gm import findata_db, yahoo_hk
    from app.routers.gm.alerts import ensure_tables
    ensure_tables()
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""SELECT id, user_id, market, code, name, rule_type, threshold,
                          cooldown_minutes, last_triggered_at
                   FROM gm_alerts WHERE enabled = TRUE""")
    rules = cur.fetchall()
    now = datetime.now(timezone.utc)
    bj_hour = datetime.now(CST).hour
    night = 0 <= bj_hour < 8
    fired = []
    for rid, uid, market, code, name, rtype, threshold, cooldown, last_trig in rules:
        threshold = float(threshold or 0)
        if last_trig and (now - last_trig).total_seconds() < (cooldown or 240) * 60:
            continue
        try:
            if rtype == "change_abs":
                q = findata_db.us_quote(code) if market == "US" else yahoo_hk.hk_quote(code)
                if not q or q.get("change_pct") is None:
                    continue
                chg = float(q["change_pct"])
                if abs(chg) < threshold:
                    continue
                if night and abs(chg) < 5:  # 夜间只推高风险
                    continue
                disp = name or q.get("name") or code
                fired.append({
                    "user_id": uid, "alert_id": rid,
                    "title": f"{disp} {'大涨' if chg > 0 else '大跌'} {abs(chg):.1f}%",
                    "detail": f"现价 {q.get('price')} {q.get('currency')} · 触发条件 ±{threshold}% · {q.get('market_state_label', '')}",
                    "path": f"/gm/stock/{'us' if market == 'US' else 'hk'}/{code}",
                })
            elif rtype == "earnings_soon" and market == "US":
                if night:
                    continue
                q = findata_db.us_quote(code)
                days = q.get("earnings_in_days") if q else None
                if days is None or days > threshold:
                    continue
                disp = name or (q.get("name") if q else code) or code
                fired.append({
                    "user_id": uid, "alert_id": rid,
                    "title": f"{disp} 财报{'今日' if days == 0 else f'{int(days)}天后'}发布",
                    "detail": f"距财报 {int(days)} 天 · 建议关注预期与持仓风险",
                    "path": f"/gm/stock/us/{code}",
                })
        except Exception as e:
            log.warning("gm alert eval id=%s failed: %s", rid, e)
    # 更新触发时间 + 写hits
    for f in fired:
        cur.execute("UPDATE gm_alerts SET last_triggered_at = NOW() WHERE id = %s", (f["alert_id"],))
        cur.execute("INSERT INTO gm_alert_hits (user_id, alert_id, title, detail) VALUES (%s,%s,%s,%s)",
                    (f["user_id"], f["alert_id"], f["title"], f["detail"]))
    conn.commit(); conn.close()
    return fired


# ── 每日08:00隔夜复盘推送 ──
_recap_pushed_date: str | None = None


def _recap_users() -> list[str]:
    from app.services.database import get_conn
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT DISTINCT user_id FROM stocks WHERE enabled = TRUE AND market IN ('US','HK')")
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows


async def _maybe_push_recaps():
    """每日08:00(北京)为有美港股自选的用户生成隔夜复盘并微信推送。
    只在周二~周六跑(覆盖隔夜美股交易日); 生成即写缓存, 用户打开App秒出。"""
    global _recap_pushed_date
    now = datetime.now(CST)
    today = now.date().isoformat()
    if now.hour != 8 or _recap_pushed_date == today:
        return
    _recap_pushed_date = today
    if now.weekday() not in (1, 2, 3, 4, 5):
        return
    import os
    from app.routers.gm.recap import build_recap
    from app.services.wx_push import broadcast
    oa = os.getenv("OA_DOMAIN", "https://yiqihecheng.net")
    users = await asyncio.to_thread(_recap_users)
    sent = 0
    for uid in users:
        try:
            rec = await asyncio.to_thread(build_recap, uid)
            if not rec or rec.get("error"):
                continue
            ai = rec.get("ai") or {}
            content = (str(ai.get("portfolio", "")) + "\n" +
                       "\n".join("· " + h for h in (ai.get("highlights") or [])[:3]))[:500]
            sent += await broadcast(title=f"🌙 隔夜复盘 · {ai.get('headline', rec.get('date', ''))}",
                                    content=content, log_id="", user_id=uid,
                                    push_type="gm_recap", detail_url=oa + "/gm/recap")
        except Exception as e:
            log.warning("[gm_recap] push uid=%s failed: %s", uid, e)
    log.info("[gm_recap] daily 08:00 done: users=%d wx_sent=%d", len(users), sent)


async def run_loop():
    log.info("[gm_alerts] checker loop started (interval %ss)", CHECK_INTERVAL)
    await asyncio.sleep(30)  # 起服后缓一下
    while True:
        try:
            await _maybe_push_recaps()
            fired = await asyncio.to_thread(_check_once_sync)
            if fired:
                from app.services.wx_push import broadcast
                import os
                oa = os.getenv("OA_DOMAIN", "https://yiqihecheng.net")
                for f in fired:
                    try:
                        await broadcast(title=f["title"], content=f["detail"], log_id="",
                                        user_id=f["user_id"], push_type="gm_alert",
                                        detail_url=oa + f["path"])
                    except Exception as e:
                        log.warning("gm alert push failed: %s", e)
                log.info("[gm_alerts] fired %d alerts", len(fired))
        except Exception as e:
            log.warning("[gm_alerts] loop error: %s", e)
        await asyncio.sleep(CHECK_INTERVAL)
