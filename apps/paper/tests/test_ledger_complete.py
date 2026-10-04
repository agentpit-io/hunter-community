"""账本补齐 · 停牌（第 3 项）/ 公司行为（第 4 项）/ 逐市场 tick（第 5 项）。

- **停牌**：`fin_instrument.halted` 由人工登记口置位，风控**先读它** → 停牌即拒单。
  **数据源未接** —— 只有人工登记口，不编造停牌。
- **公司行为**：分红（现金入账）/ 拆合股 · 送股（股数与成本价调整）的账务，
  记**只追加**的事件表 `fin_corporate_action`，人工登记口写入。**数据源未接**。
- **逐市场 tick**：`app.tick.resolve_tick` 三级解析（标的 → 市场 → 全局回落），
  三市场各不同（港股逐价位区间）。

前两组纯函数（不连库），后一组端到端（要账本库）。
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from app.matching.model import ExecutionModel
from app.matching.pricing import OrderSpec, match
from app.risk.engine import RiskInputs, evaluate
from app.tick import resolve_tick, tick_from_spec

from datetime import datetime, timezone

CST = timezone(__import__("datetime").timedelta(hours=8))

# 迁移 0050 种下的三市场 tick_spec（逐字照 `db/migrations/0050` 的种子）。
CN_SPEC = {"mode": "fixed", "tick": 0.01}
HK_SPEC = {"mode": "bands", "bands": [
    {"lt": 0.25, "tick": 0.001}, {"lt": 0.5, "tick": 0.005}, {"lt": 10, "tick": 0.01},
    {"lt": 20, "tick": 0.02}, {"lt": 100, "tick": 0.05}, {"lt": 200, "tick": 0.1},
    {"lt": 500, "tick": 0.2}, {"lt": 1000, "tick": 0.5}, {"lt": 2000, "tick": 1.0},
    {"lt": 5000, "tick": 2.0}, {"tick": 5.0}]}
US_SPEC = {"mode": "bands", "bands": [{"lt": 1.0, "tick": 0.0001}, {"tick": 0.01}]}

GLOBAL = Decimal("0.0100")   # fin_execution_model.tick_size（老数据的回落值）


# ════════════════════════════════════════════════════════════════════════
# 一 · 逐市场 tick（纯函数）
# ════════════════════════════════════════════════════════════════════════

def test_cn_a_fixed_tick():
    assert tick_from_spec(CN_SPEC, Decimal("10.00")) == Decimal("0.01")


def test_hk_bands_by_price():
    """港股逐价位区间：价格落在哪个档就用哪个 tick。"""
    assert tick_from_spec(HK_SPEC, Decimal("0.234")) == Decimal("0.001")
    assert tick_from_spec(HK_SPEC, Decimal("0.40")) == Decimal("0.005")
    assert tick_from_spec(HK_SPEC, Decimal("5.00")) == Decimal("0.01")
    assert tick_from_spec(HK_SPEC, Decimal("15.0")) == Decimal("0.02")
    assert tick_from_spec(HK_SPEC, Decimal("421.2")) == Decimal("0.2")
    assert tick_from_spec(HK_SPEC, Decimal("6000")) == Decimal("5.0")   # 末档上无界


def test_us_sub_penny_bands():
    """SEC Rule 612：<$1 → $0.0001；≥$1 → $0.01。"""
    assert tick_from_spec(US_SPEC, Decimal("0.50")) == Decimal("0.0001")
    assert tick_from_spec(US_SPEC, Decimal("50")) == Decimal("0.01")


def test_resolve_falls_back_to_global_when_unset():
    """两级都没配 → 回落全局值（老数据行为不变）。"""
    assert resolve_tick(None, None, Decimal("10"), fallback=GLOBAL) == GLOBAL
    assert resolve_tick({}, {}, Decimal("10"), fallback=GLOBAL) == GLOBAL


def test_resolve_instrument_overrides_market():
    inst = {"tick_size": Decimal("0.05")}
    assert resolve_tick(inst, {"tick_spec": CN_SPEC}, Decimal("10"), fallback=GLOBAL) == Decimal("0.05")


def test_resolve_returns_global_object_when_equal():
    """解析值与全局值数值相等 → 回落全局值本身（刻度一致，成交价渲染逐字节不变）。"""
    out = resolve_tick(None, {"tick_spec": CN_SPEC}, Decimal("10"), fallback=GLOBAL)
    assert out == GLOBAL and format(out, "f") == "0.0100"   # 不是 "0.01"


MODEL = ExecutionModel(version="m", slippage_ticks=Decimal("1"),
                       tick_size=Decimal("0.01"), part_fill=False)


def _snap(last, ask1="10.00"):
    return {"snapshot_id": "S", "last_price": Decimal(last), "ask1_price": Decimal(ask1),
            "bid1_price": Decimal(ask1), "prev_close": Decimal("10.00"),
            "tradable": True, "missing_flag": False, "quality": "ok",
            "ask1_volume": None, "bid1_volume": None}


def test_market_order_fill_price_follows_market_tick():
    """三市场的市价单成交价按各自 tick 对齐（这就是「价格精度校验按它来」）。"""
    spec = OrderSpec("buy", 100, "market")
    # CN_A tick 0.01：10.0000 + 滑点 0.01 = 10.0100 → 2 位
    cn = match(spec, _snap("10.0000", "10.0000"), MODEL, tick=Decimal("0.01"))
    assert format(cn.price, "f") == "10.01"
    # HK tick 0.001（价 0.234 落 0.25 以下档）：0.2345 + 滑点 0.01 = 0.2445 → 0.245（3 位）
    hk = match(spec, _snap("0.2345", "0.2345"), MODEL, tick=Decimal("0.001"))
    assert format(hk.price, "f") == "0.245"
    # US tick 0.0001（价 < $1）：0.5678 + 0.0025 → 0.5703（4 位）
    us_model = ExecutionModel(version="m", slippage_ticks=Decimal("25"),
                              tick_size=Decimal("0.0001"), part_fill=False)
    us = match(spec, _snap("0.5678", "0.5678"), us_model, tick=Decimal("0.0001"))
    assert format(us.price, "f") == "0.5703"


# ════════════════════════════════════════════════════════════════════════
# 二 · 停牌（纯函数：风控先读它）
# ════════════════════════════════════════════════════════════════════════

CAL = {"is_trading": True, "sessions": [{"open": "09:30", "close": "11:30"},
                                        {"open": "13:00", "close": "15:00"}]}
RULE = {"market": "CN_A", "currency": "CNY", "timezone": "Asia/Shanghai",
        "sessions": CAL["sessions"], "sellable_rule": "t_plus_n", "sellable_days": 1,
        "lot_rule": "fixed", "lot_fixed": 100, "price_limit_mode": "pct"}
FEE = {"version": "fee-cn-a-v1", "commission_pct": Decimal("0.00025"),
       "commission_min": Decimal("5.00"), "stamp_tax_pct": Decimal("0.0005"),
       "transfer_fee_pct": Decimal("0.00001")}


def _inputs(instrument):
    return RiskInputs(at=datetime(2026, 10, 2, 10, 0, tzinfo=CST), side="buy", qty=100,
                      price=Decimal("10.00"), calendar=CAL, instrument=instrument,
                      fee_model=FEE, prev_close=Decimal("10.00"), available=Decimal("10000"),
                      position_qty=0, sellable_qty=0, market="CN_A", market_rule=RULE)


def test_halt_rejects_with_reason():
    inst = {"code": "600519", "limit_up_pct": Decimal("0.10"), "limit_down_pct": Decimal("0.10"),
            "lot_size": 100, "halted": True, "halted_reason": "重大事项停牌",
            "halted_source": "manual"}
    out = evaluate(_inputs(inst))
    assert not out.passed
    assert out.failed_names[0] == "halt"
    assert "停牌" in out.decline_reason and "重大事项停牌" in out.decline_reason


def test_not_halted_runs_all_rules():
    inst = {"code": "600519", "limit_up_pct": Decimal("0.10"), "limit_down_pct": Decimal("0.10"),
            "lot_size": 100, "halted": False}
    out = evaluate(_inputs(inst))
    assert out.passed and out.result_of("halt").ok


# ════════════════════════════════════════════════════════════════════════
# 三 · 端到端（需要账本库）
# ════════════════════════════════════════════════════════════════════════

os.environ.setdefault("HUNTER_INTERNAL_KEY", "test-internal-key")
os.environ.setdefault("PAPER_MODE", "PAPER")

from helpers import install_quote_source, order_body, seed_reference, uniq_when  # noqa: E402

from app import ledger, recon  # noqa: E402

pytest.importorskip("psycopg2")
if not os.getenv("PAPER_TEST_DSN"):
    pytest.skip("未设置 PAPER_TEST_DSN，跳过 L05 端到端用例", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
H = {"X-Hunter-Internal-Key": "test-internal-key"}


def _code_for(project_id: str) -> str:
    n = int(project_id.rsplit("_", 1)[-1], 16) % 900000
    return f"6{n + 100000:06d}"


def _ready_home(pg, project):
    """本币 A 股项目：参考数据 + 报价 + 入金。返回 (code, when)。"""
    code = _code_for(project)
    when = uniq_when(project)
    seed_reference(pg, code=code, trade_date=when[:10])
    pg.connection.commit()
    install_quote_source(when=when, code=code, price="10.00", ask1="10.00")
    assert client.post(f"/api/v1/projects/{project}/funding", headers=H, json={}).status_code == 200
    return code, when


def _post(path, body):
    return client.post(path, headers=H, json=body)


@pytest.mark.db
def test_halt_endpoint_then_order_rejected(pg, project):
    """人工登记停牌 → 下单被拒（理由写清「停牌」）→ 解除后又可下单。"""
    code, _ = _ready_home(pg, project)

    r = _post(f"/api/v1/instruments/{code}/halt", {"halted": True, "reason": "重大资产重组", "actor": "tester"})
    assert r.status_code == 200, r.text
    assert r.json()["halted"] is True and r.json()["halted_source"] == "manual"

    body = _post("/api/v1/orders", order_body(project, code=code, qty=100)).json()
    assert body["status"] == "rejected", body
    assert "停牌" in body["decline_reason"] and "重大资产重组" in body["decline_reason"]

    # 解除停牌 → 同一笔可正常成交
    assert _post(f"/api/v1/instruments/{code}/halt", {"halted": False}).json()["halted"] is False
    ok = _post("/api/v1/orders", order_body(project, code=code, qty=100)).json()
    assert ok["status"] == "filled", ok


@pytest.mark.db
def test_dividend_credits_cash(pg, project):
    """分红：现金入账，持仓不变；事件行 source=manual。"""
    code, _ = _ready_home(pg, project)
    assert _post("/api/v1/orders", order_body(project, code=code, qty=100)).json()["status"] == "filled"
    cash_before = ledger.cash_balance(pg, project, "CN_A")[0]

    r = _post("/api/v1/corporate-actions", {
        "project_id": project, "code": code, "action_type": "dividend",
        "ex_date": "2026-10-05", "cash_per_share": "0.50", "actor": "tester"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["cash_delta"] == "50.0000" and out["qty_delta"] == 0
    assert out["source"] == "manual"

    cash_after = ledger.cash_balance(pg, project, "CN_A")[0]
    assert cash_after == cash_before + Decimal("50.0000")
    # 持仓不变
    pg.execute("SELECT qty FROM fin_position WHERE project_id = %s AND code = %s", (project, code))
    assert int(pg.fetchone()["qty"]) == 100
    # 流水里有一条 adjust
    entries = ledger.list_cash(pg, project, market="CN_A")
    assert any(e["kind"] == "adjust" and "分红" in (e["memo"] or "") for e in entries)


@pytest.mark.db
def test_split_adjusts_qty_and_cost_and_recon_passes(pg, project):
    """拆股 2:1：股数 ×2、成本价 ÷2、总成本不变；对账（持仓 = 成交净额 + 公司行为）通过。"""
    code, _ = _ready_home(pg, project)
    assert _post("/api/v1/orders", order_body(project, code=code, qty=100)).json()["status"] == "filled"
    pg.execute("SELECT qty, avg_cost FROM fin_position WHERE project_id=%s AND code=%s", (project, code))
    before = pg.fetchone()
    assert int(before["qty"]) == 100 and str(before["avg_cost"]) == "10.0000"

    r = _post("/api/v1/corporate-actions", {
        "project_id": project, "code": code, "action_type": "split",
        "ex_date": "2026-10-05", "ratio": "2"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["qty_delta"] == 100
    assert out["position_after"]["qty"] == 200
    assert out["position_after"]["avg_cost"] == "5.0000"     # 1000 / 200

    # 对账：持仓净额把 qty_delta 算进去 → 通过
    result = recon.run(pg, project, datetime.now(timezone.utc), market="CN_A")
    assert result["passed"], [c for c in result["checks"] if not c["passed"]]


@pytest.mark.db
def test_bonus_dilutes_cost(pg, project):
    """10 送 3：股数 ×1.3（向下取整整股），成本价按新股数摊薄。"""
    code, _ = _ready_home(pg, project)
    assert _post("/api/v1/orders", order_body(project, code=code, qty=100)).json()["status"] == "filled"
    r = _post("/api/v1/corporate-actions", {
        "project_id": project, "code": code, "action_type": "bonus",
        "ex_date": "2026-10-05", "ratio": "0.3"})
    out = r.json()
    assert out["position_after"]["qty"] == 130
    assert out["position_after"]["avg_cost"] == "7.6923"     # 1000 / 130 → 4 位


@pytest.mark.db
def test_receipt_carries_market_tick(pg, project):
    """下单回执带该市场实际用的 tick（CN_A = 0.01，来自市场 tick_spec）。"""
    code, _ = _ready_home(pg, project)
    body = _post("/api/v1/orders", order_body(project, code=code, qty=100, price="10.00",
                                              price_type="market")).json()
    assert body["status"] == "filled", body
    assert Decimal(str(body["tick_size"])) == Decimal("0.01")


@pytest.mark.db
def test_hk_order_receipt_uses_hk_band_tick(pg, project):
    """港股的 tick 按价位查档（421.2 → 0.2）—— 回执里的 tick 与 A 股不同。"""
    code = "00700"
    when = uniq_when(project)
    # HK 标的 + 日历 + 费用模型（限价单必做；HK 无盘口，市价单拒）
    pg.execute(
        "INSERT INTO fin_instrument (code,name,exchange,board,is_st,limit_up_pct,limit_down_pct,"
        "lot_size,market,currency,source) VALUES (%s,'腾讯','HKEX','hk_main',false,NULL,NULL,1,'HK','HKD','test') "
        "ON CONFLICT (code) DO UPDATE SET market='HK', currency='HKD', lot_size=1",
        (code,))
    pg.execute(
        "INSERT INTO fin_market_calendar (market,trade_date,is_trading,sessions) VALUES "
        "('HK',%s,true,'[{\"open\":\"09:30\",\"close\":\"12:00\"},{\"open\":\"13:00\",\"close\":\"16:00\"}]') "
        "ON CONFLICT (market,trade_date) DO UPDATE SET is_trading=true",
        (when[:10],))
    pg.execute(
        "INSERT INTO fin_fee_model (version,commission_pct,commission_min,stamp_tax_pct,"
        "transfer_fee_pct,market,currency,stamp_side) VALUES "
        "('fee-hk-l05',0.0003,0,0.001,0.00006,'HK','HKD','both') ON CONFLICT (version) DO NOTHING")
    pg.connection.commit()
    install_quote_source(when=when, code=code, price="421.20", prev_close="421.20", market="HK",
                         quote_quality="last_only")
    assert client.post(f"/api/v1/projects/{project}/funding?market=HK", headers=H,
                       json={}).status_code == 200

    body = _post("/api/v1/orders", order_body(project, code=code, qty=10, price="421.20")).json()
    assert body["status"] == "filled", body
    assert body["currency"] == "HKD", body
    assert Decimal(str(body["tick_size"])) == Decimal("0.2"), body
