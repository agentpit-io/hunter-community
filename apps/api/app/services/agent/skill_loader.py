"""Skill 加载器（100% 兼容 opencode/anomalyco 的 SKILL.md 格式）

从 4 个路径合并加载（优先级降序）：
  1. .hunter/skills/*/SKILL.md    (项目内 · 首选)
  2. .opencode/skills/*/SKILL.md  (opencode 兼容路径)
  3. ~/.config/hunter/skills/     (用户级)
  4. ~/.opencode/skills/          (opencode 用户级)

启动时全量加载到内存；orchestrator 在 system prompt 里注入 skills manifest
(name + description)，用户问题命中时通过 `skill({name})` tool 拉全文。
"""
from __future__ import annotations
import os
import re
from pathlib import Path
from typing import Optional
from loguru import logger


# ─────────────────────────── 加载路径 ───────────────────────────
def _search_paths() -> list[Path]:
    # HUNTER_SKILL_ROOT 环境变量可覆盖首选路径（测试用）
    override = os.getenv("HUNTER_SKILL_ROOT")
    if override:
        return [Path(override)]
    # 从当前进程 cwd 找项目根（api/ 或 hermes-1/）
    cwd = Path.cwd()
    candidates = []
    for root in [cwd, cwd.parent, cwd.parent.parent]:
        candidates.append(root / ".hunter" / "skills")
        candidates.append(root / ".opencode" / "skills")
    candidates.append(Path.home() / ".config" / "hunter" / "skills")
    candidates.append(Path.home() / ".opencode" / "skills")
    return candidates


# ─────────────────────────── frontmatter 解析 ───────────────────────────
_FM_RE = re.compile(
    r"^---\s*\n(?P<fm>.*?)\n---\s*\n(?P<body>.*)$",
    re.DOTALL,
)


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """返回 (fm dict, body)。若无 frontmatter，fm 为空 dict"""
    m = _FM_RE.match(content)
    if not m:
        return {}, content
    fm_lines = m.group("fm").splitlines()
    fm: dict = {}
    for line in fm_lines:
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm, m.group("body")


# ─────────────────────────── Skill dataclass ───────────────────────────
class Skill:
    __slots__ = ("name", "description", "body", "path", "meta")

    def __init__(self, name: str, description: str, body: str,
                 path: str, meta: dict):
        self.name = name
        self.description = description
        self.body = body
        self.path = path
        self.meta = meta

    def __repr__(self) -> str:
        return f"<Skill {self.name}>"


# ─────────────────────────── 全局注册 ───────────────────────────
_SKILLS: dict[str, Skill] = {}


def load_all_skills() -> dict[str, Skill]:
    """启动时调一次；返回 name→Skill dict（同时内部缓存）"""
    _SKILLS.clear()
    seen = set()
    for base in _search_paths():
        if not base.exists() or not base.is_dir():
            continue
        for p in base.glob("*/SKILL.md"):
            if p in seen:
                continue
            seen.add(p)
            try:
                content = p.read_text(encoding="utf-8")
                fm, body = parse_frontmatter(content)
                name = fm.get("name") or p.parent.name
                if name in _SKILLS:
                    continue  # 前面优先级已注册
                _SKILLS[name] = Skill(
                    name=name,
                    description=fm.get("description", "")[:200],
                    body=body,
                    path=str(p),
                    meta=fm,
                )
            except Exception as e:
                logger.warning("[skill_loader] 加载 {} 失败: {}", p, e)
    logger.info("[skill_loader] 加载 {} 个 skill: {}",
                 len(_SKILLS), list(_SKILLS.keys()))
    return dict(_SKILLS)


def get_skill(name: str) -> Optional[Skill]:
    return _SKILLS.get(name)


def all_skills() -> list[Skill]:
    return list(_SKILLS.values())


def skills_manifest() -> str:
    """给 orchestrator system prompt 用的 skill 清单（仅 name + description）"""
    if not _SKILLS:
        return ""
    lines = ["# 可用 Skill（专门场景的分析模板，命中时用 skill(name) 加载完整指令）"]
    for s in _SKILLS.values():
        lines.append(f"- **{s.name}** — {s.description}")
    return "\n".join(lines)
