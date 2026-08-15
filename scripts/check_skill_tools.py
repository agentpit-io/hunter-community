#!/usr/bin/env python3
"""校验 SKILL 文件:引用的工具真实存在 · prompt_tpl 是我们系统认的东西。

为什么需要它:2026-08-14 发现 29 个内置 SKILL 里有 6 个写着
`truesource_get_quote` / `kronos_kronos_forecast` / `debate_start` 之类 ——
那是**生产环境**的 MCP 命名被原样抄进开源版,而开源版镜像根本没有这些工具。
模型照着 tools 字段去调必然失败,而且**没有任何报错**,只表现为"这个能力时好时坏"。

同一类漂移还出现在 system prompt 里(见 a86736b)。根因都是
"我有哪些工具"这份知识散落多处、各自演化。这个脚本把它变成可自动发现的。

**数据源已迁移**(Step A · 2b4d61a):原来读 `chat_skill.py` 的 BUILTINS dict,
现在读 `skills/*/SKILL.md`。BUILTINS 删掉之后这个脚本静默坏了一段时间 ——
校验脚本自己坏掉是最糟的一种,它坏了就没人拦得住漂移。所以现在**读不到文件
直接报错退出**,不再假装通过。

**新增第二项检查**(prompt_tpl):Step A 迁移时把 17 个 uzi_* 的
`prompt_tpl` 原样搬了过来,内容是 `/stock-deep-analyzer:quick-scan {股票}` ——
那是 **Claude Code 插件的斜杠命令**语法,我们系统根本不认。用户点侧栏按钮,
输入框里被填进一串谁也不认识的命令。工具本身是通的,断的是点击这条路。

用法:
    python scripts/check_skill_tools.py              # 从运行中的容器读真实工具集
    python scripts/check_skill_tools.py --offline    # 用脚本内的已知清单(CI 无 docker 时)

退出码:0 = 全部合法 · 1 = 有问题
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# 中文 Windows 控制台默认 GBK,打 emoji / 部分符号会 UnicodeEncodeError 直接崩。
# 这个脚本要能在开发者本机跑(不只是 CI),所以强制 stdout 为 UTF-8。
# 同一类坑今天已经在 README 的 PowerShell 命令上踩过一次(见 ef21ef3)。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO / "skills"
USER_SKILLS_DIR = REPO / "user-skills"

# 离线兜底清单 —— **从工具注册表读**,不再手写一份。
#
# 原来这里是硬编码的 10 个工具名。Step C 加了 hunter_cap 那 3 个之后没同步,
# 于是 forecast 补上 needs_tools: hunter_cap_kpred 时被误报成"引用不存在的工具"。
# 一个防漂移的脚本自己漂了 —— 正是它要治的毛病。
#
# tool_catalog.py 由 scripts/check_tools.py 负责与容器对账,所以从它读是安全的:
# 那份表错了会被另一个脚本抓到,不会两边一起错。
def _known_tools() -> set[str]:
    import importlib.util
    tc = REPO / "apps" / "api" / "app" / "services" / "tool_catalog.py"
    if not tc.is_file():
        return set()
    # 直接 import 会拖起 app 包的依赖,这里只按文件加载这一个模块。
    # **必须先塞进 sys.modules 再 exec** —— dataclass 解析 `list[str]` 这类注解时
    # 要回查 sys.modules[cls.__module__],不注册就 AttributeError: NoneType。
    spec = importlib.util.spec_from_file_location("_tool_catalog", tc)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_tool_catalog"] = mod
    spec.loader.exec_module(mod)
    return {t.key for t in mod.CATALOG}


KNOWN_TOOLS = _known_tools()


def tools_from_container() -> set[str] | None:
    """从运行中的 opencode 容器现读 MCP 工具名。容器没起就返回 None。"""
    script = (
        'for f in watchlist portfolio uzi hunter_user; do '
        'grep -oE \'name="[a-z_]+"\' /opt/opencode-workspace/mcp/${f}_mcp.py 2>/dev/null '
        '| grep -oE \'"[a-z_]+"\' | tr -d \'"\' | sed "s/^/${f}_/"; done'
    )
    try:
        out = subprocess.run(
            ["docker", "compose", "exec", "-T", "opencode", "sh", "-c", script],
            cwd=REPO, capture_output=True, text=True, timeout=60,
        )
    except Exception:
        return None
    names = {l.strip() for l in out.stdout.splitlines() if l.strip()}
    return names or None


def parse_skills() -> list[dict]:
    """扫 skills/ 与 user-skills/ 下每个 SKILL.md,取出 key / needs_tools / prompt_tpl。

    这里故意不 import `app.services.skill_files` —— 那个要跑在容器里(有 loguru
    等依赖),而这个脚本要能在开发者本机裸 python 直接跑。前 matter 结构极简单,
    用正则读这三个字段够了;真出现复杂 YAML 再说。
    """
    out: list[dict] = []
    for base in (SKILLS_DIR, USER_SKILLS_DIR):
        if not base.is_dir():
            continue
        for f in sorted(base.glob("*/SKILL.md")):
            txt = f.read_text(encoding="utf-8")
            m = re.match(r"^---\s*\n(.*?)\n---", txt, re.S)
            fm = m.group(1) if m else ""
            key = (re.search(r"^name:\s*(.+)$", fm, re.M) or [None, f.parent.name])[1]
            tools = re.findall(r"^\s{4}-\s*([a-z_0-9]+)\s*$",
                               (re.search(r"^\s{2}needs_tools:\s*\n((?:\s{4}-.*\n)+)", fm, re.M)
                                or ["", ""])[1], re.M)
            tpl = (re.search(r'^\s{2}prompt_tpl:\s*"?(.*?)"?\s*$', fm, re.M) or ["", ""])[1]
            out.append({"key": str(key).strip().strip('"'), "tools": tools,
                        "prompt_tpl": tpl, "path": f.relative_to(REPO)})
    if not out:
        raise SystemExit(f"[check] 找不到任何 SKILL.md(看过 {SKILLS_DIR} 与 {USER_SKILLS_DIR})\n"
                         f"        校验脚本读不到数据就直接失败,不假装通过。")
    return out


def check_prompt_tpl(skills: list[dict]) -> list[tuple[str, str, str]]:
    """prompt_tpl 会被前端**原样填进输入框**,所以它必须是模型看得懂的自然语言。

    以 `/` 开头的一律拒绝:那是斜杠命令语法。我们系统里没有插件命令这个概念,
    填进去模型只会把它当普通文本,用户看到的就是"点了没反应/答非所问"。
    """
    bad = []
    for s in skills:
        tpl = s["prompt_tpl"]
        if tpl.startswith("/"):
            bad.append((s["key"], tpl, "以 / 开头 · 那是斜杠命令语法,本系统不支持"))
        elif not tpl.strip():
            bad.append((s["key"], "(空)", "空模板 · 点击侧栏卡片不会有任何反应"))
    return bad


def main() -> int:
    offline = "--offline" in sys.argv
    real = None if offline else tools_from_container()
    if real is None:
        real = KNOWN_TOOLS
        print(f"[check] 用离线清单({len(real)} 个工具)"
              + ("" if offline else " —— 容器没起或读取失败"))
    else:
        print(f"[check] 从容器读到 {len(real)} 个工具")

    skills = parse_skills()
    print(f"[check] 检查了 {len(skills)} 个 SKILL 文件")

    bad_tools = [(s["key"], t) for s in skills for t in s["tools"] if t not in real]
    bad_tpl = check_prompt_tpl(skills)

    rc = 0
    if bad_tools:
        rc = 1
        print(f"\n❌ [needs_tools] {len(bad_tools)} 处引用了不存在的工具:")
        for key, t in bad_tools:
            print(f"   {key:22} → {t}")
        print("\n   只能填本部署实际存在的工具名。现有清单:")
        for t in sorted(real):
            print(f"     {t}")
    else:
        print("✅ [needs_tools] 全部引用合法")

    if bad_tpl:
        rc = 1
        print(f"\n❌ [prompt_tpl] {len(bad_tpl)} 个模板前端填进去也没用:")
        for key, tpl, why in bad_tpl:
            print(f"   {key:22} → {tpl}")
            print(f"   {'':22}   {why}")
        print("\n   模板会被原样填进输入框,得是模型看得懂的人话,例如:")
        print('     "对 {股票} 做 DCF 估值建模 · 给出内在价值区间"')
    else:
        print("✅ [prompt_tpl] 全部是自然语言")

    return rc


if __name__ == "__main__":
    sys.exit(main())
