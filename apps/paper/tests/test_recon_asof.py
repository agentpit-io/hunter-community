"""对账 · 任意历史时点重跑 + 三项必查 + 不平告警（M7 · M-30）。

三项必查（`05 §3.4`）：
  1. 持仓市值 + 可用 + 冻结 = 总资产；
  2. 买入数量恒为 100 股整数倍；
  3. 成交合计与资金变动逐笔对得上。

以及「不平即告警」：不平 → `passed=false` + `fin_alert_log` 排一条待投递记录
（通道是 hunter 现有的 `notify-qq`，宿主脚本 `scripts/fin_recon_alert.sh` 投递）。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("HUNTER_INTERNAL_KEY", "test-internal-key")
os.environ.setdefault("PAPER_MODE", "PAPER")

pytestmark = pytest.mark.db

if not os.getenv("PAPER_TEST_DSN"):
    pytest.skip("未设置 PAPER_TEST_DSN，跳过需要账本库的用例", allow_module_level=True)

from helpers import (  # noqa: E402
    install_quote_source,
    order_body,
    seed_reference,
    uniq_date,
    uniq_when,
)

from app.main import app  # noqa: E402

CODE = "600010"          # 避开 M6 夹具用过的代码（见 test_ledger_e2e 的同一条注释）
client = TestClient(app)
H = {"X-Hunter-Internal-Key": "test-internal-key"}


def _post(path, body=None):
    r = client.post(path, json=body or {}, headers=H)
    return r.status_code, (r.json() if r.content else None)


@pytest.fixture
def ready(pg, project):
    seed_reference(pg, code=CODE, trade_date=uniq_date(project))
    pg.connection.commit()
    install_quote_source(when=uniq_when(project), code=CODE)
    assert _post(f"/api/v1/projects/{project}/funding")[0] == 200
    return project


def _recon(project_id, as_of):
    status, out = _post(f"/api/v1/projects/{project_id}/recon", {"as_of": as_of})
    assert status == 200, out
    return out


def _named(out, name):
    return next(c for c in out["checks"] if c["name"] == name)


def test_recon_three_required_checks_pass_at_now(ready):
    assert _post("/api/v1/orders", order_body(ready, code=CODE))[1]["status"] == "filled"
    as_of = datetime.now().astimezone().isoformat()
    out = _recon(ready, as_of)
    assert out["passed"] is True, [c for c in out["checks"] if not c["passed"]]
    for name in ("balance_equation", "buy_lot", "cash_ties_trades"):
        assert _named(out, name)["passed"] is True, _named(out, name)


def test_recon_replays_for_a_past_as_of_before_the_trade(ready):
    """**历史时点重跑**：把 as_of 拨到买入之前 → 那天的持仓与成交都是空的。

    这就是「任意时点」的意义：同一份账本，换个时间轴，读到的是那个时间点的状态。
    """
    _post("/api/v1/orders", order_body(ready, code=CODE))
    before = (datetime.fromisoformat(uniq_when(ready)) - timedelta(days=1)).isoformat()
    out = _recon(ready, before)
    assert out["passed"] is True, [c for c in out["checks"] if not c["passed"]]
    # 买入之前：没有任何成交
    assert _named(out, "buy_lot")["actual"] == "0 笔非整手"
    # 持仓市值与现金都是 0（项目还没开始动）
    assert _named(out, "cash_sum")["expected"] == "0.0000"


def test_recon_replays_for_a_past_as_of_after_the_trade(ready):
    """拨到买入之后（但仍是历史时点）：成交与资金都对得上。"""
    _post("/api/v1/orders", order_body(ready, code=CODE))
    after = (datetime.fromisoformat(uniq_when(ready)) + timedelta(hours=1)).isoformat()
    out = _recon(ready, after)
    assert out["passed"] is True, [c for c in out["checks"] if not c["passed"]]
    # 一笔成交，现金 = 10000 - 1000 - 5.01
    assert _named(out, "cash_sum")["expected"] == "8994.9900"
    # 历史时点：只有当前值可比的项标 skipped，不假装通过也不假装失败
    assert _named(out, "project_version")["skipped"] is True


def test_deliberate_imbalance_fails_and_queues_an_alert(ready, pg):
    """故意让「买入恒为整手」不成立 → 对账不过 + 排一条告警。

    扰动做得**最小**：只加一笔 150 股的买入（并把现金 / 持仓 / 版本都按它补齐），
    这样只有 `buy_lot` 一项该红 —— 告警指向的正是那条业务规则。
    """
    _, receipt = _post("/api/v1/orders", order_body(ready, code=CODE))
    assert receipt["status"] == "filled"

    pg.execute(
        "SELECT order_id, snapshot_id, price, traded_at FROM fin_trade "
        " WHERE project_id = %s LIMIT 1", (ready,))
    base = pg.fetchone()
    # 补齐现金 / 持仓 / 版本：让除 buy_lot 外的项仍然成立
    pg.execute(
        "SELECT available_after, frozen_after FROM fin_cash_ledger "
        " WHERE project_id = %s ORDER BY entry_id DESC LIMIT 1", (ready,))
    last = pg.fetchone()
    # id 从项目 id 派生：这份注入是**提交**的（对账要读它），固定 id 第二次跑会撞主键。
    new_trade = "trd_" + ready.removeprefix("prj_test_")
    pg.execute(
        """
        INSERT INTO fin_trade (trade_id, order_id, project_id, code, side, qty, price, amount,
                               commission, stamp_tax, transfer_fee, total_fee, snapshot_id,
                               source, fee_model_version, traded_at)
        VALUES (%s, %s, %s, %s, 'buy', 150, %s, 1500, 0, 0, 0, 0, %s, 'ai', 'fee-cn-a-v1', %s)
        """,
        (new_trade, base["order_id"], ready, CODE, base["price"], base["snapshot_id"],
         base["traded_at"]),
    )
    pg.execute(
        """
        INSERT INTO fin_cash_ledger (project_id, kind, amount, available_after,
                                     frozen_after, trade_id, memo)
        VALUES (%s, 'buy', -1500, %s, %s, %s, '测试注入的非整手买入')
        """,
        (ready, last["available_after"] - 1500, last["frozen_after"], new_trade),
    )
    pg.execute("UPDATE fin_position SET qty = qty + 150 WHERE project_id = %s", (ready,))
    pg.execute(
        "UPDATE fin_project SET version = (SELECT COUNT(*) FROM fin_trade WHERE project_id = %s)"
        " WHERE project_id = %s", (ready, ready))
    pg.connection.commit()

    as_of = datetime.now().astimezone().isoformat()
    out = _recon(ready, as_of)
    assert out["passed"] is False
    bad = [c["name"] for c in out["checks"] if not c["passed"]]
    assert bad == ["buy_lot"], bad
    assert _named(out, "buy_lot")["actual"] == "1 笔非整手"

    # 告警已排队（未投递）
    pg.execute(
        "SELECT id, kind, ref_id, channel, subject, sent_at FROM fin_alert_log "
        " WHERE ref_id = %s ORDER BY id DESC LIMIT 1", (out["id"],))
    alert = pg.fetchone()
    assert alert is not None and alert["channel"] == "notify-qq"
    assert alert["kind"] == "recon_failed" and alert["sent_at"] is None
    assert "账本不平" in alert["subject"]
