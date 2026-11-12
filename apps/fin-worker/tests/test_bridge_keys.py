"""L06 · 两个客户端各用各的钥匙（证据用例，不读密钥值）。
L11 补：`HUNTER_EXEC_KEY` 没配时 `config.exec_key()` 回退到读取凭证（老部署平滑升级）。
"""

from __future__ import annotations

from app import config
from app.bridge.hunter_api import HunterApiClient
from app.bridge.paper import PaperClient


def test_two_keys_from_different_env_vars():
    assert config.READ_KEY_ENV == "HUNTER_INTERNAL_KEY"
    assert config.EXEC_KEY_ENV == "HUNTER_EXEC_KEY"
    assert config.READ_KEY_ENV != config.EXEC_KEY_ENV


def test_missing_exec_key_falls_back_to_read_key(monkeypatch):
    """L11 · 没配 `HUNTER_EXEC_KEY` → `exec_key() == read_key()`。"""
    monkeypatch.setenv("HUNTER_INTERNAL_KEY", "read-only")
    monkeypatch.delenv("HUNTER_EXEC_KEY", raising=False)
    assert config.exec_key() == "read-only"


def test_configured_exec_key_is_not_overridden_by_fallback(monkeypatch):
    """L11 · 配了 `HUNTER_EXEC_KEY` → 用它自己，**不被回退盖掉**。"""
    monkeypatch.setenv("HUNTER_INTERNAL_KEY", "read-x")
    monkeypatch.setenv("HUNTER_EXEC_KEY", "exec-y")
    assert config.exec_key() == "exec-y"


def test_paper_client_uses_exec_key(monkeypatch):
    monkeypatch.setenv("HUNTER_INTERNAL_KEY", "read-rrr")
    monkeypatch.setenv("HUNTER_EXEC_KEY", "exec-eee")
    client = PaperClient(base_url="http://paper.test")
    assert client._key == "exec-eee", "打 paper 下单必须带执行凭证"
    assert client._headers()["X-Hunter-Internal-Key"] == "exec-eee"


def test_hunter_api_client_uses_read_key(monkeypatch):
    monkeypatch.setenv("HUNTER_INTERNAL_KEY", "read-rrr")
    monkeypatch.setenv("HUNTER_EXEC_KEY", "exec-eee")
    client = HunterApiClient(base_url="http://api.test")
    assert client._key == "read-rrr", "打 api 数据面必须带读取凭证"
    assert client._headers()["X-Hunter-Internal-Key"] == "read-rrr"
