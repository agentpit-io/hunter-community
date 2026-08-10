"""cache 模块测试 — 无 Redis 时 fall-through；有 Redis 时 get/put/clear 正常"""
import pytest


def test_key_stable():
    from app.services.agent.cache import _key
    a = _key("get_quote", {"code": "600519"})
    b = _key("get_quote", {"code": "600519"})
    assert a == b
    c = _key("get_quote", {"code": "000001"})
    assert a != c


def test_key_arg_order_stable():
    from app.services.agent.cache import _key
    a = _key("scout", {"code": "600519", "days": 7})
    b = _key("scout", {"days": 7, "code": "600519"})
    assert a == b


def test_get_put_when_redis_unavailable_falls_through(monkeypatch):
    """Redis 挂时 get 返回 None，put 不抛异常"""
    from app.services.agent import cache
    monkeypatch.setattr(cache, "_redis", None)
    assert cache.get("get_quote", {"code": "600519"}) is None
    cache.put("get_quote", {"code": "600519"}, {"price": 1732})  # 不应抛
    assert cache.clear() == 0
