"""Hunter platform key · status / save / clear  —  GET·PUT·DELETE /api/hunter/unlock

Backs the bottom-left "解锁全部工具" button in the chat sidebar. The UI needs to
know three things and this router answers all of them in one GET:

  · configured  — is there a key at all (so the button reads 解锁 vs 已解锁)
  · unlocked    — does that key actually work upstream (a revoked key is
                  configured but not unlocked · the UI must say so, not
                  silently fail on the first tool call)
  · tools       — the list to show, returned even while locked so the sidebar
                  can display everything and prompt on click

Auth: any logged-in user of this instance. It's software you run on your own
machine; there is no reason to gate it behind an admin role.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services import hunter_key

router = APIRouter(prefix="/hunter", tags=["hunter-unlock"])


def _uid(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, "需要登录")
    return str(uid)


class KeyIn(BaseModel):
    key: str


@router.get("/unlock")
async def status(request: Request):
    _uid(request)
    key = hunter_key.resolve()
    m = await hunter_key.manifest(key)
    return {
        "configured": bool(key),
        "unlocked": bool(m.get("unlocked")),
        "masked": hunter_key.masked(key),
        "env_locked": hunter_key.env_locked(),
        "apply_url": m.get("apply_url") or hunter_key.APPLY_URL,
        "message": m.get("message"),
        "tools": m.get("tools") or [],
        "upstream_error": bool(m.get("upstream_error")),
    }


@router.put("/unlock")
async def save(body: KeyIn, request: Request):
    """Verify before storing — saving a typo'd key and only finding out on the
    first tool call is the worst version of this flow."""
    _uid(request)
    if hunter_key.env_locked():
        raise HTTPException(
            409, "这台实例的 key 来自 .env（HUNTER_API_KEY），请改 .env 后重启容器")
    plain = body.key.strip()
    if not plain:
        raise HTTPException(400, "key 不能为空")

    m = await hunter_key.manifest(plain)
    if m.get("upstream_error"):
        raise HTTPException(503, m.get("message") or "连不上 Hunter 服务器")
    if not m.get("unlocked"):
        raise HTTPException(400, "这把 key 无效或已吊销，请到 " + hunter_key.APPLY_URL + " 重新申请")

    hunter_key.save(plain)
    return await status(request)


@router.delete("/unlock")
async def clear(request: Request):
    _uid(request)
    if hunter_key.env_locked():
        raise HTTPException(409, "key 来自 .env，请改 .env 后重启容器")
    hunter_key.clear()
    return {"ok": True, "configured": False, "unlocked": False}


# ═══════════════════════════════════════════════════════════════
# 能力矩阵 · 一 key 解锁什么(2026-08-29 UnlockModal UI 改造)
# 关联方案:doc/开源hunter-community/05hunterSkill/2026-08-29_*.md
# 三视角聚合:市场 / 数据类型 / 用途 SKILL
# 数据来源:source_catalog.CATALOG + tool_catalog.CATALOG + skill_files.load_all()
# 严禁 mock 兜底 · CATALOG 空返空数字 · 不假装有数据
# ═══════════════════════════════════════════════════════════════

_MARKET_FLAG = {"a": "🇨🇳", "hk": "🇭🇰", "us": "🇺🇸", "global": "🌐"}
_MARKET_ORDER = ["a", "hk", "us", "global"]


@router.get("/capabilities/matrix")
async def capabilities_matrix(request: Request):
    """能力矩阵 · UnlockModal 用来展示"一把 key 解锁什么"

    返 4 块:
      summary            数字大字(数据源/工具/SKILL 各 total/need_key/free)
      by_market          按市场分组 · 每个市场几条数据 · highlights
      by_kind            按数据类型分组 · 12 类 × 4 市场矩阵(前端画表格)
      by_skill_category  按 SKILL 用途分组 · UZI 系列自然聚在综合分析下
    """
    _uid(request)
    from app.services import source_catalog as sc
    from app.services import tool_catalog as tc
    from app.services import skill_files

    sources = list(sc.CATALOG)
    tools = list(tc.CATALOG)
    try:
        skills = list(skill_files.load_all())
    except Exception:
        skills = []

    # ── summary · 三层数字 ─────────────────────────────
    def _src_need_key(s) -> bool:
        return bool(getattr(s, "requires_key", False)) and getattr(s, "available", True)

    def _src_unavail(s) -> bool:
        return not getattr(s, "available", True)

    def _tool_need_key(t) -> bool:
        return len(getattr(t, "needs_data", [])) > 0

    def _skill_need_key(s: dict) -> bool:
        tools_needed = s.get("needs_tools") or []
        for k in tools_needed:
            e = tc.get(k) if hasattr(tc, "get") else None
            if e and _tool_need_key(e):
                return True
        return False

    src_need = sum(1 for s in sources if _src_need_key(s))
    src_free = sum(1 for s in sources
                   if not _src_need_key(s) and not _src_unavail(s))
    src_unavail = sum(1 for s in sources if _src_unavail(s))

    tool_need = sum(1 for t in tools if _tool_need_key(t))
    tool_free = len(tools) - tool_need

    skill_need = sum(1 for s in skills if _skill_need_key(s))
    skill_free = len(skills) - skill_need

    summary = {
        "sources": {"total": len(sources), "need_key": src_need,
                    "free": src_free, "unavailable": src_unavail},
        "tools":   {"total": len(tools),   "need_key": tool_need, "free": tool_free},
        "skills":  {"total": len(skills),  "need_key": skill_need, "free": skill_free},
    }

    # ── by_market · 按市场聚合 ──────────────────────────
    by_market: list[dict] = []
    for mk in _MARKET_ORDER:
        try:
            mv = sc.Market(mk)
        except Exception:
            continue
        m_srcs = [s for s in sources if s.market == mv]
        if not m_srcs:
            continue
        need = [s for s in m_srcs if _src_need_key(s)]
        free = [s for s in m_srcs if not _src_need_key(s) and not _src_unavail(s)]
        highlights = [s.name for s in need[:5]]  # 前 5 名列 UI
        by_market.append({
            "key": mk, "label": sc.MARKET_LABEL[mv], "flag": _MARKET_FLAG[mk],
            "need_key": len(need), "free": len(free),
            "highlights": highlights,
            "sources": [_src_to_grid(s) for s in m_srcs],
        })

    # ── by_kind · 12 数据类型 × 4 市场 矩阵 ─────────────
    by_kind: list[dict] = []
    for kd_val in [
        sc.DataKind.QUOTE, sc.DataKind.KLINE, sc.DataKind.NEWS,
        sc.DataKind.ANNOUNCE, sc.DataKind.FINANCIAL, sc.DataKind.CAPITAL,
        sc.DataKind.HOLDER, sc.DataKind.RESEARCH, sc.DataKind.VALUATION,
        sc.DataKind.FORECAST, sc.DataKind.INTEL, sc.DataKind.GEO,
    ]:
        markets_cell: dict[str, dict | None] = {}
        for mk in _MARKET_ORDER:
            try:
                mv = sc.Market(mk)
            except Exception:
                markets_cell[mk] = None
                continue
            match = [s for s in sources if s.kind == kd_val and s.market == mv]
            if not match:
                markets_cell[mk] = None
            else:
                # 优先展示 official · 否则 free
                official = next((s for s in match if _src_need_key(s)), None)
                free_one = next((s for s in match if not _src_need_key(s)), None)
                pick = official or free_one
                markets_cell[mk] = {
                    "source_key": pick.key,
                    "source_name": pick.name,
                    "status": "official" if _src_need_key(pick) else "free",
                    "count_in_cell": len(match),
                }
        # 只保留至少一格有数据的行
        if any(v is not None for v in markets_cell.values()):
            by_kind.append({
                "key": kd_val.value,
                "label": sc.KIND_LABEL[kd_val],
                "markets": markets_cell,
            })

    # ── by_skill_category · SKILL 按用途聚合 ────────────
    from collections import defaultdict
    cat_map: dict[str, list[dict]] = defaultdict(list)
    for s in skills:
        cat = s.get("category") or "其它"
        cat_map[cat].append({
            "key": s["key"],
            "name": s["name"],
            "icon": s.get("icon") or "✨",
            "prompt_tpl": s.get("prompt_tpl") or "",
            "brand": s.get("brand") or "",
            "hint": s.get("hint") or "",
            "need_key": _skill_need_key(s),
        })
    by_skill_category = [
        {"key": cat, "label": cat, "skills": items}
        for cat, items in cat_map.items()
    ]

    return {
        "summary": summary,
        "by_market": by_market,
        "by_kind": by_kind,
        "by_skill_category": by_skill_category,
        # 已配 key 判断(前端根据这个切"未解锁"/"已解锁"视图)
        "unlocked": bool(hunter_key.resolve()),
    }


def _src_to_grid(src) -> dict:
    """单条数据源转 UI 用的最小字段(供 by_market.sources 展开用)"""
    return {
        "key": src.key,
        "name": src.name,
        "kind": src.kind.value,
        "tier": src.tier.value,
        "requires_key": src.requires_key,
        "available": src.available,
        "upstream": src.upstream or "",
    }
