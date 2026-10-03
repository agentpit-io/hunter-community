"""R5 · 经验消费点：**规范化标的全等匹配（读 `symbols` 列）+ 负向层**。

经验对决策的唯一作用就是「命中本标的的**负向**结论 → 本时点不下单」。
`R5` 把命中依据从 `R3` 的 `applicability` 自由文本**改成读 `symbols` 列** ——
`HK:00700` 与 `US:0700` 必须判为**不同标的**（`US:0700` 拦不掉 `HK:00700`）。

用例按**判定类别**铺（怎么算命中 / 怎么不算 / 负向层怎么分），只加不删。
"""

from __future__ import annotations

import pytest

from app.strategy import memory_gate as mg


# ── ① 规范化（与 api 侧 `services/fin/symbols.normalize` 对齐）──────────────

def test_normalize_symbol_is_market_colon_code_uppercased():
    assert mg.normalize_symbol("hk", " 00700 ") == "HK:00700"
    assert mg.normalize_symbol("CN_A", "601398") == "CN_A:601398"
    assert mg.normalize_symbol("a", "600519") == "CN_A:600519"     # 市场别名
    assert mg.normalize_symbol(None, "aapl") == ":AAPL"


def test_normalize_strips_exchange_suffix():
    """`fin_trade.code` 里 `AAPL` 与 `AAPL.US` 两种形态都出现过 —— 同一只票一个身份。"""
    assert mg.normalize_symbol("US", "AAPL.US") == "US:AAPL"
    assert mg.normalize_symbol("HK", "00700.HK") == "HK:00700"


def test_normalize_does_not_pad_or_strip_leading_zeros():
    """**不做零填充、不剥前导零** —— `US:0700` 里那个 0 是身份的一部分。"""
    assert mg.normalize_symbol("US", "0700") == "US:0700"
    assert mg.normalize_symbol("US", "700") == "US:700"


def test_normalize_empty_code_is_empty():
    assert mg.normalize_symbol("HK", "") == ""
    assert mg.normalize_symbol("HK", None) == ""


def test_different_markets_never_collide():
    """`HK:00700` 与 `US:0700` 必须判为**不同标的**（任务书 §一.3）。"""
    assert mg.normalize_symbol("HK", "00700") != mg.normalize_symbol("US", "0700")


# ── ② 命中：`symbols` 列全等 ──────────────────────────────────────────────

def _item(**kw):
    base = {"experience_id": "exp_mem1", "kind": "verified", "status": "已确认",
            "statement": "追高后回撤概率显著上升",
            "symbols": ["HK:00700"], "polarity": "refute"}
    base.update(kw)
    return base


def test_is_blocking_requires_kind_status_symbol_and_polarity():
    assert mg.is_blocking(_item(), market="HK", code="00700") is True
    assert mg.is_blocking(_item(kind="fact"), market="HK", code="00700") is False
    assert mg.is_blocking(_item(kind="hypothesis"), market="HK", code="00700") is False
    assert mg.is_blocking(_item(status="待验证"), market="HK", code="00700") is False
    assert mg.is_blocking(_item(status="已推翻"), market="HK", code="00700") is False
    # 标的列里没有本次标的 → 不拦（跨市场 / 别的票）
    assert mg.is_blocking(_item(symbols=["US:0700"]), market="HK", code="00700") is False
    assert mg.is_blocking(_item(symbols=["HK:00701"]), market="HK", code="00700") is False


def test_symbols_column_is_the_only_target_source():
    """`applicability` 里**写对了也不算命中** —— `R5` 起只读 `symbols` 列。"""
    item = _item(symbols=None, applicability="HK:00700 追高后回撤")
    assert mg.is_blocking(item, market="HK", code="00700") is False


def test_symbols_must_be_exact_string_match():
    """裸代码 `0700` / `00700` 不是规范化标的，不算命中（撞车的根源）。"""
    assert mg.is_blocking(_item(symbols=["0700"]), market="HK", code="00700") is False
    assert mg.is_blocking(_item(symbols=["00700"]), market="HK", code="00700") is False
    # 多标的里含本次标的 → 命中
    assert mg.is_blocking(_item(symbols=["US:0700", "HK:00700"]),
                          market="HK", code="00700") is True


# ── ③ 负向层（`polarity`，fail-closed）────────────────────────────────────

@pytest.mark.parametrize("polarity", ["refute", None, ""])
def test_refute_and_unknown_block(polarity):
    """`refute` 拦；`NULL`（旧行未回填，未知）**也拦** —— 拿不准就按负向处理。"""
    item = _item()
    if polarity is None:
        item.pop("polarity")
    else:
        item["polarity"] = polarity
    assert mg.is_blocking(item, market="HK", code="00700") is True


@pytest.mark.parametrize("polarity", ["support", "neutral"])
def test_explicit_non_negative_does_not_block(polarity):
    assert mg.is_blocking(_item(polarity=polarity), market="HK", code="00700") is False


# ── ④ 遍历与容错 ──────────────────────────────────────────────────────────

def test_blocking_experience_returns_first_match_and_none_when_clean():
    items = [_item(experience_id="exp_a", symbols=["US:0700"]),
             _item(experience_id="exp_b"),
             _item(experience_id="exp_c")]
    got = mg.blocking_experience(items, market="HK", code="00700")
    assert got is not None and got["experience_id"] == "exp_b"
    assert got["statement"] == "追高后回撤概率显著上升"
    assert got["symbols"] == ["HK:00700"] and got["polarity"] == "refute"
    assert mg.blocking_experience([_item(symbols=["US:0700"])], market="HK", code="00700") is None


def test_blocking_experience_tolerates_garbage():
    assert mg.blocking_experience(None, market="HK", code="00700") is None
    assert mg.blocking_experience([None, "x", 5], market="HK", code="00700") is None
    assert mg.is_blocking(_item(symbols="HK:00700"), market="HK", code="00700") is False  # 不是数组
    assert mg.is_blocking(_item(symbols=[None, 7]), market="HK", code="00700") is False


# ── ⑤ 写法类别（旧「文本匹配」那一批对应到新口径）────────────────────────

@pytest.mark.parametrize("market,code,expected", [
    ("HK", "00700", "HK:00700"),
    ("hk", " 00700 ", "HK:00700"),
    ("US", "0700", "US:0700"),
    ("US", "aapl", "US:AAPL"),
    ("US", "AAPL.US", "US:AAPL"),
    ("HK", "00700.HK", "HK:00700"),
    ("CN_A", "601398", "CN_A:601398"),
    ("a", "600519", "CN_A:600519"),
    ("cn", "600519", "CN_A:600519"),
    ("CN_A", "600519.SH", "CN_A:600519"),
])
def test_normalize_by_writing_style(market, code, expected):
    assert mg.normalize_symbol(market, code) == expected


@pytest.mark.parametrize("symbols", [
    ["HK:00700"],                          # 单个
    ["US:0700", "HK:00700"],               # 多标的，本次在其中
    ["CN_A:601398", "HK:00700", "US:AAPL"],
])
def test_hit_when_target_in_symbol_list(symbols):
    assert mg.is_blocking(_item(symbols=symbols), market="HK", code="00700") is True


@pytest.mark.parametrize("symbols", [
    ["US:0700"],       # ← 这条就是「规范化」要解决的：别的市场，代码形近
    ["HK:00701"],      # 差一位
    ["HK:700"],        # 位数不同
    ["00700"],         # 裸代码（不是标的声明）
    ["hk:00700"],      # 小写（列里本该是大写规范形态）
    ["HK00700"],       # 缺冒号
    [],
    None,              # 旧行未回填
])
def test_miss_when_target_not_exactly_in_symbol_list(symbols):
    assert mg.is_blocking(_item(symbols=symbols), market="HK", code="00700") is False


@pytest.mark.parametrize("market,code", [("US", "0700"), ("HK", "700"), ("CN_A", "601398"), ("US", "")])
def test_other_targets_do_not_hit_hk_00700(market, code):
    assert mg.is_blocking(_item(), market=market, code=code) is False


def test_first_of_multiple_matches_is_returned():
    items = [_item(experience_id="exp_1"), _item(experience_id="exp_2")]
    got = mg.blocking_experience(items, market="HK", code="00700")
    assert got["experience_id"] == "exp_1"


def test_non_dict_and_missing_fields_are_safe():
    assert mg.is_blocking("not-a-dict", market="HK", code="00700") is False
    assert mg.is_blocking({}, market="HK", code="00700") is False
    assert mg.is_blocking({"kind": "verified", "status": "已确认"},
                          market="HK", code="00700") is False
