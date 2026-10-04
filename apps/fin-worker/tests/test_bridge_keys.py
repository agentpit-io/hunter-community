"""L06 · 两个客户端各用各的钥匙（证据用例，不读密钥值）。"""

from __future__ import annotations

from app import config
from app.bridge.hunter_api import HunterApiClient
from app.bridge.paper import PaperClient


def test_two_keys_from_different_env_vars():
    assert config.READ_KEY_ENV == "HUNTER_INTERNAL_KEY"
    assert config.EXEC_KEY_ENV == "HUNTER_EXEC_KEY"
    assert config.READ_KEY_ENV != config.EXEC_KEY_ENV


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
