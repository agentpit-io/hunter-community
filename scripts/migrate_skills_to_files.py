#!/usr/bin/env python3
"""把 chat_skill.py 里的 BUILTINS dict 转成标准 SKILL.md 文件。

`_14` §6 Step A 的第一步。一次性脚本 —— 跑完之后 BUILTINS 就废弃了,
skills/ 目录成为唯一事实来源,以后改 SKILL 直接改文件。

为什么要转:
  · 标准格式(Anthropic Agent Skills / opencode SkillV2)= 网上下载的 skill
    直接能用,不需要我们做任何转换
  · 用户自建与下载来的走同一条路,不用维护两套逻辑
  · 方法论正文用 Markdown 写,比塞在 Python 字符串里可读、可 diff

生成的格式(实测 opencode 只认 name/description/slash,hunter: 命名空间
下的扩展字段会被原样忽略,所以标准兼容与我们的扩展可以共存):

    ---
    name: quote
    description: ...
    hunter:
      icon: "📊"
      ...
    ---
    # 正文方法论

用法:
    python scripts/migrate_skills_to_files.py            # 生成到 skills/
    python scripts/migrate_skills_to_files.py --dry-run  # 只看会生成什么
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):          # 中文 Windows 控制台默认 GBK
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "apps" / "api" / "app" / "routers" / "chat_skill.py"
OUT = REPO / "skills"


def parse_builtins() -> list[dict]:
    """从 chat_skill.py 里抠出 BUILTINS。

    直接 import 会把整个 FastAPI 依赖链拉起来(需要 DB 等),
    这里用 exec 只求值 BUILTINS 那一段列表字面量。
    """
    src = SRC.read_text(encoding="utf-8")
    m = re.search(r"^BUILTINS:\s*list\[dict\]\s*=\s*(\[.*?^\])", src, re.S | re.M)
    if not m:
        raise SystemExit("找不到 BUILTINS 定义")
    ns: dict = {}
    exec("BUILTINS = " + m.group(1), ns)
    return ns["BUILTINS"]


def yaml_str(v: str) -> str:
    """YAML 标量转义 —— 我们的值里有中文、冒号、引号,一律用双引号包并转义。"""
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_md(b: dict) -> str:
    key = b["key"]
    display = b.get("name", key)
    hint = b.get("hint", "")
    long_desc = b.get("long_desc", "")
    tools = b.get("tools") or []

    # description 是**给模型看的**:它据此判断这次要不要用这个 skill。
    # 所以用 hint(一句话用途)而不是 display_name(可能只是个品牌前缀)。
    desc = hint or long_desc[:80] or display

    lines = ["---", f"name: {key}", f"description: {yaml_str(desc)}",
             "hunter:",
             f"  display_name: {yaml_str(display)}",
             f"  icon: {yaml_str(b.get('icon', '⭐'))}",
             f"  category: {yaml_str(b.get('category', '其他'))}"]
    if b.get("brand"):
        lines.append(f"  brand: {yaml_str(b['brand'])}")
    if b.get("source_url"):
        lines.append(f"  source_url: {yaml_str(b['source_url'])}")
    lines.append(f"  prompt_tpl: {yaml_str(b.get('prompt_tpl', ''))}")
    if tools:
        lines.append("  needs_tools:")
        lines += [f"    - {t}" for t in tools]
    else:
        lines.append("  needs_tools: []")
    # needs_data 留空 —— Step D 才逐个精修。现在瞎填不如不填:
    # 填错会让解析器误判"数据不齐"而把可用的 SKILL 标灰。
    lines.append("  needs_data: []")
    lines.append("---")
    lines.append("")
    lines.append(f"# {display}")
    lines.append("")
    if long_desc:
        lines.append("## 这个能力做什么")
        lines.append("")
        lines.append(long_desc)
        lines.append("")
    lines.append("## 怎么用")
    lines.append("")
    lines.append(f"用户提问后,按下面的模板组织分析:")
    lines.append("")
    lines.append("```")
    lines.append(b.get("prompt_tpl", ""))
    lines.append("```")
    lines.append("")
    if tools:
        lines.append("## 需要的工具")
        lines.append("")
        for t in tools:
            lines.append(f"- `{t}`")
        lines.append("")
    lines.append("<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->")
    return "\n".join(lines) + "\n"


def main() -> int:
    dry = "--dry-run" in sys.argv
    builtins = parse_builtins()
    print(f"[migrate] 解析到 {len(builtins)} 个内置 SKILL")

    if not dry:
        OUT.mkdir(exist_ok=True)

    for b in builtins:
        d = OUT / b["key"]
        content = build_md(b)
        if dry:
            print(f"  会生成 {d.relative_to(REPO)}/SKILL.md  ({len(content)} 字符)")
            continue
        d.mkdir(exist_ok=True)
        (d / "SKILL.md").write_text(content, encoding="utf-8", newline="\n")
        print(f"  ✓ {b['key']:22} {b.get('name','')}")

    if not dry:
        print(f"\n[migrate] 已生成到 {OUT.relative_to(REPO)}/")
        print("[migrate] 下一步:chat_skill.py 改为读文件 · compose 挂载 · 正文精修")
    return 0


if __name__ == "__main__":
    sys.exit(main())
