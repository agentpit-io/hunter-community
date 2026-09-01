"""SKILL 暂存区 —— 模型写这里,用户确认才落盘(`_23` §2.5)。

## 为什么要有暂存这一层

用户拍板「把作者的说明直接喂给模型,让它按 README 办」。于是流程变成:
模型读 INSTALL.md → 自己决定装哪几个 → 逐个写。

如果模型直接写进 `user-skills/`,就会出现这些情况:

  · 装到一半模型改主意了 —— 磁盘上留下半套
  · 它装了 12 个,用户其实只想要 3 个 —— 已经落盘了,得手工删
  · 用户看到确认卡时,东西**已经生效了**,"确认"变成了走过场

暂存区把"模型编排"和"落盘"切开:模型爱写多少写多少,都在内存里;
用户看完整体再决定要不要。

## 为什么按 user_id 存,不按会话

第一版让模型传 `session`。实测发现:**模型根本不知道 opencode 的 session id** ——
它只能编一个(比如 "uzi-install")。而前端按自己知道的 session 查,两边对不上,
于是"暂存成功了、确认卡永远不弹",而模型那边一切正常。

改成按 `user_id`(X-Hunter-User-Id,plugin 已经在注入)。两边都知道这个值,
不需要任何一方去猜。代价是同一用户同时装两个仓库会混在一起 ——
但那不是真实场景,而"让模型填一个它不知道的 ID"是必然出错的设计。

## 为什么在内存而不是临时目录

暂存的内容随时可能被丢弃,而且**一个会话一份**。落到磁盘就要考虑
清理、并发、重启残留 —— 而这些内容的生命周期只有"用户看一眼"那么长。

代价是 api 重启会丢暂存。可以接受:重启时用户手上那张确认卡本来也失效了。

## 边界(工具的定义域,不是对模型的不信任)

  · 名字走 `skill_files.validate_name()` —— 它直接进文件系统路径,
    而内容来自网络。这条 `_18` 已经在做
  · 只收 `.md` 内容,不收可执行文件。`_18` §3.5 定的"代码永不安装"
    不因为"作者说要装"而改变
  · 单个会话最多 40 条 —— 防止模型陷在循环里把内存写爆
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field

from loguru import logger

# 一个会话最多暂存这么多。40 是按 UZI-Skill 那种大仓库(12 个 SKILL)
# 留了三倍余量 —— 超过多半是模型循环了,不是仓库真有那么多
MAX_STAGED = 40

# 暂存区存活时间。超过就当用户已经走开了
TTL_SEC = 30 * 60

# 给模型看的 hunter: 段样板。
# 别人仓库的 SKILL 基本都没有这一段(它是我们的扩展),而**缺了它
# 用户在能力列表里点不动那个 SKILL** —— 装完看得见、用不了,
# 而他不知道为什么。所以缺失时把样板原样回给模型让它照填。
_TPL_TEMPLATE = """
hunter:
  display_name: "给人看的名字"
  icon: "⭐"
  category: "快速判断|综合分析|投研报告|估值建模|事件与筛选|组合级|尽调风控"
  prompt_tpl: "用它分析 {股票}"
"""


@dataclass
class StagedSkill:
    name: str
    content: str
    source_path: str = ""        # 来自仓库的哪个文件 · 空 = 模型自己写的
    note: str = ""               # 模型为什么装它(给用户看)
    risks: list = field(default_factory=list)
    lines: int = 0
    # 暂存的这份还残留着对作者仓库的依赖吗(`_23` 实测发现)。
    # UZI-Skill 的 5 个 SKILL.md 里 4 个写着"读 .cache/xxx.json""跑 scripts/fetch_lhb.py"——
    # 模型该把这些改写成调我们的工具。**没改干净的必须在确认卡上标出来**,
    # 否则用户装进去,用的时候模型会去跑不存在的脚本,而他不知道为什么
    coupling: list = field(default_factory=list)


_lock = threading.Lock()
# user_id -> {"repo": str, "at": float, "items": {name: StagedSkill}}
_store: dict[str, dict] = {}


def _gc() -> None:
    """清掉过期的。**在每次写入时顺手做** —— 单独起个定时任务不值得,
    而不清理会让一个长期运行的进程慢慢攒满废弃暂存。"""
    now = time.time()
    dead = [k for k, v in _store.items() if now - v["at"] > TTL_SEC]
    for k in dead:
        _store.pop(k, None)
    if dead:
        logger.debug("[skill_stage] 清理 {} 个过期暂存", len(dead))


def stage(user: str, repo: str, name: str, content: str,
          source_path: str = "", note: str = "") -> dict:
    """暂存一个 SKILL。同名覆盖 —— 模型可能先写一版再改。"""
    from app.services import skill_files
    from app.services.skill_install import portability, scan_risks

    # 名字直接进文件系统路径,而内容来自网络 —— 这条校验不能省
    clean = skill_files.validate_name((name or "").strip())

    body = (content or "").strip()
    if not body:
        raise ValueError(f"{clean}: 内容是空的")
    if len(body) > 200_000:
        raise ValueError(f"{clean}: 内容超过 200KB —— SKILL 是给模型读的方法论,"
                         f"不该有这么大")

    with _lock:
        _gc()
        slot = _store.setdefault(user, {"repo": repo, "at": time.time(), "items": {}})
        slot["at"] = time.time()
        slot["repo"] = repo or slot.get("repo", "")
        if clean not in slot["items"] and len(slot["items"]) >= MAX_STAGED:
            raise ValueError(
                f"暂存已满({MAX_STAGED} 个)—— 一次装这么多多半不是本意,"
                f"先确认这批,再装下一批")
        port = portability(body)
        slot["items"][clean] = StagedSkill(
            name=clean, content=body, source_path=source_path, note=note,
            risks=scan_risks(body), lines=len(body.splitlines()),
            coupling=port["coupling"],
        )
        n = len(slot["items"])

    out = {"staged": clean, "total": n,
           "hint": f"已暂存 {n} 个 · 尚未写入磁盘,等用户确认"}

    # 没有 hunter.prompt_tpl 的 SKILL **在能力列表里点不动** ——
    # 用户装完看得见但用不了,而他不知道为什么。
    # 外来仓库的 frontmatter 基本都没有 hunter: 段,所以这条几乎必然触发。
    # 与 coupling 警告同一套路:回给模型让它补,实测它会照改
    if "prompt_tpl" not in body:
        out["warning_tpl"] = (
            f"{clean} 缺 hunter.prompt_tpl —— 装进去用户在能力列表里**点不动它**。"
            "请在 frontmatter 顶部补一段(注意 category 只能是那 7 个之一):"
            + _TPL_TEMPLATE
            + "补完同名再 stage 一次(会覆盖)。"
        )
    if port["coupling"]:
        # **回给模型一句提醒,而不是默默收下。**
        # 它可能以为自己改干净了,实际还留着 "读 .cache/xxx.json"。
        # 不提醒的话这份就带着残留进了确认卡,用户看不出问题
        out["warning"] = (
            f"{clean} 里还有 {len(port['coupling'])} 处对作者仓库的依赖"
            f"({'、'.join(c['why'] for c in port['coupling'][:3])})—— "
            f"这些文件在本系统不存在。要么改写成调我们的工具再暂存一次(同名会覆盖),"
            f"要么在 note 里说清楚这份需要用户自行部署原仓库才能完整工作。"
        )
    return out


# ═══════════════════════════════════════════════════════════════
# 仓库清单 —— 「装了什么 / 跳过了什么」靠它算,不靠模型自觉汇报
# ═══════════════════════════════════════════════════════════════

# user_id -> {"repo": str, "at": float, "found": [SKILL.md 路径]}
_inventory: dict[str, dict] = {}


def remember_inventory(user: str, repo: str, skill_paths: list[str]) -> None:
    """记下这个仓库里**一共有哪些 SKILL** —— 在 repo_open 时调用。

    ## 为什么需要

    用户说「按这个地址装这个 SKILL」,模型自己决定装哪几个。实测它会
    自作主张:读到 fundamental-analysis 之后说一句「这个已经合并到
    stock-eval 里了,我们换一个更有价值的 technical-analysis」,然后
    只暂存了一个。

    用户从界面上**完全看不出**仓库里原本有 5 个、装了 1 个、跳过 4 个 ——
    确认卡只显示"待确认 1 个"。他以为整个仓库都装好了,
    过一会儿在能力库里找不到 fundamental-analysis,以为是我们的 bug。

    ## 为什么不让模型自己报

    最直接的做法是加个"请汇报你跳过了什么"的工具或 prompt 规则。
    但**跳过恰恰是模型自己的判断**,让它汇报自己的省略,等于让它
    先意识到自己省略了 —— 它当时的想法是"我在做一个更好的选择",
    不是"我跳过了"。这条路不可靠。

    repo_open 的返回里本来就有 `skill_md_paths`(仓库全量清单),
    记下来,peek 时和暂存列表一比,差集就是没装的。**不需要模型配合。**
    """
    if not user or not skill_paths:
        return
    with _lock:
        _gc_inventory()
        _inventory[user] = {
            "repo": repo or "", "at": time.time(),
            "found": list(skill_paths)[:200],
        }


def _gc_inventory() -> None:
    now = time.time()
    for k in [k for k, v in _inventory.items() if now - v["at"] > TTL_SEC]:
        _inventory.pop(k, None)


def _skipped(user: str, staged: list[dict]) -> dict:
    """算出「发现 N 个 · 这次装 M 个 · 剩下哪几个没装」。

    ## 匹配为什么不能只比名字

    模型暂存时经常改名:仓库里叫 `stock-eval`,它落盘成 `invest_stock_eval`
    (加了个来源前缀)。只比名字的话这条会被误判成"没装",
    确认卡上就会出现一条假的"跳过" —— 比不显示还糟。

    三级匹配,命中任一即认为已装:
      ① source_path 完全相同   —— 模型填了就用这个,最准
      ② 目录名归一后相同       —— `stock-eval` ↔ `stock_eval`
      ③ 暂存名包含目录名       —— `invest_stock_eval` 含 `stock_eval`

    宁可漏报(该提示的没提示)也不误报:用户看到一条"没装 xxx"然后
    发现其实装了,下次就不信这个提示了。
    """
    with _lock:
        inv = _inventory.get(user)
    if not inv:
        return {}
    found = inv["found"]

    def _leaf(path: str) -> str:
        # plugins/us-stock-analysis/skills/fundamental-analysis/SKILL.md
        #   → fundamental-analysis
        parts = [x for x in path.split("/") if x and x != "SKILL.md"]
        return parts[-1] if parts else path

    def _norm(x: str) -> str:
        return x.replace("-", "_").replace(".", "_").lower()

    staged_paths = {(i.get("source_path") or "").strip() for i in staged}
    staged_norm = {_norm(i["name"]) for i in staged}

    def _is_staged(path: str) -> bool:
        if path in staged_paths:
            return True
        leaf = _norm(_leaf(path))
        if leaf in staged_norm:
            return True
        return any(leaf and leaf in n for n in staged_norm)

    skipped = [{"name": _leaf(p), "path": p} for p in found if not _is_staged(p)]
    return {
        "repo": inv["repo"],
        "found_count": len(found),
        "staged_count": len(staged),
        "skipped": skipped[:30],
        "skipped_count": len(skipped),
    }


def peek(user: str) -> dict:
    """看暂存了什么 —— 确认卡的数据源。

    **返回正文全文**,不截断。`_18` 的原则是「装之前必须让用户看见内容」,
    截断了就看不全。前端自己决定折叠多少。
    """
    with _lock:
        slot = _store.get(user)
        if not slot:
            return {"repo": "", "items": [], "total": 0}
        items = [asdict(v) for v in slot["items"].values()]
    out = {
        "repo": slot["repo"],
        "items": items,
        "total": len(items),
        "risk_count": sum(len(i["risks"]) for i in items),
    }
    # 仓库里还有哪些没被暂存 —— 确认卡要把这个显式告诉用户,
    # 否则他以为整个仓库都装好了(见 remember_inventory 的说明)
    inv = _skipped(user, items)
    if inv:
        out["inventory"] = inv
    return out


def discard(user: str) -> int:
    with _lock:
        slot = _store.pop(user, None)
    return len(slot["items"]) if slot else 0


def commit(user: str, names: list[str] | None = None) -> dict:
    """落盘 —— **只有这一步真写磁盘**,而它由用户触发,不由模型触发。

    `names` 为空 = 全部;给了就只装这几个(用户在确认卡上勾选)。
    """
    from app.services import skill_files

    with _lock:
        slot = _store.get(user)
        if not slot or not slot["items"]:
            raise ValueError("没有待确认的 SKILL —— 暂存可能已过期(30 分钟)")
        picked = ([v for k, v in slot["items"].items() if k in set(names)]
                  if names else list(slot["items"].values()))

    if names is not None and not picked:
        raise ValueError("勾选的名字都不在暂存里")

    written, failed = [], []
    for s in picked:
        try:
            # 用 save_raw 不用 save:导入的是**完整原文**,
            # 拆开重拼会丢作者自定义的 frontmatter 字段
            # 带上来源仓库 —— 能力页按来源分组要靠它。
            # `install()` 那条路早就在写 origin,这条路一直没写,
            # 结果同样是"从 GitHub 装的",一半有来源一半没有。
            skill_files.save_raw(
                s.name, s.content,
                origin=("github:" + slot["repo"]) if slot.get("repo") else "")
            written.append(s.name)
        except Exception as e:                                # noqa: BLE001
            # 一个失败不该让其余的也不装 —— 但**必须报出来**。
            # 悄悄跳过的话用户以为 12 个都装好了,实际只有 9 个
            logger.warning("[skill_stage] 写 {} 失败: {}", s.name, e)
            failed.append({"name": s.name, "error": str(e)[:200]})

    with _lock:
        slot = _store.get(user)
        if slot:
            for n in written:
                slot["items"].pop(n, None)
            if not slot["items"]:
                _store.pop(user, None)

    return {"written": written, "failed": failed, "count": len(written)}
