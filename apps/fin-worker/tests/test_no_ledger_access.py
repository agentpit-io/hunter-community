"""源码守卫 · **fin-worker 不许直连账本库**（`01方案 §7.4` / §11.3）。

`08 §3.3`：「`fin-worker` 只能通过 `paper` 的 HTTP 接口改账本，**不直连
`hunter_fin` 写**」。这条不能靠 review 记住 —— 于是写成一条扫源码的用例：

1. 源码里不许 import 任何 DB 驱动；
2. 不许出现 `DATABASE_URL` / `PAPER_DATABASE_URL` 之类连接串变量名；
3. 不许出现对 `fin_*` 表的写 SQL（`INSERT INTO fin_` / `UPDATE fin_` / `DELETE FROM`）；
4. 依赖清单里不许有 DB 驱动；
5. 配置模块里不许有 `postgresql://` 连接串。

守卫只扫**代码**（用 `tokenize` 去掉注释与字符串字面量），否则「这里不许出现
psycopg2」这句说明本身就会被判违规 —— 那种守卫只能靠删文档来变绿。

以后有人「顺手」加一条直连账本库的捷径，这里会当场红。
"""

from __future__ import annotations

import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]      # apps/fin-worker
APP = ROOT / "app"

DB_DRIVERS = ("psycopg2", "psycopg", "asyncpg", "sqlalchemy", "pymysql",
              "aiomysql", "sqlite3", "MySQLdb", "cx_Oracle")

CONN_VARS = ("DATABASE_URL", "FIN_DATABASE_URL", "PAPER_DATABASE_URL", "DB_DSN")

WRITE_SQL = re.compile(r"(?i)\b(insert\s+into|update|delete\s+from)\s+fin_[a-z_]+")

_SKIP = {tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE,
         tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING}


def _py_files() -> list[Path]:
    return sorted(p for p in APP.rglob("*.py"))


def code_only(path: Path) -> str:
    """去掉注释与字符串字面量后的源码。"""
    parts: list[str] = []
    with path.open("rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type not in _SKIP:
                parts.append(tok.string)
    return " ".join(parts)


def test_no_db_driver_imports():
    hits = []
    for path in _py_files():
        code = code_only(path)
        for drv in DB_DRIVERS:
            if re.search(rf"(?m)(^|\s)(import|from)\s+{re.escape(drv)}\b", code):
                hits.append(f"{path.relative_to(ROOT)}: imports {drv}")
    assert not hits, "fin-worker 不许 import 数据库驱动：\n" + "\n".join(hits)


def test_no_connection_string_env_vars():
    hits = []
    for path in _py_files():
        code = code_only(path)
        for var in CONN_VARS:
            if var in code:
                hits.append(f"{path.relative_to(ROOT)}: mentions {var}")
    assert not hits, "fin-worker 不许有账本库连接串：\n" + "\n".join(hits)


def test_no_write_sql_against_ledger_tables():
    hits = []
    for path in _py_files():
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.split("#", 1)[0]
            if WRITE_SQL.search(stripped):
                hits.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not hits, "fin-worker 不许直接写账本表：\n" + "\n".join(hits)


def test_requirements_have_no_db_driver():
    lines = []
    for raw in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        lines.append(raw.split("#", 1)[0])
    text = "\n".join(lines).lower()
    for drv in DB_DRIVERS:
        assert drv.lower() not in text, f"依赖里不该有 {drv}"


def test_config_has_no_postgres_uri():
    text = code_only(APP / "config.py")
    assert "postgresql://" not in text
    assert "postgres://" not in text
