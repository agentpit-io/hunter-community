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
# session_id -> {"repo": str, "at": float, "items": {name: StagedSkill}}
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


def stage(session: str, repo: str, name: str, content: str,
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
        slot = _store.setdefault(session, {"repo": repo, "at": time.time(), "items": {}})
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


def peek(session: str) -> dict:
    """看暂存了什么 —— 确认卡的数据源。

    **返回正文全文**,不截断。`_18` 的原则是「装之前必须让用户看见内容」,
    截断了就看不全。前端自己决定折叠多少。
    """
    with _lock:
        slot = _store.get(session)
        if not slot:
            return {"repo": "", "items": [], "total": 0}
        items = [asdict(v) for v in slot["items"].values()]
    return {
        "repo": slot["repo"],
        "items": items,
        "total": len(items),
        "risk_count": sum(len(i["risks"]) for i in items),
    }


def discard(session: str) -> int:
    with _lock:
        slot = _store.pop(session, None)
    return len(slot["items"]) if slot else 0


def commit(session: str, names: list[str] | None = None) -> dict:
    """落盘 —— **只有这一步真写磁盘**,而它由用户触发,不由模型触发。

    `names` 为空 = 全部;给了就只装这几个(用户在确认卡上勾选)。
    """
    from app.services import skill_files

    with _lock:
        slot = _store.get(session)
        if not slot or not slot["items"]:
            raise ValueError("没有待确认的 SKILL —— 暂存可能已过期(30 分钟)")
        picked = ([v for k, v in slot["items"].items() if k in set(names)]
                  if names else list(slot["items"].values()))

    if names is not None and not picked:
        raise ValueError("勾选的名字都不在暂存里")

    written, failed = [], []
    for s in picked:
        try:
            skill_files.save(s.name, s.content)
            written.append(s.name)
        except Exception as e:                                # noqa: BLE001
            # 一个失败不该让其余的也不装 —— 但**必须报出来**。
            # 悄悄跳过的话用户以为 12 个都装好了,实际只有 9 个
            logger.warning("[skill_stage] 写 {} 失败: {}", s.name, e)
            failed.append({"name": s.name, "error": str(e)[:200]})

    with _lock:
        slot = _store.get(session)
        if slot:
            for n in written:
                slot["items"].pop(n, None)
            if not slot["items"]:
                _store.pop(session, None)

    return {"written": written, "failed": failed, "count": len(written)}
