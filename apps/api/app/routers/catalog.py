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
