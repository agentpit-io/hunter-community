#!/usr/bin/env python3
"""比对「磁盘上有几个 SKILL」与「opencode 真的认到几个」。

**为什么需要这个**:opencode 只在**启动时**扫一次 skill 目录,之后缓存住
(源码 `skill/index.ts` 用 `InstanceState.make`,缓存键是工作目录)。
文件改了但没重启 opencode,就会出现:

    侧栏(读我们自己的 API)  23 个
    模型手上(opencode)      29 个 ← 含 6 个已经删掉的

2026-08-15 就真的发生了,而且**存在了几个小时没人发现** ——
因为在这之前,没有任何东西在比对这两个数。

实测过的三件事(写在这里,免得下一个人再试一遍):
  · 文件丢进挂载目录 → 容器内 `ls` 立刻可见,但 opencode 认不到
  · `POST /instance/dispose` → 返回 200 true,**skill 列表纹丝不动**
  · 重启容器 → 有效,约 52 秒

用法:
    python scripts/check_skill_sync.py            # 本机跑,自动读 .env 里的凭证
    python scripts/check_skill_sync.py --fix      # 不一致就自动重启 opencode 并复查

退出码:0 = 一致 · 1 = 不一致(CI 里应当失败)
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):      # 中文 Windows 控制台默认 GBK
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO / "skills"
USER_SKILLS_DIR = REPO / "user-skills"

# opencode 自带的 skill,不在我们的目录里 —— 比对时要排除,
# 否则永远差 1 个,变成一个天天报警的假阳性
BUILTIN_LOCATION = "<built-in>"


def env(key: str, default: str = "") -> str:
    """从仓库根的 .env 读一个值(不覆盖已有的进程环境变量)。"""
    if os.getenv(key):
        return os.environ[key]
    f = REPO / ".env"
    if not f.is_file():
        return default
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(rf"^{re.escape(key)}=(.*)$", line.strip())
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return default


def disk_skills() -> set[str]:
    """磁盘上的 skill 名 —— 用 frontmatter 的 name,没有就用目录名。

    与 opencode 保持同一口径:它也是读 frontmatter 的 name。
    用目录名比对会在两者不一致时误报。
    """
    out: set[str] = set()
    for base in (SKILLS_DIR, USER_SKILLS_DIR):
        if not base.is_dir():
            continue
        for f in base.glob("*/SKILL.md"):
            fm = (re.match(r"^---\s*\n(.*?)\n---", f.read_text(encoding="utf-8"), re.S)
                  or ["", ""])[1]
            m = re.search(r"^name:\s*(.+)$", fm, re.M)
            out.add((m.group(1).strip().strip('"') if m else f.parent.name))
    return out


def opencode_skills() -> tuple[set[str], str] | tuple[None, str]:
    """问 opencode 它认到哪些(排除它自带的)。失败返回 (None, 原因)。"""
    user, pw = env("OPENCODE_USER"), env("OPENCODE_PASS")
    url = env("OPENCODE_PROBE_URL", "http://127.0.0.1:3921") + "/skill"
    req = urllib.request.Request(url)
    if user or pw:
        req.add_header("Authorization", "Basic " +
                       base64.b64encode(f"{user}:{pw}".encode()).decode())
    try:
        # localhost 不该走代理 —— Clash 之类会把它劫走然后回 502。
        # 这个坑今天踩过一次,所以显式装一个不用代理的 opener。
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=20) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}(凭证不对?检查 .env 里的 OPENCODE_USER/PASS)"
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:80]}(opencode 没起?)"
    return {x["name"] for x in data if x.get("location") != BUILTIN_LOCATION}, ""


def restart_opencode() -> bool:
    print("  重启 opencode(约 50 秒)...")
    try:
        subprocess.run(["docker", "compose", "restart", "opencode"],
                       cwd=REPO, capture_output=True, timeout=180)
    except Exception as e:
        print(f"  ✗ 重启失败: {e}")
        return False
    for _ in range(40):
        time.sleep(3)
        got, _err = opencode_skills()
        if got is not None:
            return True
    print("  ✗ 重启后 120 秒仍连不上")
    return False


def compare() -> int:
    disk = disk_skills()
    got, err = opencode_skills()
    print(f"  磁盘:     {len(disk)} 个")
    if got is None:
        print(f"  opencode: 读取失败 —— {err}")
        # 读不到不算通过。校验脚本自己"读不到就当没事"是最糟的形态,
        # 今天已经在 check_skill_tools 上踩过一次(数据源迁走后它静默坏了)。
        return 1
    print(f"  opencode: {len(got)} 个(已排除它自带的 {BUILTIN_LOCATION})")

    only_disk = sorted(disk - got)
    only_oc = sorted(got - disk)
    if not only_disk and not only_oc:
        print("\n✅ 一致")
        return 0

    print()
    if only_disk:
        print(f"  ✗ 磁盘有、opencode 不认({len(only_disk)} 个)—— **新加的没生效**:")
        for n in only_disk:
            print(f"      {n}")
    if only_oc:
        print(f"  ✗ opencode 还认、磁盘已删({len(only_oc)} 个)—— **模型手上有幽灵 SKILL**:")
        for n in only_oc:
            print(f"      {n}")
    print("\n  修复:docker compose restart opencode(或本脚本加 --fix)")
    return 1


def main() -> int:
    print("比对 SKILL 同步状态(磁盘 ↔ opencode)")
    rc = compare()
    if rc and "--fix" in sys.argv:
        print()
        if restart_opencode():
            print("\n重启后复查:")
            rc = compare()
    return rc


if __name__ == "__main__":
    sys.exit(main())
