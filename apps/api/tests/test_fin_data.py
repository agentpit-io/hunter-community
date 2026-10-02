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


def test_instrument_marks_st_from_quote_name():
    """**ST 只认行情通道的简称**（拍板 2026-10-02 §二 / §七）。

    `name=`（`company_master` 的中文名）只作展示，**不作 ST 依据** —— hunter
    网关的 name 就是代码本身、master 只有 300 行种子，两者都不权威。
    """
    assert fin_data.instrument("600519", st_name="*ST 测试")["is_st"] is True
    assert fin_data.instrument("600519", st_name="退市工行")["is_st"] is True
    assert fin_data.instrument("600519", st_name="贵州茅台")["is_st"] is False
    # master 名字里有 ST 不算数（口径变更前它是唯一来源，正是要堵的那条路）
    assert fin_data.instrument("600519", name="*ST 测试")["is_st"] is False
    # 简称等于代码本身 = 没拿到真简称（hunter 通道就是这个形状）
    assert fin_data.instrument("600519", st_name="600519")["is_st"] is False


def test_instrument_records_st_source():
    """`source` 记的是 **ST 判定**的来源，供 `fin_instrument.source` 留痕。"""
    assert fin_data.instrument("600519", st_name="贵州茅台")["source"] == "quote_name"
    assert fin_data.instrument("600519", name="贵州茅台")["source"] == "company_master/stock_universe"


def test_instrument_refuses_when_st_unknown():
    """同步路径（`require_st_name=True`）：**判不出 ST 状态 → 拒绝该标的**。

    拍板 §七 零容忍：拿不到真实证券简称时绝不假设「它不是 ST」—— 假设错的
    后果是把一只 5% 的 ST 股按 10% 撮合。
    """
    item = fin_data.instrument("600519", require_st_name=True)
    assert item["available"] is False
    assert "ST" in item["reason"] and "拒绝该标的" in item["reason"]
    # 拿得到简称就正常放行
    ok = fin_data.instrument("600519", st_name="贵州茅台", require_st_name=True)
    assert ok["available"] is True and ok["is_st"] is False


def test_tencent_names_parses_batch_payload(monkeypatch):
    """批量简称解析：一张 URL 多个代码、GBK 解码、按符号回填代码。"""
    payload = (
        'v_sh600519="1~贵州茅台~600519~1258.62~";\n'
        'v_sz000001="1~平安银行~000001~11.20~";\n'
        'v_sh600000="1~~600000~0~";\n'          # 没名字的那条不收录
    ).encode("gbk")

    class _Resp:
        status_code = 200
        content = payload

    monkeypatch.setattr("requests.get", lambda *a, **kw: _Resp())
    out = fin_data.tencent_names(["600519", "000001", "600000"])
    assert out == {"600519": "贵州茅台", "000001": "平安银行"}


def test_tencent_names_raises_on_waf_501(monkeypatch):
    """见 501（WAF 拦截）**立即中止整批** —— 硬闯会把整机 IP 送进黑名单。"""
    class _Resp:
        status_code = 501
        content = b""

    monkeypatch.setattr("requests.get", lambda *a, **kw: _Resp())
    with pytest.raises(fin_data.TencentWafBlocked):
        fin_data.tencent_names(["600519"])


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


# ── hunter 网关的两个已知缺口（2026-10-02 实测）· 补齐路径 ──────────────────
#
# hunter `quote` 的 A 股返回：`ts` 只到日期（`"2026-09-30"`）、`name` 就是代码
# （`"600519"`）。前者会让快照落成当天 00:00 → 交易时段校验与新鲜度双杀，
# 委托永远不成交；后者让卡片把代码当名字显示。两处都用腾讯通道补，
# **只补字段、不换价格**（拍板 §一 价格仍归 hunter）。

def _cst(h, m=0, s=0):
    from datetime import datetime, timezone, timedelta
    return datetime(2026, 9, 30, h, m, s, tzinfo=timezone(timedelta(hours=8)))


_OFFICIAL_DATE_ONLY = {
    "name": "601398", "last_price": "8.28", "prev_close": "8.16",
    "open": "8.17", "high": "8.30", "low": "8.15",
    "bid1_price": None, "bid1_volume": None, "ask1_price": None, "ask1_volume": None,
    "event_time": _cst(0), "source": "official",
}


def test_quote_fills_date_only_time_and_code_name(monkeypatch):
    monkeypatch.setattr(fin_data, "_official_quote", lambda code: dict(_OFFICIAL_DATE_ONLY))
    monkeypatch.setattr(fin_data, "_tencent_quote", lambda code: {
        "name": "工商银行", "last_price": "8.28",
        "event_time": _cst(14, 55, 1), "source": "tencent-qt"})

    q = fin_data.quote("601398")
    assert q["source"] == "official" and q["last_price"] == "8.28"   # 价格与来源不变
    assert q["name"] == "工商银行"                                   # 简称补齐
    assert q["event_time"].startswith("2026-09-30T14:55:01")         # 盘中时刻补齐
    assert any("盘中时刻取自腾讯通道" in g for g in q["gaps"])
    assert any("证券简称取自腾讯通道" in g for g in q["gaps"])


def test_quote_keeps_official_when_intraday_time_present(monkeypatch):
    """时刻已经是盘中的 → 只补 name，不碰 event_time。"""
    hit = dict(_OFFICIAL_DATE_ONLY)
    hit["event_time"] = _cst(10, 0, 0)
    monkeypatch.setattr(fin_data, "_official_quote", lambda code: dict(hit))
    monkeypatch.setattr(fin_data, "_tencent_quote", lambda code: {
        "name": "工商银行", "event_time": _cst(10, 0, 5), "source": "tencent-qt"})

    q = fin_data.quote("601398")
    assert q["event_time"].startswith("2026-09-30T10:00:00")   # 用官方那个，不是腾讯的
    assert q["name"] == "工商银行"
    assert not any("盘中时刻取自腾讯通道" in g for g in q["gaps"])


def test_quote_keeps_date_only_when_tencent_also_fails(monkeypatch):
    """腾讯也取不到 → **如实记缺口**，不编一个时刻出来。"""
    monkeypatch.setattr(fin_data, "_official_quote", lambda code: dict(_OFFICIAL_DATE_ONLY))
    monkeypatch.setattr(fin_data, "_tencent_quote", lambda code: None)

    q = fin_data.quote("601398")
    assert q["event_time"].startswith("2026-09-30T00:00:00")
    assert any("腾讯通道也取不到盘中时刻" in g for g in q["gaps"])


def test_is_date_only():
    assert fin_data._is_date_only(_cst(0)) is True
    assert fin_data._is_date_only(_cst(0, 0, 1)) is False
    assert fin_data._is_date_only(None) is False
