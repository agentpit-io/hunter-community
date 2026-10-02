"""「账本只追加」的机器可验形式。

两道：

1. **源码扫描** —— `apps/paper/app/**` 里不许出现对追加表的 `UPDATE` / 任何
   `DELETE FROM fin_*`，也不许出现 `float(`（金额全程 Decimal）。
2. **路由扫描** —— 路由方法只允许 GET / POST / PUT（PUT 只用在参考数据的 upsert 上），
   没有任何 DELETE 端点。

前科（`CLAUDE.md` 开头）：靠 review 看不住「顺手 UPDATE 了一笔成交」这类改动，
必须有一条断言盯着。
"""

from __future__ import annotations

import os
import re

APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")

_APPEND_ONLY_UPDATE = re.compile(
    r"UPDATE\s+fin_(trade|cash_ledger|snapshot|valuation|recon_log|param_change_log|human_action_log)\b",
    re.IGNORECASE,
)
_ANY_DELETE = re.compile(r"DELETE\s+FROM\s+fin_", re.IGNORECASE)
_FLOAT_CALL = re.compile(r"\bfloat\s*\(")


def _py_files():
    for root, _dirs, files in os.walk(APP_DIR):
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(root, name)


def test_no_update_on_append_only_tables():
    offenders = []
    for path in _py_files():
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for m in _APPEND_ONLY_UPDATE.finditer(src):
            offenders.append(f"{os.path.relpath(path, APP_DIR)}: {m.group(0)}")
    assert offenders == [], f"追加表上出现了 UPDATE：{offenders}"


def test_no_delete_from_fin_tables():
    offenders = []
    for path in _py_files():
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for m in _ANY_DELETE.finditer(src):
            offenders.append(f"{os.path.relpath(path, APP_DIR)}: {m.group(0)}")
    assert offenders == [], f"出现了 DELETE FROM fin_*：{offenders}"


def test_no_float_cast_in_paper_code():
    offenders = []
    for path in _py_files():
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for m in _FLOAT_CALL.finditer(src):
            offenders.append(os.path.relpath(path, APP_DIR))
    assert offenders == [], f"金额代码里出现了 float(：{sorted(set(offenders))}"


def test_routes_have_no_delete_and_no_mutating_ledger_paths():
    os.environ.setdefault("HUNTER_INTERNAL_KEY", "test-internal-key")
    os.environ.setdefault("PAPER_MODE", "PAPER")
    from app.main import app

    allowed = {"GET", "POST", "PUT", "HEAD", "OPTIONS"}
    offenders = []
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        for method in methods:
            if method not in allowed:
                offenders.append(f"{method} {route.path}")
    assert offenders == [], f"出现了不该有的方法：{offenders}"

    # 没有任何端点指向「改一笔成交 / 删一条流水」
    bad_paths = [r.path for r in app.routes
                 if getattr(r, "path", "").endswith(("/trades/{trade_id}", "/cash/{entry_id}"))]
    assert bad_paths == []
