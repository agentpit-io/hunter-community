# -*- coding: utf-8 -*-
"""R2 · **唯一入口守护**（不连库、不连网）。

方案 §七 第 10 条：全仓 `grep -rn "fin_experience" apps/ db/` **只许**命中
`services/fin/memory.py` / `routers/fin_memory.py` / 迁移 / 测试 ——
**其他任何地方命中即失败**。

这条守护是「不建第二套记忆 / 唯一入口」的机器可验形式（仿
`apps/fin-worker/tests/test_no_ledger_access.py`）：以后有人「顺手」在别处
直接查这三张表，这里会当场红。

判据说明（为什么不是逐字节 grep）：
  · **Python 文件去掉注释后再扫**（保留字符串字面量）。理由与 fin-worker 那条一样 ——
    否则「这里不许出现 fin_experience」这句说明本身就会判违规，守卫只能靠删文档变绿。
    字符串**保留**是因为真正的违规形态就是 `cur.execute("SELECT … FROM fin_experience")`，
    那必须被抓到。
  · 非 Python 文件（`.sql` / `.ts` / …）整文件扫 —— 没有注释语法可依赖，宁可严一点。

    cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_memory_guard.py -q
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]        # hunter-community

# 三张表的名字。`fin_experience` 是 `fin_experience_evidence` 的前缀，一条正则全覆盖。
TABLE_RE = re.compile(r"fin_experience|fin_memory_snapshot")

SCAN_ROOTS = [REPO / "apps", REPO / "db"]

# 允许命中的路径（相对仓库根）—— 唯一入口本身 + 迁移 + 测试。
ALLOWED_EXACT = {
    "apps/api/app/services/fin/memory.py",     # 唯一服务模块
    "apps/api/app/routers/fin_memory.py",      # 唯一路由（本轮它只调服务，不留表名）
}
# 迁移目录与任何 tests/ 目录一律放行。
_ALLOWED_DIR_PARTS = (("db", "migrations"),)
_SKIP_DIRS = {"__pycache__", "node_modules", ".next", ".git", "dist", "build"}

_SKIP_SUFFIX = {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2",
                ".lock", ".map"}


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def _allowed(rel: str) -> bool:
    if rel in ALLOWED_EXACT:
        return True
    parts = rel.split("/")
    if "tests" in parts:                       # 任何层的测试目录都放行
        return True
    for prefix in _ALLOWED_DIR_PARTS:          # db/migrations
        if parts[:len(prefix)] == list(prefix):
            return True
    return False


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


def _strip_py_comments(text: str) -> str:
    """极简去注释：逐行砍掉 `#` 之后的内容（字符串里的 `#` 也会被砍 —— 与 fin-worker 同口径，
    宁可漏检一行也不放过整文件；真正的违规是 `FROM fin_experience` 那类，不受影响）。"""
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _scan_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix == ".py":
        return _strip_py_comments(text)
    return text


def test_experience_tables_referenced_only_by_the_single_entry():
    hits: list[str] = []
    for path in _iter_files():
        rel = _rel(path)
        if _allowed(rel):
            continue
        for i, line in enumerate(_scan_text(path).splitlines(), 1):
            if TABLE_RE.search(line):
                hits.append(f"{rel}:{i}: {line.strip()}")
    assert not hits, (
        "经验三表只许出现在 services/fin/memory.py · routers/fin_memory.py · 迁移 · 测试，"
        "其他任何地方命中即失败（唯一入口 / 不建第二套记忆）：\n" + "\n".join(hits)
    )


def test_single_entry_files_actually_reference_the_tables():
    """反向断言：守卫不能是「真空绿」—— 唯一服务模块必须真的引用这三张表。"""
    service = REPO / "apps/api/app/services/fin/memory.py"
    code = _strip_py_comments(service.read_text(encoding="utf-8"))
    assert "fin_experience" in code
    assert "fin_experience_evidence" in code
    assert "fin_memory_snapshot" in code


def test_migration_is_the_only_schema_definition():
    """三张表只在 0041 迁移里建。"""
    sql_files = sorted((REPO / "db" / "migrations").glob("*.sql"))
    creators = [p.name for p in sql_files
                if re.search(r"CREATE TABLE IF NOT EXISTS fin_experience", p.read_text(encoding="utf-8"))]
    assert creators == ["0041_memory_core.sql"], creators
