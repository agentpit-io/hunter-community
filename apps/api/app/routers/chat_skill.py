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
import json
import logging
import os
import re
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services import opencode_admin, skill_files, skill_install
from app.services.database import get_conn

log = logging.getLogger(__name__)
router = APIRouter()

# 每人自定义能力上限。
#
# ⚠️ 原来是 20 —— 那是「用户在表单里一个一个手写」年代定的防滥用数字。
# 而 `_24` §5 的推荐位是**一次装一整个仓**:光我们自己推荐的 5 个仓
# 就有 23 个 SKILL(cc-equity-research 9 + agent-skills 7 + UZI 5 + 2),
# **按推荐清单全装一遍必然超**。实测用户装到第 3 个仓就被拦下:
#     「装 algoderiv/agent-skills 失败:最多 20 个自定义能力」
#
# 真正的约束不是磁盘,是**模型的上下文** —— opencode 会把每个 SKILL 的
# 名字与描述注入系统提示,装太多会挤掉真正有用的东西。100 是个宽松但
# 仍有意义的数字;真要更多可以用 env 调,但调之前想清楚上面这句。
MAX_CUSTOM = int(os.getenv("HUNTER_MAX_CUSTOM_SKILLS", "100"))
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
    """自建能力的表单。

    前四个是老字段(旧版 UI 只发这些,保持兼容);后面几个是改写文件之后
    才有意义的 —— 有了它们,用户建的才是**带方法论、能声明依赖的 SKILL**,
    而不只是一个带图标的提示词快捷方式(见 `_19` §5.2)。
    """
    name: str
    icon: str = "⭐"
    prompt_tpl: str
    # 目录名(英文)· 不给就从 display_name 生成
    slug: str | None = None
    description: str | None = None
    category: str | None = None
    needs_tools: list[str] = []
    needs_data: list[str] = []
    body: str | None = None          # Markdown 方法论正文


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


def _slugify(display: str, given: str | None) -> str:
    """给定就用给定的;否则从显示名生成一个安全的目录名。

    显示名多半是中文,音译不现实,所以中文场景直接落到时间戳兜底 ——
    目录名对用户不可见,可读性让位于**一定能生成合法值**。
    """
    if given:
        return given.strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", (display or "").strip().lower()).strip("_")
    if s and s[0].isdigit():
        s = "s_" + s
    return s or f"skill_{int(time.time())}"


def _after_write() -> dict:
    """写完文件之后让 opencode 重扫,并把结果如实带给前端。

    **不许假装成功**:文件写好了但 opencode 没重扫,表现是"侧栏有了、
    模型说没有",用户完全无从判断。旧镜像上刷新端点是 404,那时要明说需要重启。
    """
    r = opencode_admin.refresh_skills()
    if r.get("ok"):
        return {"synced": True, "skill_count": r.get("count")}
    return {"synced": False, "needs_restart": True,
            "message": opencode_admin.restart_hint(), "reason": r.get("reason", "")}


@router.post("/chat/skills")
async def create_skill(body: SkillIn, request: Request):
    """新建自定义能力 —— **写文件,不写数据库**。

    改成写 `user-skills/{slug}/SKILL.md` 之后,「UI 里建的」「手动放进目录的」
    「从 GitHub 装的」是同一个东西,一套加载逻辑(`_19` §5.2)。
    """
    _uid(request)                       # 仅做鉴权
    name, tpl = _validate(body.name, body.prompt_tpl)
    existing = [s for s in _builtins() if not s.get("builtin", True)]
    if len(existing) >= MAX_CUSTOM:
        raise HTTPException(
            400, f"已有 {len(existing)} 个自定义能力,上限 {MAX_CUSTOM} —— "
                 f"删掉不用的,或调 HUNTER_MAX_CUSTOM_SKILLS")

    slug = _slugify(name, body.slug)
    if any(s["key"] == slug for s in _builtins() if s.get("builtin", True)):
        raise HTTPException(400, f"{slug} 与内置能力重名 —— 换个名字,"
                                 f"或到「管理」里直接改那个内置能力")
    try:
        skill_files.save({
            "name": slug, "display_name": name, "icon": (body.icon or "⭐")[:4],
            "description": body.description or "", "category": body.category or "其他",
            "prompt_tpl": tpl, "needs_tools": body.needs_tools,
            "needs_data": body.needs_data, "origin": "ui",
        }, body.body or "")
    except skill_files.SkillWriteError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "key": slug, **_after_write()}


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
    """删自定义能力。内置能力不能删,只能关(PATCH enabled=false)。

    自建的现在是 `user-skills/{key}/` 目录,删目录即可。
    **只动 user-skills/**,内置目录碰都不碰 —— 这是"恢复默认永远不会坏"的前提。
    """
    uid = _uid(request)

    # 老路径:custom:{id} 是改成写文件之前存在数据库里的,留着能删干净
    if key.startswith("custom:"):
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

    if not skill_files.is_user_skill(key):
        raise HTTPException(400, "内置能力不能删除,可在管理里关闭")
    if not skill_files.delete(key):
        raise HTTPException(404, "能力不存在")
    return {"ok": True, **_after_write()}


@router.post("/chat/skills/reset")
async def reset_skills(request: Request):
    """恢复内置能力的默认状态(只删覆盖记录,自建的保留)。"""
    uid = _uid(request)
    c = get_conn(); cur = c.cursor()
    cur.execute("DELETE FROM chat_user_skill WHERE user_id = %s AND builtin_key <> ''", (uid,))
    n = cur.rowcount
    c.commit(); c.close()
    return {"ok": True, "reset": n}


# ══════════════════════════════════════════════════════════════
# 从 GitHub 装 SKILL(_18)
#
# **两步**,不是一步:先 inspect(不下载,只读目录树)让用户看清楚要装什么、
# 会丢什么、有没有可疑内容;确认之后才 install。
#
# 一步装完看着更顺,但那等于让用户闭着眼睛把陌生人的提示词注入自己的
# 模型上下文 —— SKILL.md 正文是直接进上下文的。
# ══════════════════════════════════════════════════════════════

class RepoIn(BaseModel):
    repo: str                       # 完整 URL / owner/repo / owner/repo@分支


class InstallIn(BaseModel):
    repo: str
    paths: list[str]                # inspect 返回的 candidate.path


@router.post("/chat/skills/inspect")
async def inspect_repo(body: RepoIn, request: Request):
    """探测 GitHub 仓库 —— **不下载内容**,只读目录树与候选 SKILL.md 的头部。"""
    _uid(request)
    try:
        return skill_install.inspect(body.repo)
    except skill_install.InstallError as e:
        raise HTTPException(400, str(e))


@router.post("/chat/skills/install")
async def install_from_repo(body: InstallIn, request: Request):
    """按 inspect 的结果装选中的 skill。可执行文件一律跳过。"""
    _uid(request)
    existing = [s for s in _builtins() if not s.get("builtin", True)]
    if len(existing) + len(body.paths) > MAX_CUSTOM:
        # 说清楚**差多少** —— 只说"超了"用户不知道该删几个,
        # 也不知道是不是自己勾多了
        over = len(existing) + len(body.paths) - MAX_CUSTOM
        raise HTTPException(
            400,
            f"你已有 {len(existing)} 个能力,这个仓要装 {len(body.paths)} 个,"
            f"合计超出上限 {MAX_CUSTOM} 共 {over} 个 —— "
            f"少勾 {over} 个,或先删掉不用的")
    try:
        installed = skill_install.install(body.repo, body.paths)
    except skill_install.InstallError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "installed": installed, **_after_write()}


# ══════════════════════════════════════════════════════════════
# `_23` · 暂存区的确认与落盘
#
# 模型通过 /api/internal/cap/skill/stage 往暂存区写(内存),
# 用户在确认卡上看完,点确认走这里 —— **落盘只发生在这一步,由用户触发**。
# ══════════════════════════════════════════════════════════════

class StagedCommitIn(BaseModel):
    # 空 = 全装。给了就只装勾选的这几个
    names: list[str] | None = None


# ══════════════════════════════════════════════════════════════
# `_24` §5 · 推荐安装
#
# 老板:「给一个入口就是推荐加的 skill,也不写我们的,你可以去看看
#        github 有哪些 star 多的,然后点一下就能加上,
#        **这个 skill 算是用户自己加的**」
#
# 最后半句是关键:推荐 ≠ 内置。装完之后它出现在「你装的」组里,
# 和用户自己贴 URL 装的没有任何区别 —— 我们只是省掉他找和贴的过程。
# ══════════════════════════════════════════════════════════════

_REC_PATH = os.getenv("HUNTER_RECOMMEND_FILE", "/opt/hunter-data/recommended-skills.json")


@router.get("/chat/skills/recommended")
async def list_recommended(request: Request):
    """推荐清单 —— **零 GitHub API 调用**。

    star 数和可移植性都是文件里的实测快照。为什么不实时查:
    GitHub 未登录 API 是 60 次/小时,开发过程中就撞到了限流。
    渲染时才去查的话,限流那一刻整个推荐位是空的,
    而用户看到的是「这个功能坏了」。只有真的点安装才访问 GitHub。

    **免登录可访问** —— 用户在决定要不要用这个开源版时,
    「它能装哪些现成能力」是个先决问题,不该拦在登录后面。
    """
    try:
        with open(_REC_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        # 清单缺失不是错误 —— 用户可能自己删了(它就是给人改的)。
        # 返回空列表让页面显示"还没有推荐",而不是一个 500
        return {"items": [], "checked_at": "", "missing": True}
    except Exception as e:                                    # noqa: BLE001
        raise HTTPException(500, f"推荐清单读不出来:{e}")

    items = [i for i in (data.get("items") or []) if i.get("repo")]

    # 标出已经装过的 —— 装过的按钮要变成"已安装",否则用户会重复点,
    # 而重复安装的表现是一堆同名 SKILL 覆盖来覆盖去
    # origin 形如 `github:owner/repo@main` —— 取中间那段和 repo 比。
    #
    # ⚠️ **`_23` 之前装的那批没有 origin**(暂存路径当时没记来源),
    # 所以它们会显示成"未安装"。这是已知的、不打算追溯修的:
    # 重复装一次的代价是覆盖同名文件,而误判成"已装"会让用户
    # 以为能力已经有了 —— 后者糟得多。
    try:
        installed = set()
        for sk in skill_files.load_all():
            if sk.get("builtin", True):
                continue
            o = (sk.get("origin") or "").strip().lower()
            if o.startswith("github:"):
                installed.add(o[7:].split("@")[0])
        for i in items:
            i["installed"] = i["repo"].lower() in installed
    except Exception:                                          # noqa: BLE001
        for i in items:
            i["installed"] = False

    # ── 容量自查 ────────────────────────────────────────────
    #
    # 2026-08-21 踩过:推荐清单合计 23 个 SKILL,而 MAX_CUSTOM 当时是 20 ——
    # **按我们自己的推荐清单全装一遍必然超**,而做推荐位时没回头看这个常量。
    # 用户装到第 3 个仓才被拦下,报的是一句干巴巴的"最多 20 个"。
    #
    # 这里把两个数摆在一起返回,并在清单本身就装不下时打一条 ERROR ——
    # 让"推荐了却装不下"这件事在**我们改清单的时候**就暴露,
    # 而不是等用户装到一半。
    need = sum(int(i.get("total") or 0) for i in items)
    try:
        installed_n = len([x for x in skill_files.load_all()
                           if not x.get("builtin", True)])
    except Exception:                                          # noqa: BLE001
        installed_n = 0
    if need > MAX_CUSTOM:
        log.error("[recommended] 推荐清单合计 %d 个 SKILL,超过上限 %d —— "
                  "用户按清单全装会被拦下。改大 MAX_CUSTOM 或精简清单",
                  need, MAX_CUSTOM)

    return {
        "items": items,
        "checked_at": data.get("checked_at", ""),
        "note": data.get("note", ""),
        # 前端据此提示"全装需要 N 个位置,你还剩 M 个"
        "capacity": {
            "installed": installed_n,
            "max": MAX_CUSTOM,
            "remaining": max(0, MAX_CUSTOM - installed_n),
            "need_for_all": need,
            "fits": need + installed_n <= MAX_CUSTOM,
        },
        # 被否掉的也返回 —— 前端折叠显示。
        # 「为什么不推荐 30500 star 那个」是用户会问的,答案写在这里
        "rejected": data.get("rejected") or [],
    }


@router.get("/chat/skills/staged")
async def get_staged(request: Request):
    """确认卡的数据源 —— **返回正文全文,不截断**。

    `_18` 的原则是「装之前必须让用户看见内容」。这里截断了就看不全,
    前端自己决定折叠多少。
    """
    from app.services import skill_stage
    return skill_stage.peek(_uid(request))


@router.post("/chat/skills/staged/commit")
async def commit_staged(body: StagedCommitIn, request: Request):
    """用户确认 → 落盘。"""
    uid = _uid(request)
    from app.services import skill_stage

    existing = [s for s in _builtins() if not s.get("builtin", True)]
    got = skill_stage.peek(uid)
    want = len(body.names) if body.names else got["total"]
    if len(existing) + want > MAX_CUSTOM:
        raise HTTPException(
            400,
            f"最多 {MAX_CUSTOM} 个自定义能力(现有 {len(existing)},这批 {want})· "
            f"可以在确认卡上只勾选需要的几个,或先删掉不用的")
    try:
        res = skill_stage.commit(uid, body.names)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # _after_write() 会调 opencode 的 /skill/refresh(我们自己加的端点)——
    # 不刷的话文件写好了但模型看不到,而"已保存"的提示会让用户以为能用了
    return {"ok": True, **res, **_after_write()}


@router.post("/chat/skills/staged/discard")
async def discard_staged(body: StagedCommitIn, request: Request):
    from app.services import skill_stage
    return {"discarded": skill_stage.discard(_uid(request))}
