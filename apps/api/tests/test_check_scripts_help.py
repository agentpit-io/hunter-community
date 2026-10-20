"""scripts/check_*.py 的 --help:要能看到用途与前置条件,退出码 0,且不跑检查。

原来 6 个脚本都不认 --help,加上它照样把整套检查跑一遍(check_skill_sync 还会去连
opencode、连不上退出码 1)。这里逐个起子进程跑 --help 盯着。
"""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = (
    "check_skill_sync.py",
    "check_skill_tools.py",
    "check_source_upstream.py",
    "check_sources.py",
    "check_tool_prompts.py",
    "check_tools.py",
)

# 仓库根:逐级往上找 scripts/check_skill_sync.py(容器里挂源码跑时层数不定)
ROOT = next((p for p in Path(__file__).resolve().parents
             if (p / "scripts" / "check_skill_sync.py").is_file()), None)


@pytest.mark.skipif(ROOT is None, reason="找不到仓库根的 scripts/ 目录")
@pytest.mark.parametrize("name", SCRIPTS)
def test_help(name):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / name), "--help"],
                       capture_output=True, text=True, encoding="utf-8",
                       timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert out.startswith(f"usage: {name}"), out[:200]
    assert "前置条件" in out
    assert "退出码" in out
    # 只打帮助,不跑检查:各脚本检查输出的开头一行都不该出现
    for marker in ("比对 SKILL 同步状态", "[check]", "数据源 ", "检查数据源注册表",
                   "工具 ", "检查工具箱注册表"):
        assert not any(line.startswith(marker) for line in out.splitlines()), marker
