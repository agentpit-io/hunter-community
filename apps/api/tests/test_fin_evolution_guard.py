# -*- coding: utf-8 -*-
"""R6 · **提案四表唯一入口守护**（不连库、不连网）。

`R2` 的经验三表守护（`test_fin_memory_guard.py`）的同一套路，扩到 `R6` 的四张进化表：
全仓 `fin_evolution_*` 只许命中**唯一服务模块** / **唯一路由** / **迁移** / **测试** ——
其他任何地方命中即失败。

判据与 `test_fin_memory_guard.py` 逐条一致（Python 去注释后扫、保留字符串；非 Python 整文件扫）。
`R7` / `R8` 加新模块时，把它加进 `ALLOWED_EXACT`（**显式、可 review**），别把守卫改宽。

    cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_evolution_guard.py -q
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]        # hunter-community

# 四张表。用分组正则，避免 `fin_evolution_plan` 之类互相前缀误伤。
TABLE_RE = re.compile(
    r"fin_evolution_(proposal|plan|shadow_event|event)\b")

SCAN_ROOTS = [REPO / "apps", REPO / "db"]

# 允许命中的路径（相对仓库根）—— 唯一入口本身 + 迁移 + 测试。
ALLOWED_EXACT = {
    "apps/api/app/services/fin/evolution.py",   # 唯一服务模块
    "apps/api/app/routers/fin_evolution.py",    # 唯一路由（只调服务，不留表名）
}
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
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _scan_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix == ".py":
        return _strip_py_comments(text)
    return text


def test_evolution_tables_referenced_only_by_the_single_entry():
    hits: list[str] = []
    for path in _iter_files():
        rel = _rel(path)
        if _allowed(rel):
            continue
        for i, line in enumerate(_scan_text(path).splitlines(), 1):
            if TABLE_RE.search(line):
                hits.append(f"{rel}:{i}: {line.strip()}")
    assert not hits, (
        "提案四表只许出现在 services/fin/evolution.py · routers/fin_evolution.py · 迁移 · 测试，"
        "其他任何地方命中即失败（唯一入口）：\n" + "\n".join(hits)
    )


def test_single_entry_module_actually_references_the_tables():
    """反向断言：守卫不能是「真空绿」—— 唯一服务模块必须真的引用它写的四张表。

    `R7` 起 `fin_evolution_shadow_event` 也由本模块写（`R6` 只建表、不写），
    所以它一并纳入反向断言。
    """
    code = _strip_py_comments(
        (REPO / "apps/api/app/services/fin/evolution.py").read_text(encoding="utf-8"))
    for table in ("fin_evolution_proposal", "fin_evolution_plan",
                  "fin_evolution_shadow_event", "fin_evolution_event"):
        assert table in code, table


def test_migration_is_the_only_schema_definition():
    """四张表只在 0043 迁移里建，且四张齐全。"""
    sql_files = sorted((REPO / "db" / "migrations").glob("*.sql"))
    creators = [p.name for p in sql_files
                if re.search(r"CREATE TABLE IF NOT EXISTS fin_evolution_", p.read_text(encoding="utf-8"))]
    assert creators == ["0043_evolution_loop.sql"], creators
    sql = (REPO / "db" / "migrations" / "0043_evolution_loop.sql").read_text(encoding="utf-8")
    for table in ("fin_evolution_proposal", "fin_evolution_plan",
                  "fin_evolution_shadow_event", "fin_evolution_event"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql, table


def test_immutability_triggers_defined_in_migration():
    """不可变触发器必须在迁移里（`BEFORE UPDATE OR DELETE` → 两张表）。"""
    sql = (REPO / "db" / "migrations" / "0043_evolution_loop.sql").read_text(encoding="utf-8")
    assert "BEFORE UPDATE OR DELETE ON fin_evolution_plan" in sql
    assert "BEFORE UPDATE OR DELETE ON fin_evolution_event" in sql
