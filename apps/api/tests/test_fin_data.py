"""统一行情适配层（M7 · M-20）：市场判定、时间对齐、板块→涨跌停、同步失败拒绝。

**不联网**：只测纯函数与解析。真正的数据源可用性由 M7 成果文档里的实测记录承担
（akshare / yfinance 在本机不可用，见报告）。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.services import fin_data


# ── 市场判定（与 `market_source.market_of` 同口径：5 位 = 港股）──────────────

@pytest.mark.parametrize("code,market", [
    ("600519", "a"), ("000001", "a"), ("300750", "a"), ("688981", "a"),
    ("00700", "hk"), ("09988", "hk"), ("00700.HK", "hk"),
    ("AAPL", "us"), ("BRK.B", "us"), ("AAPL.US", "us"),
])
def test_market_of(code, market):
    assert fin_data.market_of(code) == market


# ── 时间对齐：数据源时刻必须带**正确的**时区 ───────────────────────────────

def test_event_time_a_share_is_cst():
    """A 股报文是纯数字的上海时间。"""
    dt = fin_data.parse_event_time("20260930161458", "a")
    assert dt is not None
    assert dt.utcoffset() == timedelta(hours=8)
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second) == (2026, 9, 30, 16, 14, 58)


def test_event_time_us_is_new_york_not_cst():
    """美股给的是纽约本地时间。

    第一版一律贴 +08:00 —— 于是 12:04 ET 被记成 12:04 CST，**差 12 小时且不报错**
    （时间戳看着完全正常）。这条用例就是钉住那个修正。
    """
    dt = fin_data.parse_event_time("2026-10-02 12:03:20", "us")
    assert dt is not None
    # 纽约夏令时 = UTC-4，绝不是 UTC+8
    assert dt.utcoffset() in (timedelta(hours=-4), timedelta(hours=-5))
    assert dt.hour == 12                       # 本地时间原样保留


def test_event_time_unparsable_is_none():
    """解析不出来就返回 None —— 不拿本机时间顶上（`09 §六-6`）。"""
    assert fin_data.parse_event_time("", "a") is None
    assert fin_data.parse_event_time("不是时间", "a") is None


# ── 板块 → 涨跌停（判不出就拒绝，不猜）─────────────────────────────────────

@pytest.mark.parametrize("code,exchange,board", [
    ("600519", "SH", "main"), ("601398", "SH", "main"), ("603000", "SH", "main"),
    ("000001", "SZ", "main"), ("002594", "SZ", "main"),
    ("300750", "SZ", "chinext"), ("301236", "SZ", "chinext"),
    ("688981", "SH", "star"), ("689009", "SH", "star"),
    ("830799", "BJ", "bse"), ("430047", "BJ", "bse"),
])
def test_board_of(code, exchange, board):
    assert fin_data.board_of(code) == (exchange, board)


@pytest.mark.parametrize("code", ["12345", "ABCDEF", "", "999999"])
def test_board_of_unknown(code):
    assert fin_data.board_of(code) == (None, None)


def test_instrument_derives_limit_from_board():
    """幅度由板块定：主板 10% / 双创 20% / 北交所 30%。"""
    assert fin_data.instrument("600519")["limit_up_pct"] == "0.10"
    assert fin_data.instrument("300750")["limit_up_pct"] == "0.20"
    assert fin_data.instrument("688981")["limit_up_pct"] == "0.20"
    assert fin_data.instrument("830799")["limit_up_pct"] == "0.30"


def test_instrument_reads_master_board_when_given():
    """`company_master` 的中文板块名要能归一。"""
    item = fin_data.instrument("300750", name="宁德时代", board="创业板")
    assert item["available"] is True and item["board"] == "chinext"


def test_instrument_marks_st_from_name():
    assert fin_data.instrument("600519", name="*ST 测试")["is_st"] is True
    assert fin_data.instrument("600519", name="贵州茅台")["is_st"] is False


def test_instrument_refuses_unknown_board():
    """判不出板块 → `available=false` 且带原因。

    调用方（fin-worker 的同步任务）据此**不写 `fin_instrument`**，
    于是风控第 4 条会拒绝该标的 —— 「绝不猜涨跌幅」的兑现点在这里。
    """
    item = fin_data.instrument("999999")
    assert item["available"] is False
    assert "拒绝该标的" in item["reason"]


# ── 免费通道报文的解析（不联网，喂一段真实报文形状）────────────────────────

class _FakeResp:
    def __init__(self, body: bytes):
        self.content = body


def _patch_requests(monkeypatch, body: bytes, capture: dict):
    import requests

    def fake_get(url, headers=None, timeout=None):
        capture["url"] = url
        return _FakeResp(body)

    monkeypatch.setattr(requests, "get", fake_get)


def test_tencent_a_share_parses_orderbook(monkeypatch):
    """A 股免费通道要给出买一/卖一价量 —— M-20 的「沪深 Level-1 盘口」。"""
    # 报文形状取自本机实测（sh601398），字段位置一致
    fields = ["1", "工商银行", "601398", "8.28", "8.16", "8.17", "100", "1", "2",
              "8.27", "9562"] + ["0"] * 8 + ["8.28", "11922"] + ["0"] * 9
    fields += ["20260930161458", "0.12", "1.47", "8.30", "8.15", "8.28/100/100"]
    body = ('v_sh601398="' + "~".join(fields) + '";').encode("gbk")
    cap: dict = {}
    _patch_requests(monkeypatch, body, cap)

    hit = fin_data._tencent_quote("601398")
    assert hit is not None
    assert hit["last_price"] == "8.28"
    assert hit["prev_close"] == "8.16"
    assert hit["bid1_price"] == "8.27" and hit["bid1_volume"] == 9562
    assert hit["ask1_price"] == "8.28" and hit["ask1_volume"] == 11922
    assert "601398" in cap["url"] and cap["url"].startswith("https://qt.gtimg.cn")


def test_tencent_hk_has_no_orderbook(monkeypatch):
    """港美股这条通道不给盘口 → 返回 None，**不拿最新价冒充买一**。"""
    fields = ["1", "腾讯控股", "00700", "421.20", "431.00", "422.00", "100", "1", "2",
              "421.20", "0"] + ["0"] * 8 + ["421.20", "0"] + ["0"] * 9
    fields += ["2026/10/02 16:08:10", "-9.80", "1", "425.00", "419.80", "x"]
    body = ('v_hk00700="' + "~".join(fields) + '";').encode("gbk")
    _patch_requests(monkeypatch, body, {})

    hit = fin_data._tencent_quote("00700")
    assert hit is not None
    assert hit["bid1_price"] is None and hit["ask1_price"] is None
    assert hit["event_time"] is not None and hit["event_time"].utcoffset() == timedelta(hours=8)


def test_tencent_zero_price_is_none(monkeypatch):
    """价格为 0 = 这个代码腾讯没有 → 返回 None（不返回 0 价）。"""
    body = ('v_sh601398="1~x~601398~0~0~0~0";').encode("gbk")
    _patch_requests(monkeypatch, body, {})
    assert fin_data._tencent_quote("601398") is None
