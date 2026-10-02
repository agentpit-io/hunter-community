# -*- coding: utf-8 -*-
"""M5 · 每日报告：事实层 / 回读校验 / 表达层（**纯函数，不连库不连网**）。

这是本轮的核心用例集 —— 「报告里每个数字都能追溯到账本或指标代码」在这里被钉死。
真库端到端（生成 → 联查 → 故意改坏 → 拦截）由 `scripts/check_report_numbers.py`
在验收环境实跑，证据贴进 `docs/开发文档/M5-成果与测试报告.md`。

    cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_report.py -q
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

# `apps/api/__init__.py` 是空文件 → 若 `/` 进了 sys.path，`import app` 会命中
# `/app` 目录包（没有 .services）而不是 `/app/app`。与 test_fin_project_router.py 同一处理。
_API_ROOT = Path(__file__).resolve().parents[1]
for _p in ("", "/", str(_API_ROOT)):
    while _p in sys.path:
        sys.path.remove(_p)
sys.path.insert(0, str(_API_ROOT))
_bad_app = sys.modules.get("app")
if _bad_app is not None and Path(getattr(_bad_app, "__file__", "") or "").parent == _API_ROOT:
    del sys.modules["app"]

from app.services.fin import report as R  # noqa: E402

SH = timezone(timedelta(hours=8))


def _dt(day: str, hh: int = 15, mm: int = 30) -> datetime:
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hh, mm, tzinfo=SH)


def _ctx(*, navs=(1.0,), total_assets=100000.0, trades=(), positions=(),
         recon_passed=True, open_orders=0, quality="ok", missing=False,
         trade_date="2026-09-30") -> dict:
    series = [
        {"as_of": _dt("2026-09-%02d" % (30 - (len(navs) - 1 - i))), "nav": n,
         "total_assets": total_assets}
        for i, n in enumerate(navs)
    ]
    valuation = {
        "as_of": _dt(trade_date), "nav": navs[-1], "total_assets": total_assets,
        "cash_available": total_assets, "cash_frozen": Decimal(0),
        "market_value": Decimal(0), "quality": quality, "missing_flag": missing,
    }
    return {
        "project": {"project_id": "prj_t", "user_id": "u_t", "tier": "manage",
                    "initial_capital": Decimal(100000)},
        "trade_date": trade_date,
        "valuation_latest": valuation,
        "valuation_series": series,
        "trades": list(trades),
        "positions": list(positions),
        "open_order_count": open_orders,
        "recon_latest": {"passed": recon_passed} if recon_passed is not None else None,
    }


# ── 事实层 ────────────────────────────────────────────────────────────────

def test_build_facts_core_metrics():
    ctx = _ctx(navs=(1.0, 1.02), total_assets=102000.0,
               trades=[{"code": "601398", "total_fee": 5.0, "traded_at": _dt("2026-09-30")}],
               positions=[{"code": "601398", "qty": 100}])
    fm = R.facts_map(R.build_facts(ctx))
    assert fm["initial_capital"]["value"] == Decimal("100000.0000")
    assert fm["nav"]["value"] == Decimal("1.020000")
    assert fm["total_assets"]["value"] == Decimal("102000.0000")
    assert fm["return_pct"]["value"] == Decimal("2.0000")     # nav-1 → +2%
    assert fm["daily_return_pct"]["value"] == Decimal("2.0000")  # 1.02/1.0-1
    assert fm["trade_count_today"]["value"] == Decimal(1)
    assert fm["position_count"]["value"] == Decimal(1)
    assert fm["fee_total"]["value"] == Decimal("5.0000")
    assert fm["recon_passed"]["value"] == Decimal(1)
    assert fm["data_quality_ok"]["value"] == Decimal(1)
    assert fm["return_caliber_count"]["value"] == Decimal(1)


def test_every_fact_has_source_ref_and_computed_by():
    """硬要求：`source_ref` 与 `computed_by` 非空。"""
    for f in R.build_facts(_ctx(navs=(1.0, 1.01))):
        assert f["source_ref"], f["metric_key"]
        assert f["computed_by"] == R.CODE_VERSION


def test_missing_valuation_is_all_none_not_zero():
    """**算不出就 None（渲染成 —），绝不兜底成 0。**"""
    ctx = _ctx(recon_passed=None)
    ctx["valuation_latest"] = None
    ctx["valuation_series"] = []
    fm = R.facts_map(R.build_facts(ctx))
    for key in ("nav", "total_assets", "return_pct", "daily_return_pct", "max_drawdown",
                "data_quality_ok", "recon_passed"):
        assert fm[key]["value"] is None, key
    # 计数是真实的 0（这个合成场景里确实一次估值都没有），不是「算不出」。
    assert fm["days_traded"]["value"] == Decimal(0)
    assert R.format_value(None, "CNY") == "—"


def test_single_point_series_cannot_compute_daily_or_drawdown():
    fm = R.facts_map(R.build_facts(_ctx(navs=(1.0,))))
    assert fm["daily_return_pct"]["value"] is None
    assert fm["max_drawdown"]["value"] is None


def test_max_drawdown_over_series():
    # 1.00 → 1.20 → 0.90 → 1.10：峰 1.20 谷 0.90 = -25%
    fm = R.facts_map(R.build_facts(_ctx(navs=(1.0, 1.2, 0.9, 1.1))))
    assert fm["max_drawdown"]["value"] == Decimal("-25.0000")
    assert fm["daily_return_pct"]["value"] == Decimal("22.2222")  # 1.10/0.90-1


# ── 回读校验 ──────────────────────────────────────────────────────────────

def test_extract_numbers_skips_dates_times_and_identifiers():
    """日期 / 时刻 / T+1 这类标识被掩掉；裸 6 位代码留给 `validate_numbers` 按持仓判定。"""
    toks = {t.raw.strip() for t in R.extract_numbers(
        "2026-09-30 15:30 用 601398 在 T+1 后净值 1.0230，回撤 -4.57%")}
    assert "1.0230" in toks and "-4.57%" in toks
    assert {"2026", "09", "15", "30", "1"} & toks == set()   # 日期/时刻/T+1 已掩


def test_validate_numbers_accepts_facts_and_rejects_invented():
    facts = R.build_facts(_ctx(navs=(1.0, 1.02),
                               trades=[{"code": "601398", "total_fee": 5.0,
                                        "traded_at": _dt("2026-09-30")}]))
    good = "今日净值 1.0200，累计收益率 +2.00%，当日成交 1 笔。"
    assert R.validate_numbers(good, facts, codes={"601398"})["ok"] is True

    bad = "今日净值 1.0200，累计收益率 +8.74%，当日成交 1 笔。"
    res = R.validate_numbers(bad, facts, codes={"601398"})
    assert res["ok"] is False
    assert [v["token"] for v in res["violations"]] == ["+8.74%"]


def test_validate_report_checks_self_review_too():
    """self_review 里编的数字同样被拦 —— 铁律覆盖报告里的**全部** AI 文字。"""
    facts = R.build_facts(_ctx(navs=(1.0, 1.02)))
    report = {
        "analysis_text": "净值 1.0200。",
        "self_review": {"did_well": "回撤控制住了。", "did_bad": "多亏了 12.34% 的行情。",
                        "change_tomorrow": "保持。"},
    }
    res = R.validate_report(report, facts)
    assert res["ok"] is False
    fields = {v["field"] for v in res["violations"]}
    assert fields == {"self_review.did_bad"}


def test_code_tokens_are_not_number_claims():
    """6 位代码是标识不是数字主张：命中 `codes` 才忽略（本金 100000 仍是数字）。"""
    ctx = _ctx(navs=(1.0,), trades=[{"code": "601398", "total_fee": 0,
                                     "traded_at": _dt("2026-09-30")}])
    facts = R.build_facts(ctx)
    res = R.validate_numbers("持仓 601398，本金 100000。", facts, codes={"601398"})
    assert res["ok"] is True
    assert res["checked"] == 1        # 只检查了 100000


# ── 三种口径不拼接 ─────────────────────────────────────────────────────────

def test_single_caliber_ok_by_default():
    assert R.assert_single_caliber(R.build_facts(_ctx(navs=(1.0,)))) == []


def test_single_caliber_rejects_cross_caliber_source():
    facts = R.build_facts(_ctx(navs=(1.0,)))
    facts.append({"metric_key": "x", "label_cn": "x", "value": Decimal(1), "unit": "",
                  "source_ref": "backtest_trade:2026-09-30", "computed_by": "x"})
    problems = R.assert_single_caliber(facts)
    assert problems and "另一种口径" in problems[0]


# ── 表达层 ────────────────────────────────────────────────────────────────

def test_render_html_has_no_none_and_shows_dash():
    ctx = _ctx(navs=(1.0,))
    facts = R.build_facts(ctx)
    report = {"trade_date": "2026-09-30", "status": "validated", "analysis_text": "净值 1.0000。",
              "self_review": {"did_well": "a", "did_bad": "b", "change_tomorrow": "c"},
              "valuation_as_of": str(_dt("2026-09-30")), "llm_model": "none",
              "prompt_version": R.PROMPT_VERSION, "created_at": "x"}
    html = R.render_html(report, facts, project=ctx["project"])
    assert "None" not in html and "nan" not in html
    assert "—" in html                     # 算不出的指标（当日收益率 / 最大回撤）
    assert "回测 / 前向模拟 / 影子运行不与之拼成同一条曲线" in html
    assert "computed_by" in html and R.CODE_VERSION in html


def test_report_id_is_deterministic():
    assert R.report_id_for("prj_1", "2026-09-30") == R.report_id_for("prj_1", "2026-09-30")
    assert R.report_id_for("prj_1", "2026-09-30") != R.report_id_for("prj_1", "2026-10-09")


# ── 解析模型输出 ──────────────────────────────────────────────────────────

def test_parse_analysis_json_from_fenced_block():
    raw = '```json\n{"analysis_text":"正文","self_review":{"did_well":"a","did_bad":"b","change_tomorrow":"c"}}\n```'
    parsed = R._parse_analysis_json(raw)
    assert parsed["analysis_text"] == "正文"
    assert parsed["self_review"]["did_bad"] == "b"


def test_parse_analysis_json_rejects_garbage():
    with pytest.raises(ValueError):
        R._parse_analysis_json("模型今天不想输出 JSON")


# ── N5 · 市场维度：币种格式化 / 逐市场事实 / 跨市场汇总（纯函数）──────────────

def test_format_value_by_currency_symbol():
    """金额按币种出符号（A 股 ¥、港股 HK$、美股 $）；币种缺失不加符号、不猜。"""
    assert R.format_value(Decimal("1234.5"), R.MONEY_UNIT, "CNY") == "¥1,234.50"
    assert R.format_value(Decimal("1234.5"), R.MONEY_UNIT, "HKD") == "HK$1,234.50"
    assert R.format_value(Decimal("1234.5"), R.MONEY_UNIT, "USD") == "$1,234.50"
    assert R.format_value(Decimal("1234.5"), R.MONEY_UNIT, None) == "1,234.50"   # 不猜 ¥
    assert R.format_value(None, R.MONEY_UNIT, "USD") == "—"


def test_build_facts_carry_market_and_currency():
    """事实层加 market 维度：每行带市场，金额行带该市场本币。"""
    ctx = _ctx(navs=(1.0, 1.02))
    ctx["market"], ctx["currency"] = "HK", "HKD"
    fm = R.facts_map(R.build_facts(ctx))
    assert all(f["market"] == "HK" for f in fm.values())
    assert fm["total_assets"]["currency"] == "HKD"
    assert fm["total_assets"]["unit"] == R.MONEY_UNIT
    assert fm["nav"]["currency"] is None          # 非金额行不带币种
    assert fm["return_pct"]["currency"] is None


def _mctx(market, currency, total):
    return {"market": market, "currency": currency,
            "valuation_latest": {"as_of": _dt("2026-09-30"), "total_assets": Decimal(str(total))}}


def test_summary_facts_combine_with_fx():
    """跨市场合计按现取汇率折算，并把 fx_source / fx_at 写进事实行。"""
    fx = {"HKDCNY": {"rate": 0.85, "at": "2026-10-03T06:59:36", "source": "sina:fx_shkdcny"},
          "USDCNY": {"rate": 6.70, "at": "2026-10-03T04:59:58", "source": "sina:fx_susdcnh"}}
    facts = R.build_summary_facts(
        [_mctx("CN_A", "CNY", 100000), _mctx("HK", "HKD", 20000), _mctx("US", "USD", 1000)], fx)
    fm = R.facts_map(facts)
    # 100000 + 20000*0.85 + 1000*6.70 = 100000 + 17000 + 6700 = 123700
    assert fm["total_assets_cny"]["value"] == Decimal("123700.0000")
    assert fm["total_assets_cny"]["currency"] == "CNY"
    assert "sina:fx_shkdcny" in fm["fx_HKDCNY"]["source_ref"]
    assert "2026-10-03T06:59:36" in fm["fx_HKDCNY"]["source_ref"]
    # 逐市场本币原值（不折算）
    assert fm["total_assets__HK"]["value"] == Decimal("20000.0000")
    assert fm["total_assets__HK"]["currency"] == "HKD"


def test_summary_facts_missing_fx_shows_dash_with_reason():
    """**取不到汇率 → 合计显示 — 并写明原因**（绝不拿缺项当 0 凑一个完整合计）。"""
    facts = R.build_summary_facts([_mctx("CN_A", "CNY", 100000), _mctx("US", "USD", 1000)], {})
    fm = R.facts_map(facts)
    assert fm["total_assets_cny"]["value"] is None
    assert fm["fx_USDCNY"]["value"] is None
    assert "拿不到" in fm["fx_USDCNY"]["source_ref"] or "unavailable" in fm["fx_USDCNY"]["source_ref"]
    assert "缺 USD→CNY 汇率" in fm["total_assets_cny"]["source_ref"]


def test_summary_caliber_row_present():
    assert R.assert_single_caliber(R.build_summary_facts([_mctx("CN_A", "CNY", 1)], {})) == []


def test_report_id_differs_by_market():
    a = R.report_id_for("prj_1", "2026-09-30", "CN_A")
    b = R.report_id_for("prj_1", "2026-09-30", "HK")
    c = R.report_id_for("prj_1", "2026-09-30", "MULTI")
    assert len({a, b, c}) == 3
    # 同一 (项目, 日, 市场) 稳定
    assert a == R.report_id_for("prj_1", "2026-09-30", "CN_A")


def test_caliber_note_states_paper_and_multi_market():
    """报告口径文案要写清「模拟盘」+ 各市场规则不同（不能只写「实盘模拟」）。"""
    for m in ("CN_A", "HK", "US", R.SUMMARY_MARKET):
        note = R.caliber_note_of(m)
        assert "模拟盘" in note and "不接实盘" in note
        assert "回测 / 前向模拟 / 影子运行不与之拼成同一条曲线" in note
    assert "跨市场汇总" in R.caliber_note_of(R.SUMMARY_MARKET)
    assert "港股" in R.caliber_note_of("HK")
