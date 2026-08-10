"""gm告警规则 CRUD —— /api/gm/alerts
规则类型: change_abs(涨跌幅绝对值≥X%) / earnings_soon(财报前N天提醒)
表 gm_alerts / gm_alert_hits 惰性建表(不动A股init_db)。"""
import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from app.services.database import get_conn

log = logging.getLogger(__name__)
router = APIRouter()

_DDL = """
CREATE TABLE IF NOT EXISTS gm_alerts (
  id SERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  market TEXT, code TEXT, name TEXT,
  rule_type TEXT NOT NULL,
  threshold NUMERIC,
  enabled BOOLEAN DEFAULT TRUE,
  cooldown_minutes INT DEFAULT 240,
  last_triggered_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_gm_alerts_user ON gm_alerts(user_id);
CREATE TABLE IF NOT EXISTS gm_alert_hits (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  alert_id INT,
  title TEXT, detail TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_gm_hits_user ON gm_alert_hits(user_id, created_at DESC);
"""
_ddl_done = False


def ensure_tables():
    global _ddl_done
    if _ddl_done:
        return
    conn = get_conn(); cur = conn.cursor()
    cur.execute(_DDL); conn.commit(); conn.close()
    _ddl_done = True


class AlertIn(BaseModel):
    market: str          # US / HK
    code: str
    name: str = ""
    rule_type: str       # change_abs / earnings_soon
    threshold: float


class AlertPatch(BaseModel):
    enabled: bool | None = None
    threshold: float | None = None


def _uid(request: Request) -> str:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "需要登录")
    return user_id


@router.get("/alerts")
async def gm_list_alerts(request: Request):
    user_id = _uid(request)
    ensure_tables()
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""SELECT id, market, code, name, rule_type, threshold, enabled, last_triggered_at
                   FROM gm_alerts WHERE user_id = %s ORDER BY id DESC""", (user_id,))
    rows = cur.fetchall(); conn.close()
    return {"items": [{
        "id": r[0], "market": r[1], "code": r[2], "name": r[3],
        "rule_type": r[4], "threshold": float(r[5]) if r[5] is not None else None,
        "enabled": r[6],
        "last_triggered_at": r[7].isoformat() if r[7] else None,
    } for r in rows]}


@router.post("/alerts")
async def gm_add_alert(body: AlertIn, request: Request):
    user_id = _uid(request)
    if body.rule_type not in ("change_abs", "earnings_soon"):
        raise HTTPException(400, "rule_type 必须是 change_abs / earnings_soon")
    if body.market not in ("US", "HK"):
        raise HTTPException(400, "market 必须是 US / HK")
    if body.rule_type == "change_abs" and not (0.5 <= body.threshold <= 50):
        raise HTTPException(400, "涨跌幅阈值需在 0.5~50 之间")
    if body.rule_type == "earnings_soon" and not (1 <= body.threshold <= 14):
        raise HTTPException(400, "财报提前天数需在 1~14 之间")
    # 与 alert_checker/quote 保持一致的代码归一化, 避免 700 vs 00700 对不上
    code = body.code.strip().upper() if body.market == "US" else body.code.strip().zfill(5)
    ensure_tables()
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""INSERT INTO gm_alerts (user_id, market, code, name, rule_type, threshold)
                   VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                (user_id, body.market, code, body.name, body.rule_type, body.threshold))
    aid = cur.fetchone()[0]; conn.commit(); conn.close()
    return {"ok": True, "id": aid}


@router.patch("/alerts/{alert_id}")
async def gm_patch_alert(alert_id: int, body: AlertPatch, request: Request):
    user_id = _uid(request)
    ensure_tables()
    conn = get_conn(); cur = conn.cursor()
    if body.enabled is not None:
        cur.execute("UPDATE gm_alerts SET enabled=%s WHERE id=%s AND user_id=%s",
                    (body.enabled, alert_id, user_id))
    if body.threshold is not None:
        cur.execute("UPDATE gm_alerts SET threshold=%s WHERE id=%s AND user_id=%s",
                    (body.threshold, alert_id, user_id))
    conn.commit(); conn.close()
    return {"ok": True}


@router.delete("/alerts/{alert_id}")
async def gm_del_alert(alert_id: int, request: Request):
    user_id = _uid(request)
    ensure_tables()
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM gm_alerts WHERE id=%s AND user_id=%s", (alert_id, user_id))
    conn.commit(); conn.close()
    return {"ok": True}
