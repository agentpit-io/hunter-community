"""能力目录接口 —— 三层模型的对外查询入口。

`_14` §6 Step B 第 3 项(数据源部分)。Step C 会在同一个前缀下补
`/catalog/toolbox` 与 `/catalog/skills`,构成侧栏三块的三个数据源。

**验收标准(`_14` §6 Step B)**:这个接口要能回答
「美股有哪些源、为什么不可用、量级多少」—— 所以每条都带 `unavailable_reason`
和 `volume_hint`,而不是只给一个布尔值。用户看到"美股 5 个源 0 个可用"却不知道
为什么,跟没有这个接口是一样的。
"""
from fastapi import APIRouter, HTTPException, Query, Request

from app.services import source_catalog as catalog
from app.services import source_health
from app.services import tool_catalog

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/sources")
async def list_sources(
    request: Request,
    market: str = Query("", description="a / hk / us / global · 空 = 全部"),
    by: str = Query("upstream", description="upstream(默认·按来源)/ market(旧·按市场)"),
    usable_only: bool = Query(False, description="只看当前真能用的"),
):
    """数据源清单。

    **默认按来源(upstream)分组** —— `_21` §2 换掉了原来的按市场分组。
    原因:按市场分是我们的视角,回答不了用户真正的问题
    「我有 Tushare 的 key,能接进来吗」。

    `by=market` 保留旧行为,因为概览页的市场统计还在用它 ——
    换分组维度不该顺手弄坏另一个页面。

    `market` 参数在两种模式下都还有效:
      · by=market   —— 筛掉别的市场分组(旧语义)
      · by=upstream —— 筛掉组内不属于该市场的条目(新语义,给筛选条用)
    """
    if by not in ("upstream", "market"):
        raise HTTPException(400, f"未知分组方式 {by!r} · 可选 upstream/market")

    mk = market.lower()
    if mk and mk not in {m.value for m in catalog.Market}:
        raise HTTPException(400, f"未知市场 {market!r} · 可选 a/hk/us/global")

    if by == "market":
        groups = catalog.grouped()
        if mk:
            groups = [g for g in groups if g["market"] == mk]
    else:
        user_id = getattr(request.state, "user_id", None)
        groups = catalog.grouped_by_upstream(user_id=user_id)
        if mk:
            # 按市场筛时,组内条目过滤后**重算计数** —— 直接沿用总数会让
            # 侧栏显示 "AKShare 7/7" 但点进去只有 5 条,是另一种形式的说谎。
            kept = []
            for g in groups:
                srcs = [s for s in g["sources"] if s["market"] == mk]
                if not srcs and g["upstream"] != "user":
                    continue
                g = {**g, "sources": srcs, "total": len(srcs),
                     "ready": len([s for s in srcs
                                   if s["status"] not in ("unavailable", "need_key")])}
                kept.append(g)
            groups = kept

    if usable_only:
        for g in groups:
            g["sources"] = [s for s in g["sources"]
                            if s["status"] not in ("unavailable", "need_key")]

    # summary 走 grouped()(按市场,不含用户源),再把用户源单独加回来。
    # 不这么做的话用户加了源,列表里能看见但顶部计数不动 —— 又是一处
    # "用户自己的东西在某个视图里不存在"。
    all_groups = catalog.grouped()
    total = sum(g["total"] for g in all_groups)
    ready = sum(g["ready"] for g in all_groups)
    blocked = [s for g in all_groups for s in g["sources"] if s["status"] == "unavailable"]
    need_key = [s for g in all_groups for s in g["sources"] if s["status"] == "need_key"]

    user_uid = getattr(request.state, "user_id", None)
    user_items = ([catalog.to_dict(s) for s in catalog._user_sources(user_uid)]
                  if user_uid else [])
    total += len(user_items)
    ready += sum(1 for i in user_items
                 if i["status"] not in ("unavailable", "need_key"))
    return {
        "groups": groups,
        "group_by": by,
        # 市场降级成筛选条(`_21` §2)—— 前端拿它渲染 chip。
        # 从注册表算而不是写死,加了新市场不用改两处。
        "markets": [{"value": m.value, "label": catalog.MARKET_LABEL[m]}
                    for m in catalog.MARKET_ORDER],
        "summary": {
            "total": total,
            "ready": ready,
            # 侧栏标题直接显示"数据源 20/32",一眼看出还有多少没解锁
            "headline": f"{ready}/{total}",
            # 分开数是因为这两种"不可用"用户的动作完全不同:
            # need_key 去申请一把 key 就解决,unavailable 做什么都没用
            "need_key_count": len(need_key),
            "unavailable_count": len(blocked),
            "user_added": len(user_items),
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
async def list_toolbox(request: Request,
                       ready_only: bool = Query(False, description="只看当前真能用的")):
    """工具箱清单 · 按 MCP server 分组 + 用户自接的那一组。

    用户原话「mcp 和 tools 算一类」—— 所以这里不分两栏,
    来源差异只体现在每个条目的 `origin` 字段(内置/平台/你接的)。

    这个接口在 middleware 里是公开的(只描述能力,不含凭证),所以
    user_id 可能拿不到 —— 那时只返回内置的,不报错。
    """
    uid = getattr(request.state, "user_id", None)
    groups = tool_catalog.grouped_with_user(uid)
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
