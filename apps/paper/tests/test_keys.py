"""L06 · 两把钥匙分开 —— 证据用例。

**不读密钥值**（本文件里的都是占位串），只证明**来源不同**：

- `config.read_key()` 与 `config.exec_key()` 取自**两个不同的环境变量**；
- **行情读取凭证打不开执行门**（`security.require_internal_key` 校验的是执行凭证）；
- 执行门认的是执行凭证（认 read 会 401，认 exec 才放行）；
- 行情来源（`snapshot/source.py`）带的是**读取凭证**，与执行凭证无关。
"""

from __future__ import annotations

from app import config
from app.snapshot.source import HttpQuoteSource


# ── 来源不同（变量名，不读值）──────────────────────────────────────────────

def test_two_keys_from_different_env_vars():
    assert config.READ_KEY_ENV == "HUNTER_INTERNAL_KEY"
    assert config.EXEC_KEY_ENV == "HUNTER_EXEC_KEY"
    assert config.READ_KEY_ENV != config.EXEC_KEY_ENV, "两把钥匙必须取自不同的环境变量"


def test_read_and_exec_read_their_own_variables(monkeypatch):
    monkeypatch.setenv("HUNTER_INTERNAL_KEY", "read-placeholder")
    monkeypatch.setenv("HUNTER_EXEC_KEY", "exec-placeholder")
    assert config.read_key() == "read-placeholder"
    assert config.exec_key() == "exec-placeholder"
    # 改一把不影响另一把 —— 两个取值来自两个变量。
    monkeypatch.setenv("HUNTER_INTERNAL_KEY", "read-changed")
    assert config.read_key() == "read-changed"
    assert config.exec_key() == "exec-placeholder"


def test_no_default_value(monkeypatch):
    """没配就是空串（没有兜底默认值），交给启动自检拒绝。"""
    monkeypatch.delenv("HUNTER_INTERNAL_KEY", raising=False)
    monkeypatch.delenv("HUNTER_EXEC_KEY", raising=False)
    assert config.read_key() == ""
    assert config.exec_key() == ""


# ── 执行门只认执行凭证 ─────────────────────────────────────────────────────

class _Req:
    """`require_internal_key` 只用到 `url.path` 与 `headers.get`，给个鸭子类型即可。"""

    def __init__(self, path: str, header_value: str | None):
        self.url = type("U", (), {"path": path})()
        self.headers = {} if header_value is None else {"X-Hunter-Internal-Key": header_value}


def test_exec_gate_accepts_exec_key_rejects_read_key(monkeypatch):
    from fastapi import HTTPException

    from app.security import require_internal_key

    monkeypatch.setenv("HUNTER_INTERNAL_KEY", "read-aaa")
    monkeypatch.setenv("HUNTER_EXEC_KEY", "exec-bbb")

    # 拿「行情读取凭证」下单 → 执行门拒绝（这是「两把钥匙分开」的核心断言）。
    try:
        require_internal_key(_Req("/api/v1/orders", "read-aaa"))
        raise AssertionError("读取凭证不应能通过执行门")
    except HTTPException as exc:
        assert exc.status_code == 401

    # 拿「执行凭证」→ 通过。
    assert require_internal_key(_Req("/api/v1/orders", "exec-bbb")) is None

    # /healthz 仍然豁免（容器健康检查拿不到密钥）。
    assert require_internal_key(_Req("/healthz", None)) is None


# ── 行情来源带的是读取凭证 ─────────────────────────────────────────────────

def test_quote_source_uses_read_key(monkeypatch):
    monkeypatch.setenv("HUNTER_INTERNAL_KEY", "read-ccc")
    monkeypatch.setenv("HUNTER_EXEC_KEY", "exec-ddd")
    src = HttpQuoteSource("http://api.test", read_key=None)
    assert src._key == "read-ccc", "行情来源必须带读取凭证，不是执行凭证"

    # 显式覆盖参数仍然生效（测试注入用）。
    assert HttpQuoteSource("http://api.test", read_key="x")._key == "x"


def test_quote_source_ignores_exec_key(monkeypatch):
    monkeypatch.setenv("HUNTER_INTERNAL_KEY", "read-ccc")
    monkeypatch.setenv("HUNTER_EXEC_KEY", "exec-ddd")
    # 把执行凭证换掉，行情来源不受影响 —— 它不读那个变量。
    monkeypatch.setenv("HUNTER_EXEC_KEY", "other")
    assert HttpQuoteSource("http://api.test")._key == "read-ccc"
