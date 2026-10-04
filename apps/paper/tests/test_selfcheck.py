"""启动自检：模式 / 口令 / 权限边界。"""

from __future__ import annotations

import os

import psycopg2
import pytest

from app import config, selfcheck


class _BoomConn:
    """一碰就炸的连接：用来在不起库的情况下测「前两关」。"""

    def cursor(self):
        raise psycopg2.OperationalError("no database")


def test_wrong_mode_is_a_problem(monkeypatch):
    monkeypatch.setenv("PAPER_MODE", "LIVE")
    monkeypatch.setenv("HUNTER_INTERNAL_KEY", "k")
    problems = selfcheck.check(_BoomConn())
    assert any("PAPER_MODE" in p for p in problems)


def test_empty_internal_key_is_a_problem(monkeypatch):
    monkeypatch.setenv("PAPER_MODE", "PAPER")
    monkeypatch.delenv("HUNTER_INTERNAL_KEY", raising=False)
    problems = selfcheck.check(_BoomConn())
    assert any("HUNTER_INTERNAL_KEY" in p for p in problems)


def test_empty_exec_key_is_a_problem(monkeypatch):
    """L06 · **缺任一钥匙都拒绝启动**：只有读取凭证、没有执行凭证也不行。"""
    monkeypatch.setenv("PAPER_MODE", "PAPER")
    monkeypatch.setenv("HUNTER_INTERNAL_KEY", "k")
    monkeypatch.delenv("HUNTER_EXEC_KEY", raising=False)
    problems = selfcheck.check(_BoomConn())
    assert any("HUNTER_EXEC_KEY" in p for p in problems)


def test_unreachable_db_is_a_problem(monkeypatch):
    monkeypatch.setenv("PAPER_MODE", "PAPER")
    monkeypatch.setenv("HUNTER_INTERNAL_KEY", "k")
    problems = selfcheck.check(_BoomConn())
    assert any("连接账本库失败" in p for p in problems)


def _dsn(name: str) -> str:
    return (os.getenv(name) or "").strip()


@pytest.mark.skipif(not _dsn("PAPER_TEST_DSN"), reason="未设置 PAPER_TEST_DSN")
def test_runtime_role_passes(monkeypatch):
    """以运行期角色（只增不改）连库 → 自检通过。"""
    monkeypatch.setenv("PAPER_MODE", "PAPER")
    monkeypatch.setenv("HUNTER_INTERNAL_KEY", "k")
    monkeypatch.setenv("PAPER_DATABASE_URL", _dsn("PAPER_TEST_DSN"))
    conn = psycopg2.connect(_dsn("PAPER_TEST_DSN"))
    try:
        assert selfcheck.check(conn) == []
    finally:
        conn.close()


@pytest.mark.skipif(not _dsn("PAPER_TEST_ADMIN_DSN"), reason="未设置 PAPER_TEST_ADMIN_DSN")
def test_admin_connection_refused(monkeypatch):
    """被指到管理员 / 属主连接（对追加表有 UPDATE）→ 自检拒绝启动。"""
    monkeypatch.setenv("PAPER_MODE", "PAPER")
    monkeypatch.setenv("HUNTER_INTERNAL_KEY", "k")
    monkeypatch.setenv("PAPER_DATABASE_URL", _dsn("PAPER_TEST_ADMIN_DSN"))
    conn = psycopg2.connect(_dsn("PAPER_TEST_ADMIN_DSN"))
    try:
        problems = selfcheck.check(conn)
    finally:
        conn.close()
    assert any("UPDATE" in p and "fin_cash_ledger" in p for p in problems)


def test_main_exits_nonzero_on_bad_mode(monkeypatch):
    monkeypatch.setenv("PAPER_MODE", "LIVE")
    monkeypatch.setenv("PAPER_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
    monkeypatch.setenv("HUNTER_INTERNAL_KEY", "k")
    assert selfcheck.main() == 1
