#!/usr/bin/env python3
"""校验工具箱注册表与容器里的真实 MCP 工具是否还对得上。

这是第四份"某某清单"(前三份:SKILL 的 needs_tools、system prompt 的工具说明、
数据源注册表)。`_13` §3.1 的规矩是**每份清单都必须有自动校验** ——
一天之内漂了四次,每次都是"两个地方写了同一件事,改了一处忘了另一处"。

四项检查:
  1. 注册表里的工具**容器里真的有** —— 多写了会让 UI 显示一个调不出来的能力
  2. 容器里的工具**注册表里都有** —— 少写了那个工具就永远不出现在侧栏
  3. `needs_data` 指向的数据源 key **在 source_catalog 里存在** ← 跨层引用最容易烂
  4. SKILL 的 `needs_tools` 指向的工具**在工具箱注册表里存在**

用法:
    docker compose exec -T api python - < scripts/check_tools.py   # 1、3、4(离线)
    python scripts/check_tools.py --live                           # 追加 1、2(要 docker)

退出码非 0 = 有问题。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
for cand in ("/app", str(REPO / "apps" / "api")):
    if cand not in sys.path and Path(cand).is_dir():
        sys.path.insert(0, cand)

from app.services import source_catalog as sc     # noqa: E402
from app.services import tool_catalog as tc       # noqa: E402

errors: list[str] = []
warns: list[str] = []


def tools_from_container() -> set[str] | None:
    """从容器现读全部 MCP server 的工具名,拼成完整名(server_tool)。"""
    script = r'''python3 - <<'EOF'
import re, os
out = []
for base, extra in (("/opt/opencode-workspace/mcp", ""), ("/opt/hunter-mcp", "")):
    if not os.path.isdir(base):
        continue
    for f in sorted(os.listdir(base)):
        if not f.endswith(".py"):
            continue
        srv = f[:-7] if f.endswith("_mcp.py") else None
        if f == "hunter_capability_mcp.py":
            srv = "hunter_cap"
        if not srv:
            continue
        s = open(os.path.join(base, f), encoding="utf-8").read()
        for n in re.findall(r'Tool\(\s*name="([a-z_0-9]+)"', s):
            out.append(srv + "_" + n)
print("\n".join(sorted(set(out))))
EOF'''
    try:
        r = subprocess.run(["docker", "compose", "exec", "-T", "opencode", "sh", "-c", script],
                           cwd=REPO, capture_output=True, text=True, timeout=90)
    except Exception:
        return None
    names = {l.strip() for l in r.stdout.splitlines() if l.strip() and "_" in l}
    return names or None


def check_against_container(live: set[str]) -> None:
    declared = {t.key for t in tc.CATALOG}
    ghost = declared - live          # 注册了但容器里没有
    missing = live - declared        # 容器里有但没注册
    for k in sorted(ghost):
        errors.append(f"注册表有 {k!r},容器里**没有这个工具** —— UI 会显示一个调不出来的能力")
    for k in sorted(missing):
        warns.append(f"容器里有 {k!r},注册表里没写 —— 这个工具永远不会出现在侧栏")
    print(f"  [1-2] 与容器对账   注册 {len(declared)} · 容器 {len(live)} · "
          f"多写 {len(ghost)} · 漏写 {len(missing)}")


def check_needs_data() -> None:
    """工具声明的数据源必须真实存在 —— 跨层引用,最容易烂的一处。

    **必需与可选都要查**。只查 needs_data 的话,把一个依赖挪到 optional_data
    就等于让它逃过校验 —— 而挪过去恰恰是最容易顺手打错字的时候。
    """
    known = {s.key for s in sc.CATALOG}
    bad = total = 0
    for t in tc.CATALOG:
        for field, keys in (("needs_data", t.needs_data), ("optional_data", t.optional_data)):
            for k in keys:
                total += 1
                if k not in known:
                    errors.append(f"工具 {t.key} 的 {field} 指向不存在的数据源 {k!r}")
                    bad += 1
        dup = set(t.needs_data) & set(t.optional_data)
        if dup:
            errors.append(f"工具 {t.key} 同一个源既必需又可选: {', '.join(sorted(dup))}")
            bad += 1
    print(f"  [3]   needs_data    引用 {total} 处(含 optional)· 无效 {bad}")


def check_skill_needs_tools() -> None:
    """SKILL 声明的工具必须在工具箱注册表里。"""
    declared = {t.key for t in tc.CATALOG}
    skills_dir = REPO / "skills"
    if not skills_dir.is_dir():          # 容器里跑时仓库不在,用挂载点
        skills_dir = Path("/opt/hunter-skills")
    if not skills_dir.is_dir():
        warns.append("找不到 skills 目录,跳过第 4 项检查")
        print("  [4]   SKILL 引用    跳过(找不到 skills 目录)")
        return
    bad, total = 0, 0
    for f in sorted(skills_dir.glob("*/SKILL.md")):
        fm = (re.match(r"^---\s*\n(.*?)\n---", f.read_text(encoding="utf-8"), re.S)
              or ["", ""])[1]
        blk = (re.search(r"^\s{2}needs_tools:\s*\n((?:\s{4}-.*\n)+)", fm, re.M) or ["", ""])[1]
        for t in re.findall(r"^\s{4}-\s*([a-z_0-9]+)\s*$", blk, re.M):
            total += 1
            if t not in declared:
                errors.append(f"SKILL {f.parent.name} 的 needs_tools 指向未注册的工具 {t!r}")
                bad += 1
    print(f"  [4]   SKILL 引用    {total} 处 · 无效 {bad}")


def main() -> int:
    print("检查工具箱注册表 (tool_catalog.py)")
    if "--live" in sys.argv:
        live = tools_from_container()
        if live is None:
            warns.append("容器读取失败,跳过与容器对账(第 1-2 项)")
            print("  [1-2] 与容器对账   跳过(容器没起)")
        else:
            check_against_container(live)
    else:
        print("  [1-2] 与容器对账   跳过(加 --live 启用)")
    check_needs_data()
    check_skill_needs_tools()

    print()
    for w in warns:
        print(f"  ⚠ {w}")
    for e in errors:
        print(f"  ✗ {e}")
    if errors:
        print(f"\n不通过:{len(errors)} 个错误")
        return 1
    print(f"\n通过{'(' + str(len(warns)) + ' 条提醒)' if warns else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
