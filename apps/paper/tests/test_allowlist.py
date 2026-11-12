"""L06 · 执行允许名单 —— 纯逻辑用例（假 cursor，不连库）。

判据的**唯一实现**是 `app/allowlist.py`；这里逐条覆盖「默认拒绝 / 登记放行 /
维度附加约束 / 撤销 / 通配」。真库上的端到端（空名单下单被拒 + 登记后放行）
在成果文档里另贴（需要 `PAPER_TEST_DSN`）。
"""

from __future__ import annotations

import pytest

from app import allowlist


class FakeCur:
    """够用的假 cursor：`fin_exec_allowance` 一张表，支持 SELECT / INSERT。"""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self._result: list[dict] = []
        self._one: dict | None = None
        self._next_id = max([r["allowance_id"] for r in self.rows], default=0) + 1

    def execute(self, sql: str, params=None):
        s = " ".join(sql.split()).lower()
        self._one = None
        if s.startswith("insert"):
            scope, subject, status, by, note = params
            row = {"allowance_id": self._next_id, "scope": scope, "subject": subject,
                   "status": status, "granted_by": by, "granted_at": "2026-10-05T00:00:00+08:00",
                   "note": note}
            self._next_id += 1
            self.rows.append(row)
            self._one = dict(row)
            return
        if "from fin_exec_allowance" in s and "limit" in s:  # history
            self._result = sorted(self.rows, key=lambda r: -r["allowance_id"])[: params[0]]
        elif "from fin_exec_allowance" in s:
            self._result = sorted(self.rows, key=lambda r: r["allowance_id"])
        else:  # pragma: no cover - 不该有别的语句
            raise AssertionError(f"未预期的 SQL：{sql}")

    def fetchall(self):
        return list(self._result)

    def fetchone(self):
        return dict(self._one) if self._one else None


def _row(i, scope, subject, status="active"):
    return {"allowance_id": i, "scope": scope, "subject": subject, "status": status,
            "granted_by": "tester", "granted_at": None, "note": None}


# ── 默认拒绝 ───────────────────────────────────────────────────────────────

def test_empty_allowlist_denies_any_order():
    cur = FakeCur([])
    with pytest.raises(allowlist.AllowanceDenied) as ei:
        allowlist.check(cur, project_id="prj_1", code="600519", tool="place_order")
    assert "不允许名单" in str(ei.value) or "不在执行允许名单" in str(ei.value)


def test_count_active_zero_when_empty():
    assert allowlist.count_active(FakeCur([])) == 0


# ── 项目维度是基础闸门 ─────────────────────────────────────────────────────

def test_project_registered_allows_order():
    cur = FakeCur([_row(1, "project", "prj_1")])
    # 标的 / 工具维度空 → 不额外限制。
    assert allowlist.check(cur, project_id="prj_1", code="600519",
                           tool="place_order") is None
    assert allowlist.count_active(cur) == 1


def test_other_project_still_denied():
    cur = FakeCur([_row(1, "project", "prj_1")])
    with pytest.raises(allowlist.AllowanceDenied):
        allowlist.check(cur, project_id="prj_2", code="600519", tool="place_order")


def test_project_wildcard_is_explicit_registration():
    cur = FakeCur([_row(1, "project", "*")])
    assert allowlist.check(cur, project_id="anything", code="X", tool="place_order") is None


# ── 标的维度：有人登记过才生效 ─────────────────────────────────────────────

def test_instrument_scope_narrows_when_present():
    cur = FakeCur([_row(1, "project", "prj_1"), _row(2, "instrument", "600519")])
    assert allowlist.check(cur, project_id="prj_1", code="600519", tool="place_order") is None
    with pytest.raises(allowlist.AllowanceDenied) as ei:
        allowlist.check(cur, project_id="prj_1", code="000001", tool="place_order")
    assert ei.value.scope == "instrument"


def test_instrument_wildcard_allows_all_codes():
    cur = FakeCur([_row(1, "project", "prj_1"), _row(2, "instrument", "*")])
    assert allowlist.check(cur, project_id="prj_1", code="000001", tool="place_order") is None


# ── 工具维度 ───────────────────────────────────────────────────────────────

def test_tool_scope_enforced_when_present():
    cur = FakeCur([_row(1, "project", "prj_1"), _row(2, "tool", "place_order")])
    assert allowlist.check(cur, project_id="prj_1", tool="place_order") is None
    with pytest.raises(allowlist.AllowanceDenied) as ei:
        allowlist.check(cur, project_id="prj_1", tool="cancel_order")
    assert ei.value.scope == "tool"


# ── 登记 / 撤销（只追加，最新一行说了算）───────────────────────────────────

def test_register_then_revoke_flips_effective_state():
    cur = FakeCur([])
    allowlist.register(cur, scope="project", subject="prj_1", granted_by="alice",
                       note="首登")
    assert allowlist.count_active(cur) == 1
    allowlist.check(cur, project_id="prj_1", tool="place_order")  # 放行

    allowlist.revoke(cur, scope="project", subject="prj_1", granted_by="alice",
                     note="撤销")
    # 只追加：两行都在，但现行状态是 revoked → 拒绝。
    assert len(cur.rows) == 2
    assert allowlist.count_active(cur) == 0
    with pytest.raises(allowlist.AllowanceDenied):
        allowlist.check(cur, project_id="prj_1", tool="place_order")

    # 再登记 → 又放行（撤销不是终局）。
    allowlist.register(cur, scope="project", subject="prj_1", granted_by="bob")
    allowlist.check(cur, project_id="prj_1", tool="place_order")
    assert len(cur.rows) == 3


def test_register_validates_inputs():
    cur = FakeCur([])
    with pytest.raises(ValueError):
        allowlist.register(cur, scope="nope", subject="x", granted_by="a")
    with pytest.raises(ValueError):
        allowlist.register(cur, scope="project", subject="", granted_by="a")
    with pytest.raises(ValueError):
        allowlist.register(cur, scope="project", subject="x", granted_by="")


def test_history_is_newest_first():
    cur = FakeCur([])
    allowlist.register(cur, scope="project", subject="p1", granted_by="a")
    allowlist.register(cur, scope="tool", subject="place_order", granted_by="a")
    hist = allowlist.history(cur, limit=10)
    assert [r["scope"] for r in hist] == ["tool", "project"]
