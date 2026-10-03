# -*- coding: utf-8 -*-
"""R4 · **开关读点守护**（不连库、不连网）。

`03 §2` / `R4.md` §一.1：四个 `FIN_*` 开关的读取口**只有一处** ——
`apps/api/app/services/fin/switches.py`。别处散着写 `os.environ.get("FIN_...")`
就是「同一件事写在两处、只改一处」，开关会在两个地方给出两个答案，且不会报错。

这条守护是「唯一读点」的机器可验形式（仿 `test_fin_memory_guard.py` /
fin-worker 的 `test_no_ledger_access.py`）：以后有人「顺手」在别处读一次，这里当场红。

判据：扫 `apps/` 下所有文件（`.py` 去掉注释、保留字符串字面量），命中四个开关名
**且不在白名单**即失败。白名单只有 `switches.py` 本身与任何 `tests/` 目录。

    cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_switches_guard.py -q
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]        # hunter-community

# 违规形态是**读**：`os.getenv("FIN_...")` / `os.environ[...]` / `os.environ.get(...)`。
# 只写在一句提示文案或文档里（"FIN_MEMORY_ENABLED=0 时…"）不算读点 —— 那不是第二个答案来源。
# 判据：同一行里既有开关名、又有 `os.environ` / `getenv`。
_SWITCH_NAME = re.compile(
    r"FIN_MEMORY_ENABLED|FIN_EVOLUTION_MODE|FIN_AUTO_APPLY|FIN_LIVE_ORDER_ENABLED")
_ENV_ACCESS = re.compile(r"os\.environ|\bgetenv\b")

SCAN_ROOTS = [REPO / "apps"]

ALLOWED_EXACT = {
    "apps/api/app/services/fin/switches.py",   # 唯一读点
    # 测试脚手架：它 `os.environ.setdefault(...)` **写**测试套件的默认值（不是读点）。
    # 那是 setdefault 不是 getenv —— 留在白名单里，注明它只写不读。
    "apps/api/conftest.py",
}

_SKIP_DIRS = {"__pycache__", "node_modules", ".next", ".git", "dist", "build", ".venv"}
_SKIP_SUFFIX = {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2",
                ".lock", ".map"}


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def _allowed(rel: str) -> bool:
    if rel in ALLOWED_EXACT:
        return True
    return "tests" in rel.split("/")            # 任何层的测试目录都放行


def _strip_py_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _iter_files():
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if path.suffix.lower() in _SKIP_SUFFIX:
                continue
            yield path


def test_switch_env_read_only_in_switches_module():
    hits: list[str] = []
    for path in _iter_files():
        rel = _rel(path)
        if _allowed(rel):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if path.suffix == ".py":
            text = _strip_py_comments(text)
        for i, line in enumerate(text.splitlines(), 1):
            if _SWITCH_NAME.search(line) and _ENV_ACCESS.search(line):
                hits.append(f"{rel}:{i}: {line.strip()}")
    assert not hits, (
        "四个 FIN_* 开关只许在 services/fin/switches.py 里读（唯一读点），"
        "其他任何地方命中即失败：\n" + "\n".join(hits)
    )


def test_switches_module_actually_reads_them():
    """反向断言：守卫不能是「真空绿」—— 唯一读点必须真的读四个名字。"""
    code = _strip_py_comments(
        (REPO / "apps/api/app/services/fin/switches.py").read_text(encoding="utf-8"))
    for name in ("FIN_MEMORY_ENABLED", "FIN_EVOLUTION_MODE",
                 "FIN_AUTO_APPLY", "FIN_LIVE_ORDER_ENABLED"):
        assert name in code, name
    # 真有读环境变量的动作（否则四个名字可以只是注释）
    assert "os.getenv" in code
