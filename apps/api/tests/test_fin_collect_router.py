"""L09 · 采集三个内网端点的路由层（**不连库、不联网**）。

直接调端点函数（不经 FastAPI 请求栈）—— 鉴权只读 `request.headers`，
一个带 headers 的桩对象就够（同 `test_fin_instruments_router.py` 的手法）。

覆盖：鉴权 401 · 入参非法 400（不猜、不填默认）· 正常路径委托到服务层 ·
情绪读回的空 / 有两条形状。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers import fin_data as router


class _Req:
    def __init__(self, key: str = "k"):
        self.headers = {"X-Hunter-Internal-Key": key}


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(router, "_INTERNAL_KEY", "k")


def test_bad_key_is_rejected():
    with pytest.raises(HTTPException) as ei:
        router.sentiment_freshness(_Req(key="wrong"), limit=1)
    assert ei.value.status_code == 401


def test_collect_news_route_delegates(monkeypatch):
    from app.services.fin import collection as coll
    seen = {}

    def fake(market, *, limit, codes, per_code):
        seen.update(market=market, limit=limit, codes=codes, per_code=per_code)
        return {"ok": True, "market": "CN_A", "inserted": 2}

    monkeypatch.setattr(coll, "collect_news", fake)
    out = router.collect_news(_Req(), router.CollectNewsIn(market="cn", limit=5, per_code=3))
    assert out["inserted"] == 2
    assert seen == {"market": "cn", "limit": 5, "codes": None, "per_code": 3}


def test_collect_fundamental_route_delegates(monkeypatch):
    from app.services.fin import collection as coll
    monkeypatch.setattr(coll, "collect_fundamentals",
                        lambda market, *, limit, codes, keep_raw:
                        {"ok": False, "market": "US", "reason": "US 财报数据源未接"})
    out = router.collect_fundamental(_Req(), router.CollectFundamentalIn(market="us"))
    assert out["ok"] is False and "未接" in out["reason"]


def test_sentiment_register_maps_validation_error_to_400(monkeypatch):
    from app.services.fin import sentiment as sent

    def boom(payload):
        raise sent.SentimentError("model_version 必填")

    monkeypatch.setattr(sent, "register_sentiment", boom)
    with pytest.raises(HTTPException) as ei:
        router.sentiment_register(_Req(), router.SentimentIn(code="600519"))
    assert ei.value.status_code == 400 and "model_version" in ei.value.detail


def test_sentiment_register_happy_path(monkeypatch):
    from app.services.fin import sentiment as sent
    monkeypatch.setattr(sent, "register_sentiment",
                        lambda payload: {"id": 1, "code": payload["code"], "quality": payload["quality"]})
    body = router.SentimentIn(code="600519", as_of="2026-10-04T09:30:00+08:00",
                              model_version="manual:v1", quality="ok", source="manual",
                              generated_at="2026-10-04T09:35:00+08:00")
    out = router.sentiment_register(_Req(), body)
    assert out["id"] == 1 and out["quality"] == "ok"


def test_sentiment_read_route_shape(monkeypatch):
    from app.services.fin import sentiment as sent
    monkeypatch.setattr(sent, "read_sentiment", lambda code, limit: [{"id": 1, "code": code}])
    out = router.sentiment_read(_Req(), "600519", limit=10)
    assert out["code"] == "600519" and out["count"] == 1
