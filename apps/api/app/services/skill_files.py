"""标准 SKILL.md 文件加载器 —— 内置能力的唯一事实来源。

`_14` §6 Step A。原来 29 个内置能力是 `chat_skill.py` 里的 Python dict,
现在改成标准 SKILL.md 文件(Anthropic Agent Skills / opencode SkillV2 格式)。

**为什么值得改**:
  · 标准格式 = 网上下载的 skill 原样丢进目录就能用,不需要任何转换
  · 用户自建与下载来的走同一条路,不用维护两套逻辑
  · 方法论正文用 Markdown 写,可读、可 diff、可 PR

**两个目录(实测确认,见 `_14` §2 结论 3)**:
  我们发的   SKILLS_DIR      默认 /opt/hunter-skills
  用户加的   USER_SKILLS_DIR 默认 /opt/hunter-user-skills
两边同名时**用户的覆盖我们的** —— 用户想改我们某个 SKILL 的措辞,
放一个同名目录即可,不用改我们的文件。

**扩展字段**:标准只认 name/description/slash,我们的东西收在 `hunter:`
命名空间下。实测 opencode 会原样忽略它(所以标准兼容与扩展可以共存),
而这里由我们自己解析。收进命名空间而不是平铺,是为了不跟 opencode 未来
新增的标准字段撞名。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from loguru import logger

SKILLS_DIR = Path(os.getenv("HUNTER_SKILLS_DIR", "/opt/hunter-skills"))
USER_SKILLS_DIR = Path(os.getenv("HUNTER_USER_SKILLS_DIR", "/opt/hunter-user-skills"))

# 分类展示顺序 · 前端 SkillManager 按此分组
CATEGORY_ORDER = ["快速判断", "综合分析", "投研报告", "估值建模",
                  "事件与筛选", "组合级", "尽调风控", "其他"]

_cache: list[dict] | None = None


# ── 极简 YAML frontmatter 解析 ────────────────────────────────
# 不引 pyyaml:SKILL.md 的 frontmatter 结构极其固定(标量 / 一层嵌套 / 字符串数组),
# 手写 30 行比多一个依赖划算。真遇到复杂 YAML 再换。

def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return v


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """返回 (frontmatter dict, 正文)。没有 frontmatter 就返回 ({}, 全文)。"""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    raw, body = m.group(1), m.group(2)

    data: dict[str, Any] = {}
    cur_map: dict | None = None      # 当前处于哪个嵌套 map 下(如 hunter:)
    cur_list: list | None = None     # 当前正在累积的数组
    cur_key: str | None = None

    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        s = line.strip()

        if s.startswith("- "):                       # 数组项
            if cur_list is not None:
                cur_list.append(_unquote(s[2:]))
            continue

        if ":" not in s:
            continue
        k, _, v = s.partition(":")
        k, v = k.strip(), v.strip()
        cur_list = None

        if indent == 0:
            cur_map = None
            if v == "":                              # 顶层嵌套 map 或数组
                data[k] = {}
                cur_map, cur_key = data[k], k
            elif v == "[]":
                data[k] = []
            else:
                data[k] = _unquote(v)
        else:                                        # 嵌套一层
            target = cur_map if cur_map is not None else data
            if v == "":
                target[k] = []
                cur_list = target[k]
            elif v == "[]":
                target[k] = []
            else:
                target[k] = _unquote(v)
    return data, body


def _load_one(skill_dir: Path, builtin: bool) -> dict | None:
    f = skill_dir / "SKILL.md"
    if not f.is_file():
        return None
    try:
        fm, body = _parse_frontmatter(f.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[skill_files] 解析失败 {}: {}", f, e)
        return None

    name = fm.get("name") or skill_dir.name
    h = fm.get("hunter") or {}
    if not isinstance(h, dict):
        h = {}

    return {
        "key": name,
        "builtin": builtin,
        # 下面几个字段的默认值让**网上下载的标准 skill**也能在 UI 上显示得体:
        # 它们不会有 hunter: 段,于是 display_name 回落到 name、分类进"其他"。
        "icon": h.get("icon") or "⭐",
        "name": h.get("display_name") or name,
        "prompt_tpl": h.get("prompt_tpl") or "",
        "hint": fm.get("description") or "",
        "brand": h.get("brand") or "",
        "source_url": h.get("source_url") or "",
        "category": h.get("category") or "其他",
        "needs_tools": h.get("needs_tools") or [],
        "needs_data": h.get("needs_data") or [],
        "playbook": body.strip(),
        "_path": str(f),
    }


def load_all(force: bool = False) -> list[dict]:
    """加载两个目录下的全部 SKILL。用户同名的覆盖我们的。

    结果缓存在进程内 —— SKILL 文件是随部署走的,不会在运行期变。
    用户新加了 skill 需要重启容器(README 里写清楚了)。
    """
    global _cache
    if _cache is not None and not force:
        return _cache

    out: dict[str, dict] = {}
    for d, builtin in ((SKILLS_DIR, True), (USER_SKILLS_DIR, False)):
        if not d.is_dir():
            continue
        for sub in sorted(d.iterdir()):
            if not sub.is_dir():
                continue
            item = _load_one(sub, builtin)
            if item:
                out[item["key"]] = item      # 后加载的(用户的)覆盖先加载的

    _cache = list(out.values())
    logger.info("[skill_files] 加载 {} 个 SKILL(内置目录 {} · 用户目录 {})",
                len(_cache), SKILLS_DIR, USER_SKILLS_DIR)
    return _cache


def category_order() -> list[str]:
    return CATEGORY_ORDER


# ══════════════════════════════════════════════════════════════
# 写入(用户自建 SKILL)
#
# `_19` §5.2。原来用户自建走 `chat_user_skill` 数据库表,只能存
# name / icon / prompt_tpl 三个字段 —— 建出来的本质是「带图标的提示词
# 快捷方式」,不是带方法论、能声明工具依赖的 SKILL。
#
# 改成写文件之后,「UI 里建的」「手动放进目录的」「从 GitHub 装的」
# 变成**同一个东西**,一套加载逻辑。
# ══════════════════════════════════════════════════════════════

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")

# 目录名黑名单 —— 这些会跟文件系统或加载逻辑打架
_RESERVED = {"con", "prn", "aux", "nul", "user-skills", "skills"}


class SkillWriteError(ValueError):
    """写入前的校验失败 —— 调用方转成 400 给用户看,不是 500。"""


def validate_name(name: str) -> str:
    """目录名必须是安全的小写标识符。

    **不接受任意字符串**:这个值会直接拼进文件路径。`../` 之类能写到
    挂载点外面去,而这个函数的输入来自网页表单。
    """
    n = (name or "").strip().lower()
    if not _NAME_RE.match(n):
        raise SkillWriteError(
            "名称只能用小写字母/数字/下划线,字母开头,2-40 位(例:my_dcf_check)")
    if n in _RESERVED:
        raise SkillWriteError(f"{n} 是保留名,换一个")
    return n


def _yaml_str(v: str) -> str:
    """YAML 标量 —— 一律双引号包并转义,值里有中文、冒号、引号都不怕。"""
    return '"' + str(v or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(fields: dict, body: str) -> str:
    """把表单字段渲染成标准 SKILL.md。

    渲染出来的必须是**标准格式** —— 用户导出这个文件丢到别人的
    Claude Code / opencode 里也该能用,而不是只有我们认。
    所以扩展字段一律收在 `hunter:` 命名空间下。
    """
    name = validate_name(fields.get("name", ""))
    lines = ["---", f"name: {name}",
             f"description: {_yaml_str(fields.get('description') or fields.get('display_name') or name)}",
             "hunter:",
             f"  display_name: {_yaml_str(fields.get('display_name') or name)}",
             f"  icon: {_yaml_str(fields.get('icon') or '⭐')}",
             f"  category: {_yaml_str(fields.get('category') or '其他')}"]
    if fields.get("brand"):
        lines.append(f"  brand: {_yaml_str(fields['brand'])}")
    if fields.get("source_url"):
        lines.append(f"  source_url: {_yaml_str(fields['source_url'])}")
    lines.append(f"  prompt_tpl: {_yaml_str(fields.get('prompt_tpl') or '')}")
    for key in ("needs_tools", "needs_data"):
        vals = [v for v in (fields.get(key) or []) if v]
        if vals:
            lines.append(f"  {key}:")
            lines += [f"    - {v}" for v in vals]
        else:
            lines.append(f"  {key}: []")
    # 来源信息 —— 日后排查"这个 skill 哪来的"全靠它
    lines.append(f"  origin: {_yaml_str(fields.get('origin') or 'ui')}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {fields.get('display_name') or name}")
    lines.append("")
    lines.append((body or "").strip() or "> 正文待补充。写清楚:什么时候用、怎么做、什么时候**不适用**。")
    lines.append("")
    return "\n".join(lines)


def save(fields: dict, body: str) -> Path:
    """写 user-skills/{name}/SKILL.md。返回写入路径。

    **换行一律 LF** —— 这个文件要挂进 Linux 容器被 opencode 解析,
    YAML frontmatter 对回车符敏感,值会带上尾随回车且肉眼看不出。
    (`.gitattributes` 管的是仓库里的文件,运行时新建的管不着。)
    """
    name = validate_name(fields.get("name", ""))
    USER_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    d = USER_SKILLS_DIR / name
    d.mkdir(exist_ok=True)
    content = render(fields, body).replace("\r\n", "\n").replace("\r", "\n")
    (d / "SKILL.md").write_text(content, encoding="utf-8", newline="\n")
    load_all(force=True)          # 让本进程立刻看到;opencode 那边另外 refresh
    logger.info("[skill_files] 写入用户 SKILL {}", d)
    return d / "SKILL.md"


def delete(name: str) -> bool:
    """删掉用户自建的 SKILL。**只动 user-skills/**,内置目录碰都不碰。"""
    n = validate_name(name)
    d = USER_SKILLS_DIR / n
    if not d.is_dir():
        return False
    import shutil
    shutil.rmtree(d)
    load_all(force=True)
    logger.info("[skill_files] 删除用户 SKILL {}", d)
    return True


def is_user_skill(name: str) -> bool:
    try:
        return (USER_SKILLS_DIR / validate_name(name) / "SKILL.md").is_file()
    except SkillWriteError:
        return False
