"""能力目录接口 —— 三层模型的对外查询入口。

`_14` §6 Step B 第 3 项(数据源部分)。Step C 会在同一个前缀下补
`/catalog/toolbox` 与 `/catalog/skills`,构成侧栏三块的三个数据源。

**验收标准(`_14` §6 Step B)**:这个接口要能回答
「美股有哪些源、为什么不可用、量级多少」—— 所以每条都带 `unavailable_reason`
和 `volume_hint`,而不是只给一个布尔值。用户看到"美股 5 个源 0 个可用"却不知道
为什么,跟没有这个接口是一样的。
"""
from fastapi import APIRouter, HTTPException, Query

from app.services import source_catalog as catalog
from app.services import source_health
from app.services import tool_catalog

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/sources")
async def list_sources(
    market: str = Query("", description="a / hk / us / global · 空 = 全部"),
    usable_only: bool = Query(False, description="只看当前真能用的"),
):
    """数据源清单 · 按市场分组。"""
    groups = catalog.grouped()
    if market:
        groups = [g for g in groups if g["market"] == market.lower()]
        if not groups:
            raise HTTPException(400, f"未知市场 {market!r} · 可选 a/hk/us/global")
    if usable_only:
        for g in groups:
            g["sources"] = [s for s in g["sources"]
                            if s["status"] not in ("unavailable", "need_key")]

    all_groups = catalog.grouped()
    total = sum(g["total"] for g in all_groups)
    ready = sum(g["ready"] for g in all_groups)
    blocked = [s for g in all_groups for s in g["sources"] if s["status"] == "unavailable"]
    need_key = [s for g in all_groups for s in g["sources"] if s["status"] == "need_key"]
    return {
        "groups": groups,
        "summary": {
            "total": total,
            "ready": ready,
            # 侧栏标题直接显示"数据源 20/32",一眼看出还有多少没解锁
            "headline": f"{ready}/{total}",
            # 分开数是因为这两种"不可用"用户的动作完全不同:
            # need_key 去申请一把 key 就解决,unavailable 做什么都没用
            "need_key_count": len(need_key),
            "unavailable_count": len(blocked),
        },
    }


@router.get("/sources/{key}")
async def get_source(key: str):
    src = catalog.get(key)
    if not src:
        raise HTTPException(404, f"未知数据源 {key!r}")
    return catalog.to_dict(src)


@router.get("/sources-health")
async def sources_health():
    """只看健康数据 —— 排错用。

    与 `/sources` 分开是因为这个会被轮询,而 `/sources` 里的静态部分不必反复传。
    """
    return {"window": source_health.WINDOW, "stats": source_health.all_stats()}


# ── 工具箱层 ──────────────────────────────────────────────────

@router.get("/toolbox")
async def list_toolbox(ready_only: bool = Query(False, description="只看当前真能用的")):
    """工具箱清单 · 按 MCP server 分组。

    用户原话「mcp 和 tools 算一类」—— 所以这里不分两栏,
    来源差异只体现在每个条目的 `origin` 字段(内置/平台/你接的)。
    """
    groups = tool_catalog.grouped()
    if ready_only:
        for g in groups:
            g["tools"] = [t for t in g["tools"] if t["status"] == "ready"]
    total = sum(g["total"] for g in groups)
    ready = sum(g["ready"] for g in groups)
    return {
        "groups": groups,
        "summary": {"total": total, "ready": ready, "headline": f"{ready}/{total}"},
    }


# ── SKILL 层 ──────────────────────────────────────────────────

@router.get("/skills")
async def list_skills():
    """SKILL 清单 · 按 category 分组。

    **不复用 `/api/chat/skills`** 的原因:那个是聊天页在用的,返回结构要保持
    稳定(Step A 迁移时特意做到逐字段零差异)。这里是能力目录视角,要多带
    「依赖是否满足」这类算出来的东西,混在一起会把那个接口撑变形。

    分类直接用 SKILL.md 里 `hunter.category` 的值 —— 前端**不要再自己维护
    一份分类映射**。老的 SkillPanel 里硬编码了一份 4 类的表(单股/组合/决策/自建),
    只覆盖 29 个里的 11 个,其余全被错误归进"自建"(明明是内置的)。
    这就是 `_13` §3.1 说的同一份知识散落多处。
    """
    from app.services import skill_files

    items = skill_files.load_all()
    out = []
    for s in items:
        tools = s.get("needs_tools") or []
        missing = [t for t in tools if tool_catalog.get(t) is None]
        # partial 不算 blocked —— 工具能用,只是某几段内容不全。
        # 把"内容不全"说成"用不了",19 个照常工作的 SKILL 会被全标灰。
        not_ready = [t for t in tools
                     if (e := tool_catalog.get(t))
                     and tool_catalog.status_of(e)["state"] not in ("ready", "partial")]
        out.append({
            "key": s["key"],
            "name": s["name"],
            "icon": s["icon"],
            "hint": s["hint"],
            "category": s["category"],
            "brand": s.get("brand", ""),
            "source_url": s.get("source_url", ""),
            "prompt_tpl": s["prompt_tpl"],
            "builtin": s.get("builtin", True),
            "needs_tools": tools,
            # 三种"不能用"分开报,因为用户的下一步动作完全不同
            "missing_tools": missing,      # 声明的工具压根不存在 → 是我们的 bug
            "blocked_tools": not_ready,    # 工具存在但依赖没就绪 → 去配 key
            "status": "broken" if missing else ("blocked" if not_ready else "ready"),
        })

    order = {c: i for i, c in enumerate(skill_files.CATEGORY_ORDER)}
    groups: dict[str, list] = {}
    for s in out:
        groups.setdefault(s["category"], []).append(s)
    grouped = [{"category": c, "total": len(v),
                "ready": sum(1 for i in v if i["status"] == "ready"), "skills": v}
               for c, v in sorted(groups.items(), key=lambda kv: order.get(kv[0], 99))]

    return {
        "groups": grouped,
        "summary": {
            "total": len(out),
            "ready": sum(1 for s in out if s["status"] == "ready"),
            "headline": f"{sum(1 for s in out if s['status'] == 'ready')}/{len(out)}",
            "user_added": sum(1 for s in out if not s["builtin"]),
        },
    }
