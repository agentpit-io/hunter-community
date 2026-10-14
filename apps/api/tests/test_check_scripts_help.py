"""scripts/check_*.py 的 `--help`(issue #9)。

六个自检脚本原来只认 `"--xxx" in sys.argv`,`--help` 被当成没带参数,
直接跑一遍完整检查(check_skill_sync 还会去连 opencode)。这里逐个确认:
`--help` 退出码 0,只打用法(含前置条件与现有参数),不跑检查。

跑法::

    cd apps/api && python -m pytest tests/test_check_scripts_help.py -q
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"

# 脚本 → 它现有的参数(help 里必须列出来)
_CASES = {
    "check_skill_sync.py": ["--fix"],
    "check_skill_tools.py": ["--offline"],
    "check_source_upstream.py": [],
    "check_sources.py": ["--probe"],
    "check_tool_prompts.py": [],
    "check_tools.py": ["--live"],
}


def test_every_check_script_is_covered():
    # 新加的 check_*.py 也要有 --help:漏登记在这里就红
    assert sorted(p.name for p in _SCRIPTS.glob("check_*.py")) == sorted(_CASES)


@pytest.mark.parametrize("name", sorted(_CASES))
def test_help(name):
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / name), "--help"],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        env=dict(os.environ, PYTHONUTF8="1"),
    )
    out = proc.stdout
    assert proc.returncode == 0, out + proc.stderr
    assert out.startswith("usage:"), out[:300]    # 只打帮助,不跑检查
    assert "前置条件" in out
    for flag in _CASES[name]:
        assert flag in out
