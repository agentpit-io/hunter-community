"""/chat 能力面板 —— 让用户知道"这东西能干什么"。

为什么不直接把 MCP 工具名列给用户看:
    技术上我们有 5 个 MCP 工具(truesource_get_quote / kronos_kronos_forecast …),
    但用户看到 `truesource_get_quote` 既看不懂、也不知道能拿它干嘛。
    所以这里展示的是**能力卡片** —— 用用户的语言描述用途,点一下把提问模板填进输入框。
    底层是 MCP 工具、opencode SKILL、还是一段提示词,用户不需要知道。

三类数据合成一个列表返回:
    ① 内置能力(skills/ 目录下的 SKILL.md)—— 我们维护,所有人一样
    ② 用户对内置能力的覆盖 —— 改名/改模板/关掉(builtin_key 非空)
    ③ 用户自建能力 —— builtin_key 为空
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services import skill_files
from app.services.database import get_conn

log = logging.getLogger(__name__)
router = APIRouter()

MAX_CUSTOM = 20          # 每人自建上限,防滥用
MAX_TPL_LEN = 500        # 模板长度上限

# 内置能力。key 一旦发布不要改(用户的覆盖记录靠它关联)。
#
# ⚠️ tools 字段只能填**本部署实际存在**的 MCP 工具名。开源版镜像当前有 10 个:
#   watchlist_stock_quickview / _stock_news / _watchlist_digest / _watchlist_add
#   portfolio_portfolio_rebalance / _portfolio_stress / _update_risk_profile
#   uzi_stock_deep_analysis
#   hunter_user_list_my_sources / _invoke
# 2026-08-14 清理过一次:曾有 6 个 SKILL 写着 truesource_* / kronos_* / debate_*,
# 那是**生产环境**的命名被原样抄了过来,开源版根本没有 —— 模型照着调必然失败。
# 校验脚本见 scripts/check_skill_tools.py。
# {股票} 是占位符,前端填入后光标停在这里。
#
# 2026-08-10 扩展：加 brand / source_url / long_desc / tools 4 字段
# 前端 /skills/[key] 详情页读它渲染；SkillManager 列表也可选择显示 brand。
# 有独立品牌来源(Kronos/UZI/TradingAgents)的显示名前缀带品牌 " · " 分隔。
# ⚠️ BUILTINS 已于 2026-08-15 迁移到 skills/ 目录下的标准 SKILL.md 文件
# (_14 §6 Step A)。**文件是唯一事实来源**,改 SKILL 请改文件,不要在这里加 dict。
#
# 为什么改:标准格式(Anthropic Agent Skills / opencode SkillV2)= 网上下载的
# skill 原样丢进目录就能用;用户自建与下载来的走同一条路,不用维护两套逻辑。
#
# 加载器见 app/services/skill_files.py · 生成脚本见 scripts/migrate_skills_to_files.py
def _builtins() -> list[dict]:
    return skill_files.load_all()


# 分类展示顺序 · 与 skill_files 保持同一份(那边是加载器的默认值,这里是接口出参)
CATEGORY_ORDER = skill_files.CATEGORY_ORDER


_BUILTIN_BY_KEY = {b["key"]: b for b in _builtins()}

_BUILTIN_KEYS = {b["key"] for b in _builtins()}


def _uid(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, "需要登录")
    return str(uid)


def _rows(user_id: str) -> list[dict]:
    c = get_conn(); cur = c.cursor()
    cur.execute("""SELECT id, name, icon, prompt_tpl, enabled, sort_order, builtin_key
                   FROM chat_user_skill WHERE user_id = %s
                   ORDER BY sort_order, id""", (user_id,))
    out = [{"id": r[0], "name": r[1], "icon": r[2], "prompt_tpl": r[3],
            "enabled": r[4], "sort_order": r[5], "builtin_key": r[6]}
           for r in cur.fetchall()]
    c.close()
    return out


@router.get("/chat/skills")
async def list_skills(request: Request):
    """合成后的能力列表(内置 + 用户覆盖 + 自建),按 sort_order 排。"""
    uid = _uid(request)
    try:
        rows = _rows(uid)
    except Exception as e:
        log.warning("[chat] 读能力列表失败: %s", e)
        rows = []

    override = {r["builtin_key"]: r for r in rows if r["builtin_key"]}
    custom = [r for r in rows if not r["builtin_key"]]

    items: list[dict] = []
    for i, b in enumerate(_builtins()):
        ov = override.get(b["key"])
        items.append({
            "key": b["key"],
            # 别写死 True —— 用户放进 user-skills/ 的 SKILL 也走这个循环,
            # 加载器已经按来源目录标好了(内置目录=True · 用户目录=False)。
            "builtin": b.get("builtin", True),
            "icon": (ov or {}).get("icon") or b["icon"],
            "name": (ov or {}).get("name") or b["name"],
            "prompt_tpl": (ov or {}).get("prompt_tpl") or b["prompt_tpl"],
            "hint": b["hint"],
            "brand": b.get("brand", ""),
            "source_url": b.get("source_url", ""),
            "category": b.get("category", "其他"),
            "enabled": ov["enabled"] if ov else True,
            "sort_order": ov["sort_order"] if ov else i,
            "id": ov["id"] if ov else None,
        })
    for r in custom:
        items.append({
            "key": f"custom:{r['id']}", "builtin": False,
            "icon": r["icon"], "name": r["name"], "prompt_tpl": r["prompt_tpl"],
            "hint": "", "brand": "", "source_url": "", "category": "我的能力",
            "enabled": r["enabled"],
            "sort_order": r["sort_order"] or (100 + r["id"]), "id": r["id"],
        })

    items.sort(key=lambda x: (x["sort_order"], x["name"]))
    return {
        "items": items,
        "custom_count": len(custom),
        "max_custom": MAX_CUSTOM,
        "category_order": CATEGORY_ORDER,
    }


@router.get("/chat/skills/detail/{key}")
async def get_skill_detail(key: str):
    """内置能力详情（公开无需登录 · 供 /skills/[key] 页 SSR/CSR 读）。

    自定义能力属于用户私有 · 不在这里公开 · 前端不做详情页入口。
    """
    b = _BUILTIN_BY_KEY.get(key)
    if not b:
        raise HTTPException(404, "能力不存在或非公开")
    return {
        "key": b["key"],
        "icon": b["icon"],
        "name": b["name"],
        "brand": b.get("brand", ""),
        "category": b.get("category", ""),
        "source_url": b.get("source_url", ""),
        "prompt_tpl": b["prompt_tpl"],
        "hint": b["hint"],
        "long_desc": b.get("long_desc", ""),
        "tools": b.get("tools", []),
    }


class SkillIn(BaseModel):
    name: str
    icon: str = "⭐"
    prompt_tpl: str


def _validate(name: str, tpl: str) -> tuple[str, str]:
    name = (name or "").strip()
    tpl = (tpl or "").strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    if not tpl:
        raise HTTPException(400, "提问模板不能为空")
    if len(name) > 20:
        raise HTTPException(400, "名称最多 20 字")
    if len(tpl) > MAX_TPL_LEN:
        raise HTTPException(400, f"模板最多 {MAX_TPL_LEN} 字")
    return name, tpl


@router.post("/chat/skills")
async def create_skill(body: SkillIn, request: Request):
    """新建自定义能力。"""
    uid = _uid(request)
    name, tpl = _validate(body.name, body.prompt_tpl)
    if len([r for r in _rows(uid) if not r["builtin_key"]]) >= MAX_CUSTOM:
        raise HTTPException(400, f"最多创建 {MAX_CUSTOM} 个自定义能力,请先删除不用的")
    c = get_conn(); cur = c.cursor()
    cur.execute("""INSERT INTO chat_user_skill (user_id, name, icon, prompt_tpl, sort_order)
                   VALUES (%s,%s,%s,%s, COALESCE(
                     (SELECT max(sort_order)+1 FROM chat_user_skill WHERE user_id=%s), 100))
                   RETURNING id""",
                (uid, name, (body.icon or "⭐")[:4], tpl, uid))
    new_id = cur.fetchone()[0]
    c.commit(); c.close()
    return {"ok": True, "id": new_id}


class SkillPatch(BaseModel):
    name: str | None = None
    icon: str | None = None
    prompt_tpl: str | None = None
    enabled: bool | None = None
    sort_order: int | None = None


@router.patch("/chat/skills/{key}")
async def update_skill(key: str, body: SkillPatch, request: Request):
    """改能力。key 为内置 key(quote/forecast…)或 custom:{id}。

    内置能力没有行时先补一条覆盖记录 —— 用户第一次关掉/改写内置项时走这里。
    """
    uid = _uid(request)
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        return {"ok": True}
    if "name" in patch or "prompt_tpl" in patch:
        n, t = _validate(patch.get("name") or "占位", patch.get("prompt_tpl") or "占位")
        if "name" in patch:
            patch["name"] = n
        if "prompt_tpl" in patch:
            patch["prompt_tpl"] = t
    if "icon" in patch:
        patch["icon"] = patch["icon"][:4]

    c = get_conn(); cur = c.cursor()
    if key.startswith("custom:"):
        try:
            rid = int(key.split(":", 1)[1])
        except ValueError:
            raise HTTPException(400, "无效的能力 id")
        sets = ", ".join(f"{k} = %s" for k in patch)
        cur.execute(f"""UPDATE chat_user_skill SET {sets}, updated_at = NOW()
                        WHERE id = %s AND user_id = %s""",
                    list(patch.values()) + [rid, uid])
        n = cur.rowcount
        c.commit(); c.close()
        if not n:
            raise HTTPException(404, "能力不存在")
        return {"ok": True}

    if key not in _BUILTIN_KEYS:
        c.close()
        raise HTTPException(404, "能力不存在")

    base = next(b for b in _builtins() if b["key"] == key)
    cur.execute("""INSERT INTO chat_user_skill
                     (user_id, name, icon, prompt_tpl, enabled, sort_order, builtin_key)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (user_id, builtin_key) WHERE builtin_key <> ''
                   DO NOTHING""",
                (uid, base["name"], base["icon"], base["prompt_tpl"], True,
                 _builtins().index(base), key))
    sets = ", ".join(f"{k} = %s" for k in patch)
    cur.execute(f"""UPDATE chat_user_skill SET {sets}, updated_at = NOW()
                    WHERE user_id = %s AND builtin_key = %s""",
                list(patch.values()) + [uid, key])
    c.commit(); c.close()
    return {"ok": True}


@router.delete("/chat/skills/{key}")
async def delete_skill(key: str, request: Request):
    """删自定义能力。内置能力不能删,只能关(PATCH enabled=false)。"""
    uid = _uid(request)
    if not key.startswith("custom:"):
        raise HTTPException(400, "内置能力不能删除,可在管理里关闭")
    try:
        rid = int(key.split(":", 1)[1])
    except ValueError:
        raise HTTPException(400, "无效的能力 id")
    c = get_conn(); cur = c.cursor()
    cur.execute("DELETE FROM chat_user_skill WHERE id = %s AND user_id = %s", (rid, uid))
    n = cur.rowcount
    c.commit(); c.close()
    if not n:
        raise HTTPException(404, "能力不存在")
    return {"ok": True}


@router.post("/chat/skills/reset")
async def reset_skills(request: Request):
    """恢复内置能力的默认状态(只删覆盖记录,自建的保留)。"""
    uid = _uid(request)
    c = get_conn(); cur = c.cursor()
    cur.execute("DELETE FROM chat_user_skill WHERE user_id = %s AND builtin_key <> ''", (uid,))
    n = cur.rowcount
    c.commit(); c.close()
    return {"ok": True, "reset": n}
