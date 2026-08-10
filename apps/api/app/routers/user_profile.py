"""用户画像与记忆 API。

面向用户:  /api/chat/profile  · /api/chat/memory  · /api/chat/system-prompt
面向 BFF:  /api/chat/system-prompt(把画像压成 system prompt 随消息带给模型)
面向 admin:/api/user-insight/*(仅统计与画像, **不含对话原文**)
"""
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.services import user_memory as um
from app.services.database import get_conn

log = logging.getLogger(__name__)
router = APIRouter()


def _uid(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, "需要登录")
    return str(uid)


# ── 画像 ────────────────────────────────────────────────

@router.get("/chat/profile")
async def read_profile(request: Request):
    uid = _uid(request)
    return {
        "profile": um.get_profile(uid),
        "options": {
            "risk_styles": um.RISK_STYLES, "horizons": um.HORIZONS,
            "markets": um.MARKETS, "cap_prefs": um.CAP_PREFS,
            "weights": um.WEIGHTS, "verbosity": um.VERBOSITY,
            "labels": um._LABEL,
        },
    }


class ProfileIn(BaseModel):
    risk_style: str | None = None
    max_drawdown: int | None = None
    horizon: str | None = None
    markets: list[str] | None = None
    sectors: list[str] | None = None
    cap_pref: str | None = None
    weight_order: list[str] | None = None
    verbosity: str | None = None
    taboos: list[str] | None = None
    onboarded: bool | None = None


@router.put("/chat/profile")
async def write_profile(body: ProfileIn, request: Request):
    uid = _uid(request)
    patch = {k: v for k, v in body.model_dump().items() if v is not None}

    # 枚举校验 —— 不合法的值会让 system prompt 出现乱码般的字眼
    checks = [("risk_style", um.RISK_STYLES), ("horizon", um.HORIZONS),
              ("cap_pref", um.CAP_PREFS), ("verbosity", um.VERBOSITY)]
    for field, allowed in checks:
        v = patch.get(field)
        if v and v not in allowed:
            raise HTTPException(400, f"{field} 取值应为 {allowed}")
    for field, allowed in [("markets", um.MARKETS), ("weight_order", um.WEIGHTS)]:
        vs = patch.get(field)
        if vs and any(x not in allowed for x in vs):
            raise HTTPException(400, f"{field} 含非法取值")
    if patch.get("max_drawdown") is not None and not (1 <= patch["max_drawdown"] <= 100):
        raise HTTPException(400, "可接受回撤应在 1~100 之间")
    for field in ("sectors", "taboos"):
        if patch.get(field) is not None:
            patch[field] = [str(x).strip()[:12] for x in patch[field] if str(x).strip()][:12]

    return {"profile": um.save_profile(uid, patch), "msg": "已保存 · 之后的对话会按新偏好调整"}


# ── 记忆 ────────────────────────────────────────────────

@router.get("/chat/memory")
async def read_memory(request: Request):
    uid = _uid(request)
    d = um.get_memory(uid)
    return {**d, "system_prompt_preview": um.build_system_prompt(uid)}


class MemoryIn(BaseModel):
    memory: dict


@router.put("/chat/memory")
async def write_memory(body: MemoryIn, request: Request):
    """用户手动编辑记忆 —— 隐私底线之一:记错了得能改。"""
    uid = _uid(request)
    return {"memory": um.save_memory(uid, body.memory or {}, session_id="(user-edited)")["memory"]}


@router.delete("/chat/memory")
async def wipe_memory(request: Request):
    uid = _uid(request)
    um.clear_memory(uid)
    return {"ok": True, "msg": "记忆已清空"}


@router.get("/chat/system-prompt")
async def system_prompt(request: Request):
    """供 web BFF 调用:把画像+记忆压成一段 system,随每条消息带给模型。"""
    uid = _uid(request)
    return {"system": um.build_system_prompt(uid)}


class CondenseIn(BaseModel):
    session_id: str = ""
    texts: list[str]
    symbols: dict[str, str] | None = None


@router.post("/chat/memory/condense")
async def condense(body: CondenseIn, request: Request):
    """会话结束后浓缩。由 BFF 在用户离开会话时触发。"""
    uid = _uid(request)
    texts = [t for t in (body.texts or []) if t and t.strip()][:200]
    if not texts:
        return {"ok": True, "skipped": "无内容"}
    try:
        mem = um.condense(uid, body.session_id, texts, body.symbols)
    except Exception as e:
        log.warning("[memory] 浓缩失败: %s", e)
        raise HTTPException(503, "记忆写入失败")
    return {"ok": True, "memory": mem}


# ── admin 后台:用户洞察 ────────────────────────────────
#
# ⚠️ 隐私边界(硬性):
#   能看  画像设置项 / 浓缩后的记忆 / 会话数量与活跃时间
#   不能看 对话原文 / 具体问了哪只股票的哪句话
# 后台的价值是理解用户群体, 不是监视个人。所有接口都不返回 message 内容。

def _require_admin(request: Request) -> str:
    from app.routers.backtest import _require_admin as ra
    return ra(request)


@router.get("/user-insight/overview")
async def insight_overview(request: Request):
    _require_admin(request)
    c = get_conn(); cur = c.cursor()
    cur.execute("SELECT count(*) FROM user_profile")
    profiles = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM user_memory WHERE memory <> '{}'::jsonb")
    memories = cur.fetchone()[0]
    cur.execute("SELECT count(DISTINCT user_id) FROM chat_session_owner WHERE NOT archived")
    chatters = cur.fetchone()[0]

    def dist(col: str) -> list[dict]:
        cur.execute(f"""SELECT {col}, count(*) FROM user_profile
                        WHERE {col} <> '' GROUP BY {col} ORDER BY count(*) DESC""")
        return [{"value": r[0], "label": um._LABEL.get(r[0], r[0]), "n": r[1]}
                for r in cur.fetchall()]

    cur.execute("""SELECT s, count(*) FROM user_profile, unnest(sectors) AS s
                   GROUP BY s ORDER BY count(*) DESC LIMIT 10""")
    sectors = [{"value": r[0], "n": r[1]} for r in cur.fetchall()]
    cur.execute("""SELECT m, count(*) FROM user_profile, unnest(markets) AS m
                   GROUP BY m ORDER BY count(*) DESC""")
    markets = [{"value": r[0], "label": um._LABEL.get(r[0], r[0]), "n": r[1]} for r in cur.fetchall()]

    out = {
        "profiled_users": profiles, "users_with_memory": memories, "chat_users": chatters,
        "risk_style": dist("risk_style"), "horizon": dist("horizon"),
        "verbosity": dist("verbosity"), "sectors": sectors, "markets": markets,
    }
    c.close()
    return out


def _identities(cur, uids: list[str]) -> dict:
    """user_id → 邮箱/昵称。

    cuid 这种 ID 给人看毫无意义,后台必须显示"是谁"。
    hermes 库没有 users 表(账号在 agentpit 那边, 经 SSO 传过来),
    邮箱散落在几张业务表里, 这里按可靠性依次回填。
    """
    if not uids:
        return {}
    out: dict[str, dict] = {}
    for sql in (
        "SELECT user_id, email, nickname FROM ax_event WHERE user_id = ANY(%s) AND email <> ''",
        "SELECT user_id, email, '' FROM chat_session_owner o JOIN ax_event a USING(user_id) WHERE o.user_id = ANY(%s)",
    ):
        try:
            cur.execute(sql, (uids,))
            for r in cur.fetchall():
                if r[0] and r[0] not in out:
                    out[r[0]] = {"email": r[1] or "", "nickname": r[2] or ""}
        except Exception:
            continue
    return out


@router.get("/user-insight/users")
async def insight_users(request: Request, limit: int = Query(50, ge=1, le=200), offset: int = 0):
    _require_admin(request)
    c = get_conn(); cur = c.cursor()
    cur.execute("""
        SELECT COALESCE(p.user_id, m.user_id, o.user_id) AS uid,
               p.risk_style, p.horizon, p.sectors, p.markets, p.updated_at,
               COALESCE(m.session_count, 0),
               COALESCE(o.n, 0), o.last_used, m.memory
        FROM user_profile p
        FULL JOIN user_memory m ON m.user_id = p.user_id
        FULL JOIN (SELECT user_id, count(*) n, max(last_used_at) last_used
                   FROM chat_session_owner WHERE NOT archived GROUP BY user_id) o
             ON o.user_id = COALESCE(p.user_id, m.user_id)
        ORDER BY o.last_used DESC NULLS LAST
        LIMIT %s OFFSET %s""", (limit, offset))
    raw = cur.fetchall()
    ids = [r[0] for r in raw if r[0]]
    who = _identities(cur, ids)

    rows = []
    for r in raw:
        mem = r[9] if isinstance(r[9], dict) else {}
        syms = mem.get("mentioned_symbols") or []
        topics = mem.get("recurring_topics") or []
        ident = who.get(r[0], {})
        # 画像完整度 —— 一眼看出谁是真填了、谁是全跳过
        filled = sum([bool(r[1]), bool(r[2]), bool(r[4]), bool(r[3])])
        rows.append({
            "user_id": r[0],
            "email": ident.get("email", ""), "nickname": ident.get("nickname", ""),
            "risk_style": r[1] or "", "risk_label": um._LABEL.get(r[1] or "", ""),
            "horizon": r[2] or "", "horizon_label": um._LABEL.get(r[2] or "", ""),
            "sectors": list(r[3] or []), "markets": list(r[4] or []),
            "profile_filled": filled, "profile_total": 4,
            "profile_updated": r[5].isoformat() if r[5] else None,
            "condensed_sessions": r[6], "chat_sessions": r[7],
            "last_active": r[8].isoformat() if r[8] else None,
            # 记忆摘要直接进列表 —— 不用点进详情就能看出这人关心什么
            "top_symbols": [s.get("name") or s.get("code") for s in syms[:3]],
            "top_topics": [t.get("topic") for t in topics[:3]],
        })
    c.close()
    return {"items": rows, "count": len(rows)}


@router.get("/user-insight/users/{user_id}")
async def insight_user_detail(user_id: str, request: Request):
    """单用户画像 + 浓缩记忆。**不返回任何对话原文。**"""
    _require_admin(request)
    c = get_conn(); cur = c.cursor()
    ident = _identities(cur, [user_id]).get(user_id, {})
    cur.execute("""SELECT count(*), max(last_used_at) FROM chat_session_owner
                   WHERE user_id = %s AND NOT archived""", (user_id,))
    n, last = cur.fetchone()
    c.close()
    return {
        "user_id": user_id,
        "email": ident.get("email", ""), "nickname": ident.get("nickname", ""),
        "chat_sessions": n or 0,
        "last_active": last.isoformat() if last else None,
        "profile": um.get_profile(user_id),
        **um.get_memory(user_id),
        # 后台看到的画像 = 实际注入给模型的那段,所见即所得
        "system_prompt": um.build_system_prompt(user_id),
        "note": "后台仅展示画像与浓缩记忆,不含对话原文",
    }
