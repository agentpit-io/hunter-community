# -*- coding: utf-8 -*-
"""agent_run 里不连库的两个组装函数:规则统计块与历史交易周期。

2026-09-12 线上发现 C-08(评分)那条规则的每档统计从来没显示过:分支排在通用「买入规则」分支后面,
C-08 的 kind 也是 buy,永远轮不到。单元测试盯住「按 id 特判的分支要排在按 kind 的通用分支前面」。
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.quant import agent_run as ar, agent_vcp3 as c3


def _t(d, seq, side, code, pnl=None, pnl_pct=None, grade=None, rule="C-01"):
    # 列顺序与 dashboard 里 SELECT agent_trade 的顺序一致(19 列,最后一列 grade)
    return (date.fromisoformat(d), seq, side, code, code, 10, 10.0, 100.0, 5.0, pnl, pnl_pct, 3, rule,
            "买入" if side == "buy" else "卖出", "", date(2026, 1, 5), 1, None, grade)


TRADES = [
    _t("2026-01-05", 1, "buy", "AAA", grade="A"),
    _t("2026-01-10", 1, "sell", "AAA", pnl=50.0, pnl_pct=5.0, rule="C-05"),
    _t("2026-01-05", 2, "buy", "BBB", grade="C"),
    _t("2026-01-10", 2, "sell", "BBB", pnl=-20.0, pnl_pct=-2.0, rule="C-04"),
]


def test_c08_per_grade_stats_reachable():
    rules = ar._rules_block(TRADES, [], [], c3.PARAMS, None, c3)
    c08 = next(r for r in rules if r["id"] == "C-08")
    labels = {s["label"]: s["value"] for s in c08["stats"]}
    assert labels["A 级"].startswith("1 笔 · 胜率 100%"), labels
    assert labels["C 级"].startswith("1 笔 · 胜率 0%"), labels
    assert labels["S 级"] == "0 笔" and labels["B 级"] == "0 笔"
    assert "挡下(D 级或否决)" in labels
    assert "挡下候选" not in labels          # 不是通用买入分支的输出


def test_generic_buy_rule_still_generic():
    rules = ar._rules_block(TRADES, [], [], c3.PARAMS, None, c3)
    c02 = next(r for r in rules if r["id"] == "C-02")
    assert [s["label"] for s in c02["stats"]] == ["挡下候选", "说明"]


def test_trade_rounds_carry_entry_grade():
    rounds = ar._trade_rounds(TRADES, {})
    assert len(rounds) == 2
    assert {r["symbol"]: r["grade"] for r in rounds} == {"AAA": "A", "BBB": "C"}
    # 档位标在代号下方(前端),信号列的规则名保持原样,不再拼「· X 级」(那一列窄,会被截掉)
    assert all("级" not in leg["rule_name"] for r in rounds for leg in r["legs"])
    no_grade = [t[:18] + (None,) for t in TRADES]
    assert all(r["grade"] is None for r in ar._trade_rounds(no_grade, {}))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
