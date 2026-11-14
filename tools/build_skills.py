#!/usr/bin/env python3
"""把仓库 `skills/` 里的 HunterCode SKILL 转成 AtomCode 能直接加载的技能。

    skills/<名>/SKILL.md  ──►  distro/workspace-template/.atomcode/skills/<名>/SKILL.md

对应开发计划 v0.2 的 TP-03。做四件事：

1. **目录式复制**：AtomCode 要的就是 `<技能名>/SKILL.md`，与我们现有布局同构，
   连带 `references/` 整个拷过去（M0 §6 实测，格式不用改）。
2. **占位符 → `$ARGUMENTS`**：见下面「占位符」一节，规则比一句话复杂。
3. **`hunter.needs_tools` → `allowed-tools`**：SKILL 里写的是 opencode 风格的
   `<服务>_<工具>`（`uzi_stock_deep_analysis`），AtomCode 的 MCP 工具名是
   `mcp__<服务>__<工具>`（`mcp__uzi__stock_deep_analysis`）。映射表不是猜的，
   来自 `distro/mcp-tools.json`（由 `deploy/tools/dump_mcp_tools.py` 直连每个
   stdio server 跑真实 `tools/list` 生成）。
4. **保留 `hunter:` 扩展字段**：M0 §6 实测，AtomCode 的 frontmatter 解析器
   逐行匹配 `name:` / `description:` / `allowed-tools:` / `user-invocable:` 四个前缀，
   **未知键被忽略、不会解析失败**。所以 `hunter:` 原样留着 ——
   HunterCode 前端要的 `display_name` / `icon` / `category` / `prompt_tpl`
   只能从 SKILL.md 自己读（`GET /skills` 只回 name + description，待办池 P1-3）。

## 占位符：为什么不是无脑替换

仓库里 `{…}` 一共出现在两个地方，含义完全相反：

| 出现位置 | 例子 | 是什么 | 怎么处理 |
|---|---|---|---|
| `hunter.prompt_tpl`（5 个技能） | `帮我写一份 {股票} 的深度投研报告` | **前端**用来预填输入框的模板 | **原样保留**。改成 `$ARGUMENTS` 会让前端提示语变成「帮我写一份 $ARGUMENTS 的…」 |
| `references/*.md`（investor_panel，30 处） | `{真实数字}` | 给模型的**输出**填空指引（"这里要填真实数字"） | **原样保留**。替换成 `$ARGUMENTS` 会把 9 份打分细则全写坏 |
| SKILL.md 正文 | 目前 0 处 | 真正的入参占位 | 替换成 `$ARGUMENTS`（规则已实现并有单测） |

真正要解决的问题是「用户带着参数调技能时，参数怎么进正文」。做法是在正文末尾生成一段
`## 参数` 区块，显式写上 `$ARGUMENTS`，占位符名字从 `prompt_tpl` 里推出来。

顺带说明 AtomCode 的实际行为（上游 `skills/skill.rs::expand` 源码）：模板里**没有**
`$ARGUMENTS` 时它会把参数**追加**到末尾。所以显式写出来不是必须的，但能控制参数出现的
位置和上下文措辞，比默认追加好。

## 一个必须说清楚的事实：`allowed-tools` 在 5.1.0 里不生效

上游 `crates/atomcode-capabilities/src/skills/skill.rs` 解析了 `allowed-tools`
并存进 `Skill.allowed_tools`，但**全仓库没有任何地方读它**（字段注释自己写着
"the L1 capability does not enforce it — that's an L2 approval-policy concern"，
而 L2 那侧不存在）。

所以这个字段目前是**纯元数据**：写了不会限制模型，也不会出现在 `GET /skills` 里。
好处是不会误伤（不用担心写窄了把 `read_file` 挡掉），代价是别指望它做安全边界 ——
硬约束只有 hook（M0 §4.5）。已记入 `docs/questions-for-atomgit.md`。

用法：

    python3 tools/build_skills.py            # 生成
    python3 tools/build_skills.py --check    # 只比对，有差异 exit 1（CI 用）
    python3 tools/build_skills.py --quiet    # 只输出汇总
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = REPO_ROOT / "skills"
DEFAULT_DST = REPO_ROOT / "distro" / "workspace-template" / ".atomcode" / "skills"
DEFAULT_TOOL_MAP = REPO_ROOT / "distro" / "mcp-tools.json"

# 正文里自动生成区块的边界。重跑时先整块删掉再重建 —— 幂等靠这个，
# 不靠"输出目录先删干净"（那样会连用户放进去的东西一起删）。
BLOCK_BEGIN = "<!-- HCA:BEGIN 由 tools/build_skills.py 生成，勿手改 -->"
BLOCK_END = "<!-- HCA:END -->"

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.S)
# 正文里的入参占位：`{中文或字母}`，不含换行、不含嵌套花括号
PLACEHOLDER_RE = re.compile(r"\{([^{}\n]{1,24})\}")


class BuildError(Exception):
    """转换失败（映射不到工具、SKILL 缺 frontmatter 等）。"""


@dataclass
class SkillLog:
    """单个技能的转换日志 —— 每一步都要能说出「改了什么、为什么」。"""

    name: str
    src: Path
    dst: Path
    needs_tools: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    body_placeholders: list[str] = field(default_factory=list)
    prompt_tpl_placeholders: list[str] = field(default_factory=list)
    body_tool_rewrites: list[tuple[str, str]] = field(default_factory=list)
    refs_copied: int = 0
    changed: bool = False
    added_args_block: bool = False

    def lines(self) -> list[str]:
        out = [f"技能 {self.name}"]
        out.append(f"  源     {self.src.relative_to(REPO_ROOT)}")
        out.append(f"  目标   {self.dst.relative_to(REPO_ROOT)}")
        if self.needs_tools:
            for raw, mapped in zip(self.needs_tools, self.allowed_tools):
                out.append(f"  工具   {raw}  ->  {mapped}")
        else:
            out.append("  工具   （该技能没声明 needs_tools，allowed-tools 留空）")
        for raw in self.unmapped:
            out.append(f"  工具   {raw}  ->  ⚠ 映射不到，已原样保留")
        if self.body_placeholders:
            out.append(
                "  占位   正文 "
                + "、".join("{%s}" % p for p in self.body_placeholders)
                + "  ->  $ARGUMENTS"
            )
        else:
            out.append("  占位   正文无入参占位符")
        if self.prompt_tpl_placeholders:
            out.append(
                "  占位   hunter.prompt_tpl "
                + "、".join("{%s}" % p for p in self.prompt_tpl_placeholders)
                + "  ->  原样保留（前端模板字段），改为在正文生成 $ARGUMENTS 区块"
            )
        if self.body_tool_rewrites:
            for raw, mapped in self.body_tool_rewrites:
                out.append(f"  正文   `{raw}`  ->  `{mapped}`（反引号内的工具名改成 AtomCode 真名）")
        else:
            out.append("  正文   没有需要改名的工具引用")
        out.append(f"  正文   {'已生成 ## 参数 区块' if self.added_args_block else '未生成 ## 参数 区块'}")
        out.append(f"  附件   references/ 复制 {self.refs_copied} 个文件")
        out.append(f"  结果   {'已更新' if self.changed else '无变化（幂等）'}")
        return out


# ── frontmatter ──────────────────────────────────────────────────────────────


def split_frontmatter(text: str) -> tuple[str, str]:
    """拆出 (frontmatter 原文, 正文)。没有 frontmatter 就抛错 —— AtomCode 至少要 name。"""
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise BuildError("SKILL.md 没有 `---` 包起来的 frontmatter")
    return m.group(1), text[m.end():]


def fm_top_level_key(line: str, key: str) -> str | None:
    """顶层键取值。只认**顶格**的键 —— 与 AtomCode 的逐行前缀匹配口径一致，
    这样 `hunter:` 块里缩进的 `display_name:` 不会被误当成顶层 `name:`。"""
    if line.startswith(f"{key}:"):
        return line[len(key) + 1:].strip()
    return None


def parse_needs_tools(fm: str) -> list[str]:
    """从 frontmatter 里挖 `hunter.needs_tools` 的列表项。

    不引 PyYAML：仓库里只有 6 个 SKILL、结构固定，多一个第三方依赖不划算
    （而且 AtomCode 自己的解析器也是逐行的，跟着它的口径更不容易出偏差）。
    """
    lines = fm.splitlines()
    in_hunter = False
    in_needs = False
    needs_indent = 0
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_hunter = line.startswith("hunter:")
            in_needs = False
            continue
        if not in_hunter:
            continue
        if in_needs:
            if stripped.startswith("-") and indent > needs_indent:
                out.append(stripped[1:].strip().strip("'\""))
                continue
            if indent <= needs_indent:
                in_needs = False
                # 不 continue，下面还要判断这一行是不是又一个 needs_tools
        if stripped.startswith("needs_tools:"):
            in_needs = True
            needs_indent = indent
            inline = stripped[len("needs_tools:"):].strip()
            if inline.startswith("[") and inline.endswith("]"):
                out.extend(
                    x.strip().strip("'\"") for x in inline[1:-1].split(",") if x.strip()
                )
                in_needs = False
    return out


def parse_prompt_tpl(fm: str) -> str:
    for line in fm.splitlines():
        stripped = line.strip()
        if stripped.startswith("prompt_tpl:") and (len(line) - len(line.lstrip())) > 0:
            return stripped[len("prompt_tpl:"):].strip().strip("'\"")
    return ""


def upsert_fm_line(fm: str, key: str, value: str) -> str:
    """把 `key: value` 写进 frontmatter：已有顶层同名键就替换，没有就插在
    `description:` 之后（没有 description 就追加到末尾）。

    只动这一行，`hunter:` / `metadata:` / `version:` 等一概原样保留。
    """
    lines = fm.splitlines()
    for i, line in enumerate(lines):
        if fm_top_level_key(line, key) is not None:
            lines[i] = f"{key}: {value}"
            return "\n".join(lines)
    for i, line in enumerate(lines):
        if line.startswith("description:"):
            lines.insert(i + 1, f"{key}: {value}")
            return "\n".join(lines)
    lines.append(f"{key}: {value}")
    return "\n".join(lines)


# ── 工具名映射 ────────────────────────────────────────────────────────────────


def load_tool_map(path: Path) -> dict[str, list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    servers = data.get("servers")
    if not isinstance(servers, dict) or not servers:
        raise BuildError(f"{path} 里没有 servers")
    return {k: list(v) for k, v in servers.items()}


def map_tool_name(raw: str, servers: dict[str, list[str]]) -> str | None:
    """opencode 风格的工具名 → AtomCode 的 `mcp__<服务>__<工具>`。

    opencode 给 MCP 工具加的前缀是**服务名 + 下划线**，于是同一个字符串可能有
    多种切法：`portfolio_rebalance` 既可以读成「服务 portfolio 的工具 rebalance」
    （不存在），也可以读成「服务 portfolio 的工具 portfolio_rebalance」（真实存在）。
    所以**不按字符串猜，按注册表核对**：

    1. 精确命中 `<服务>_<工具>`（工具名本身可能还带服务名前缀，如上例）；
    2. 退一步，工具名在某个服务里唯一（SKILL 直接写了裸工具名的情况）；
    3. 都不中 → None，调用方决定是报错还是原样保留。

    最长服务名优先，避免 `hunter_cap` 与 `hunter_user` 这种共享前缀的歧义。
    """
    for server in sorted(servers, key=len, reverse=True):
        for tool in servers[server]:
            if raw == f"{server}_{tool}" or raw == tool:
                return f"mcp__{server}__{tool}"
    return None


# ── 正文加工 ──────────────────────────────────────────────────────────────────


def strip_generated_block(body: str) -> str:
    """删掉上一次生成的区块（含前后空行），保证重跑幂等。"""
    start = body.find(BLOCK_BEGIN)
    if start == -1:
        return body
    end = body.find(BLOCK_END, start)
    if end == -1:
        return body[:start].rstrip() + "\n"
    return (body[:start].rstrip() + "\n" + body[end + len(BLOCK_END):].lstrip("\r\n")).rstrip() + "\n"


def rewrite_body_tool_names(body: str, servers: dict[str, list[str]]) -> tuple[str, list[tuple[str, str]]]:
    """把正文里反引号包着的 opencode 风格工具名换成 AtomCode 的真名。

    为什么必须做：SKILL 正文是**写给模型看的操作说明**，里面直接点名
    「调用 `uzi_stock_deep_analysis`」。AtomCode 下这个名字不存在，真名是
    `mcp__uzi__stock_deep_analysis`。M0 §11.1 用例 6 实测过后果 ——
    模型老老实实照 SKILL 去找 `portfolio_update_risk_profile`，找不到，
    只好改口说明情况。光改 frontmatter 的 allowed-tools 救不了这个
    （何况那个字段在 5.1.0 根本不生效）。

    只替换**反引号里、且是 `<服务>_<工具>` 完整形式**的出现：
    这个形式带服务名前缀，不可能和正文里的普通词撞上。裸工具名
    （`invoke`、`market_screen` 这种）不碰 —— 它们在中文正文里太容易误伤。
    """
    alias: dict[str, str] = {}
    for server, tools in servers.items():
        for tool in tools:
            alias[f"{server}_{tool}"] = f"mcp__{server}__{tool}"
    hits: list[tuple[str, str]] = []
    for raw in sorted(alias, key=len, reverse=True):
        needle = f"`{raw}`"
        if needle in body:
            n = body.count(needle)
            body = body.replace(needle, f"`{alias[raw]}`")
            hits.append((raw, alias[raw]))
            del n
    return body, hits


def convert_body_placeholders(body: str) -> tuple[str, list[str]]:
    """把正文里的 `{占位}` 换成 `$ARGUMENTS`（目前 6 个 SKILL 正文里一个都没有，
    规则先立在这儿，将来加技能时自动生效）。"""
    found = [m.group(1) for m in PLACEHOLDER_RE.finditer(body)]
    if not found:
        return body, []
    return PLACEHOLDER_RE.sub("$ARGUMENTS", body), found


def build_args_block(placeholders: list[str], tool_pairs: list[tuple[str, str]]) -> str:
    """生成尾部的自动区块：`## 参数` + `## 工具名对照`。

    参数名从 prompt_tpl 的占位符推出来，推不出就用通用措辞。
    工具名对照是 rewrite_body_tool_names 的兜底：正文里没被反引号包住、
    或者换了写法的地方，模型还能从这张表查到真名。
    """
    if placeholders:
        what = "、".join(placeholders)
        desc = f"本技能的入参是**{what}**（代码或名称都可以）。用户这次给的是："
    else:
        desc = "用户这次调用带的参数（可能为空）："
    parts = [
        BLOCK_BEGIN,
        "",
        "## 参数",
        "",
        desc,
        "",
        "$ARGUMENTS",
        "",
        "参数为空时，先问清楚要看哪只标的再开工，**不要随便挑一只演示**。",
        "",
    ]
    if tool_pairs:
        parts += [
            "## 工具名对照",
            "",
            "上面正文提到的工具，在 AtomCode 里的真名是：",
            "",
            "| 正文里的写法 | 实际要调的工具 |",
            "|---|---|",
        ]
        parts += [f"| `{raw}` | `{mapped}` |" for raw, mapped in tool_pairs]
        parts += [
            "",
            "**按右边那一列调。** 左边是 HunterCode opencode 版的命名，这套发行版里不存在。",
            "",
        ]
    parts += [BLOCK_END, ""]
    return "\n".join(parts)


# ── 主流程 ────────────────────────────────────────────────────────────────────


def render_skill(text: str, servers: dict[str, list[str]], log: SkillLog) -> str:
    fm, body = split_frontmatter(text)

    raw_tools = parse_needs_tools(fm)
    mapped: list[str] = []
    for raw in raw_tools:
        m = map_tool_name(raw, servers)
        if m is None:
            log.unmapped.append(raw)
            mapped.append(raw)  # 原样保留，别静默丢掉
        else:
            log.needs_tools.append(raw)
            mapped.append(m)
            log.allowed_tools.append(m)

    if mapped:
        # AgentSkills 规范是空格分隔；AtomCode 两种都收（split([' ', ','])）。
        # 用空格，跟规范走。
        fm = upsert_fm_line(fm, "allowed-tools", " ".join(mapped))
    # 显式写出来：daemon 的 GET /skills 只列 user-invocable 的，缺省虽然也是 true，
    # 但写死了才不会被将来的上游默认值变化带跑。
    fm = upsert_fm_line(fm, "user-invocable", "true")

    body = strip_generated_block(body)
    body, tool_hits = rewrite_body_tool_names(body, servers)
    log.body_tool_rewrites = tool_hits
    body, body_ph = convert_body_placeholders(body)
    log.body_placeholders = body_ph

    tpl = parse_prompt_tpl(fm)
    tpl_ph = [m.group(1) for m in PLACEHOLDER_RE.finditer(tpl)]
    log.prompt_tpl_placeholders = tpl_ph

    # 对照表取「正文里改过名的」并上「needs_tools 声明的」，去重保序
    pairs: list[tuple[str, str]] = list(tool_hits)
    seen = {a for a, _ in pairs}
    for raw, mapped in zip(log.needs_tools, log.allowed_tools):
        if raw not in seen:
            pairs.append((raw, mapped))
            seen.add(raw)
    block = build_args_block(tpl_ph, pairs)
    log.added_args_block = True
    body = body.rstrip() + "\n\n" + block

    return f"---\n{fm}\n---\n\n{body.lstrip()}"


def write_if_changed(path: Path, content: str, check: bool) -> tuple[bool, str]:
    """返回 (是否有差异, 差异文本)。check 模式只比对不落盘。"""
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == content:
        return False, ""
    diff = "\n".join(
        difflib.unified_diff(
            (old or "").splitlines(),
            content.splitlines(),
            fromfile=f"{path} (旧)",
            tofile=f"{path} (新)",
            lineterm="",
            n=1,
        )
    )
    if not check:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return True, diff


def copy_references(src_dir: Path, dst_dir: Path, check: bool) -> tuple[int, bool]:
    """整份拷 `references/`（逐字节，**不做占位符替换** —— 里面的 `{真实数字}`
    是给模型的输出填空指引，替换掉会把 9 份打分细则写坏）。

    返回 (文件数, 是否有变化)。目标目录里多出来的文件会被删掉，
    这样删了一份 reference 之后重跑能真的同步掉。"""
    src_refs = src_dir / "references"
    dst_refs = dst_dir / "references"
    if not src_refs.is_dir():
        if dst_refs.exists() and not check:
            shutil.rmtree(dst_refs)
        return 0, dst_refs.exists()

    changed = False
    count = 0
    wanted: set[Path] = set()
    for f in sorted(src_refs.rglob("*")):
        if f.is_dir():
            continue
        rel = f.relative_to(src_refs)
        target = dst_refs / rel
        wanted.add(target)
        count += 1
        data = f.read_bytes()
        if not target.exists() or target.read_bytes() != data:
            changed = True
            if not check:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
    if dst_refs.is_dir():
        for f in sorted(dst_refs.rglob("*")):
            if f.is_file() and f not in wanted:
                changed = True
                if not check:
                    f.unlink()
    return count, changed


def build(
    src: Path = DEFAULT_SRC,
    dst: Path = DEFAULT_DST,
    tool_map: Path = DEFAULT_TOOL_MAP,
    check: bool = False,
) -> tuple[list[SkillLog], list[str]]:
    """返回 (每个技能的转换日志, 差异文本列表)。"""
    servers = load_tool_map(tool_map)
    skill_dirs = sorted(p for p in src.iterdir() if p.is_dir() and (p / "SKILL.md").is_file())
    if not skill_dirs:
        raise BuildError(f"{src} 下一个 SKILL.md 都没有")

    logs: list[SkillLog] = []
    diffs: list[str] = []
    for d in skill_dirs:
        out_dir = dst / d.name
        log = SkillLog(name=d.name, src=d / "SKILL.md", dst=out_dir / "SKILL.md")
        if src.resolve() == DEFAULT_SRC.resolve():
            try:
                from tools.skill_security_scanner import scan_skill_dir
                scan_rep = scan_skill_dir(d)
                if not scan_rep.passed:
                    err_msg = "; ".join(f"[{i.rule_id}] {i.message}" for i in scan_rep.issues)
                    raise BuildError(f"安全与许可证扫描未通过: {err_msg}")
            except ImportError:
                pass
        try:
            rendered = render_skill((d / "SKILL.md").read_text(encoding="utf-8"), servers, log)
        except BuildError as e:
            raise BuildError(f"{d.name}: {e}") from e
        changed, diff = write_if_changed(out_dir / "SKILL.md", rendered, check)
        if diff:
            diffs.append(diff)
        n_refs, refs_changed = copy_references(d, out_dir, check)
        log.refs_copied = n_refs
        log.changed = changed or refs_changed
        logs.append(log)

    # 输出目录里多出来的技能目录（源里已经删了的）也要清掉
    if dst.is_dir():
        known = {d.name for d in skill_dirs}
        for p in sorted(dst.iterdir()):
            if p.is_dir() and p.name not in known:
                diffs.append(f"多余技能目录：{p}（源 {src} 里已不存在）")
                if not check:
                    shutil.rmtree(p)
    return logs, diffs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="skills/ → distro/workspace-template/.atomcode/skills/ 技能转换器",
    )
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC, help="源技能目录")
    ap.add_argument("--dst", type=Path, default=DEFAULT_DST, help="输出目录")
    ap.add_argument("--tool-map", type=Path, default=DEFAULT_TOOL_MAP, help="MCP 工具注册表")
    ap.add_argument("--check", action="store_true", help="只比对不落盘，有差异 exit 1")
    ap.add_argument("--quiet", action="store_true", help="只输出汇总")
    ap.add_argument("--strict", action="store_true", help="有 needs_tools 映射不到就 exit 2")
    args = ap.parse_args(argv)

    try:
        logs, diffs = build(args.src, args.dst, args.tool_map, args.check)
    except BuildError as e:
        print(f"✗ 转换失败：{e}", file=sys.stderr)
        return 2

    if not args.quiet:
        for log in logs:
            print("\n".join(log.lines()))
            print()

    n_changed = sum(1 for x in logs if x.changed)
    n_unmapped = sum(len(x.unmapped) for x in logs)
    n_tools = sum(len(x.allowed_tools) for x in logs)
    verb = "需要更新" if args.check else "已更新"
    print(
        f"汇总：{len(logs)} 个技能，{n_tools} 条工具映射，{n_changed} 个{verb}，"
        f"{n_unmapped} 条工具映射不到"
    )

    if n_unmapped and args.strict:
        print("✗ --strict：有工具映射不到", file=sys.stderr)
        return 2
    if args.check and diffs:
        print("\n── 差异 ──", file=sys.stderr)
        for d in diffs:
            print(d, file=sys.stderr)
        print("✗ --check：产物与源不一致，请跑一次 python3 tools/build_skills.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
