#!/usr/bin/env python3
"""校验 SKILL 的 tools 字段只引用真实存在的 MCP 工具。

为什么需要它:2026-08-14 发现 29 个内置 SKILL 里有 6 个写着
`truesource_get_quote` / `kronos_kronos_forecast` / `debate_start` 之类 ——
那是**生产环境**的 MCP 命名被原样抄进开源版,而开源版镜像根本没有这些工具。
模型照着 tools 字段去调必然失败,而且**没有任何报错**,只表现为"这个能力时好时坏"。

同一类漂移今天还出现在 system prompt 里(见 a86736b)。根因都是
"我有哪些工具"这份知识散落多处、各自演化。这个脚本把它变成可自动发现的。

用法:
    python scripts/check_skill_tools.py              # 从运行中的容器读真实工具集
    python scripts/check_skill_tools.py --offline    # 用脚本内的已知清单(CI 无 docker 时)

退出码:0 = 全部合法 · 1 = 有引用不存在的工具
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
SKILL_FILE = REPO / "apps" / "api" / "app" / "routers" / "chat_skill.py"

# 离线兜底清单 · 与镜像 .opencode/opencode.jsonc 注册的 4 个 MCP 对应。
# 镜像加减 MCP 时要更新这里(或者直接用在线模式,它从容器现读)。
KNOWN_TOOLS = {
    "watchlist_stock_quickview", "watchlist_stock_news",
    "watchlist_watchlist_digest", "watchlist_watchlist_add",
    "portfolio_portfolio_rebalance", "portfolio_portfolio_stress",
    "portfolio_update_risk_profile",
    "uzi_stock_deep_analysis",
    "hunter_user_list_my_sources", "hunter_user_invoke",
}


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


def declared_tools() -> list[tuple[str, list[str]]]:
    """解析 chat_skill.py 里每个内置 SKILL 的 (key, tools)。"""
    src = SKILL_FILE.read_text(encoding="utf-8")
    found = []
    for m in re.finditer(r'\{\s*"key":\s*"([a-z_0-9]+)".*?"tools":\s*\[([^\]]*)\]', src, re.S):
        tools = [t.strip().strip('"') for t in m.group(2).split(",") if t.strip()]
        found.append((m.group(1), tools))
    return found


def main() -> int:
    offline = "--offline" in sys.argv
    real = None if offline else tools_from_container()
    if real is None:
        real = KNOWN_TOOLS
        print(f"[check] 用离线清单({len(real)} 个工具)"
              + ("" if offline else " —— 容器没起或读取失败"))
    else:
        print(f"[check] 从容器读到 {len(real)} 个工具")

    bad = []
    total = 0
    for key, tools in declared_tools():
        total += 1
        for t in tools:
            if t not in real:
                bad.append((key, t))

    print(f"[check] 检查了 {total} 个内置 SKILL")
    if bad:
        print(f"\n❌ {len(bad)} 处引用了不存在的工具:")
        for key, t in bad:
            print(f"   SKILL {key:22} → {t}")
        print("\n只能填本部署实际存在的工具名。现有清单:")
        for t in sorted(real):
            print(f"   {t}")
        return 1

    print("✅ 全部 tools 引用合法")
    return 0


if __name__ == "__main__":
    sys.exit(main())
