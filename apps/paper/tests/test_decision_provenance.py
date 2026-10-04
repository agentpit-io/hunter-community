"""L03 · 成交行的决策「出身证」—— `execution_model_version` / `mode` / `portfolio_version`。

真跑一笔成交（走真实 HTTP 路由：鉴权 → 取快照 → 撮合 → 记账），断言成交行上三样的**真值**；
再断言这张快照的 `available_at` / `revision_id` **确实是 `NULL`** —— 数据源不给这两个时刻，
只能留空（红线 5：不许拿 `now()` / 自增号冒充）。

走 HTTP 而不是直接调函数，是为了让路由、鉴权、序列化一起验到；报价走
`install_quote_source`（换的是行情来源，不是被测逻辑），链路与线上逐行相同。
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("HUNTER_INTERNAL_KEY", "test-internal-key")
os.environ.setdefault("PAPER_MODE", "PAPER")

from helpers import (  # noqa: E402
    install_quote_source,
    order_body,
    seed_reference,
    uniq_date,
    uniq_when,
)

pytestmark = pytest.mark.db

pytest.importorskip("psycopg2")
if not os.getenv("PAPER_TEST_DSN"):
    pytest.skip("未设置 PAPER_TEST_DSN，跳过决策出身证用例", allow_module_level=True)

from app.main import app  # noqa: E402

client = TestClient(app)
H = {"X-Hunter-Internal-Key": "test-internal-key"}


def _code_for(project_id: str) -> str:
    """由 project_id 派一个**纯数字**的独有代码（6 位，归 CN_A）。

    快照 / 估值按「该代码 ≤ as_of 的最新快照」取价，而 `fin_snapshot` 全库共享 ——
    每个用例一个独有代码，才只看得到自己那张快照（与 `test_ledger_e2e` 同思路）。
    """
    n = int(project_id.rsplit("_", 1)[-1], 16) % 900000
    return f"6{n + 100000:06d}"


@pytest.fixture
def ready(pg, project):
    """干净项目 + 参考数据 + 独有代码 + 独有报价时刻 + 入金。返回 `(project_id, code)`。"""
    code = _code_for(project)
    seed_reference(pg, code=code, trade_date=uniq_date(project))
    pg.connection.commit()
    install_quote_source(when=uniq_when(project), code=code)
    r = client.post(f"/api/v1/projects/{project}/funding", json={}, headers=H)
    assert r.status_code == 200, r.text
    return project, code


def test_trade_row_carries_execution_model_version_mode_portfolio_version(ready, pg):
    """成交行记录：撮合用的执行模型版本 + 显式 PAPER + 本笔应用在的账户版本。"""
    project, code = ready
    before = client.get(f"/api/v1/projects/{project}", headers=H).json()
    before_version = int(before["project"]["version"])

    r = client.post("/api/v1/orders", json=order_body(project, code=code), headers=H)
    assert r.status_code == 200, r.text
    receipt = r.json()
    assert receipt["status"] == "filled", receipt

    pg.execute(
        "SELECT execution_model_version, mode, portfolio_version, fee_model_version "
        "  FROM fin_trade WHERE trade_id = %s",
        (receipt["trade_id"],),
    )
    row = pg.fetchone()
    pg.connection.rollback()
    assert row is not None

    # ① 执行模型版本 = 撮合时 `fin_execution_model` 里那一行（seed_reference 种的 paper-model-v1）。
    #    与 fee_model_version 同一口径（两者都是 Not NULL 的真值，不是编的）。
    assert row["execution_model_version"] == "paper-model-v1"
    assert row["fee_model_version"] == "fee-cn-a-v1"
    # ② 显式模式：本服务恒 PAPER。
    assert row["mode"] == "PAPER"
    # ③ portfolio_version = 本笔**应用在**的那一版账户（bump_version **之前**的取值）——
    #    入金不 bump，所以成交前若为空账本，这里就是 0。
    assert row["portfolio_version"] == before_version
    # 成交后账本版本 +1（bump_version），可见 portfolio_version 记的是「旧版」。
    after = client.get(f"/api/v1/projects/{project}", headers=H).json()
    assert int(after["project"]["version"]) == before_version + 1


def test_execution_model_version_tracks_the_seeded_model_row(ready, pg):
    """`execution_model_version` 指向 `fin_execution_model` 里**真实存在**的那一行（可反查）。"""
    project, code = ready
    r = client.post("/api/v1/orders", json=order_body(project, code=code), headers=H)
    assert r.status_code == 200, r.text
    receipt = r.json()

    pg.execute(
        "SELECT t.execution_model_version, m.version AS model_row "
        "  FROM fin_trade t "
        "  LEFT JOIN fin_execution_model m ON m.version = t.execution_model_version "
        " WHERE t.trade_id = %s",
        (receipt["trade_id"],),
    )
    row = pg.fetchone()
    pg.connection.rollback()
    assert row is not None
    # JOIN 命中（model_row 非空）= 记的版本在执行模型表里真的存在（不是随手写的字符串）。
    assert row["execution_model_version"] == "paper-model-v1"
    assert row["model_row"] == "paper-model-v1"


def test_trades_view_exposes_the_provenance_columns(ready):
    """读取路径（`list_trades` 的 SELECT）也带上了三列 —— 否则落了库也看不见。"""
    project, code = ready
    r = client.post("/api/v1/orders", json=order_body(project, code=code), headers=H)
    assert r.status_code == 200, r.text
    item = client.get(f"/api/v1/projects/{project}/trades", headers=H).json()["items"][0]
    assert item["execution_model_version"] == "paper-model-v1"
    assert item["mode"] == "PAPER"
    assert "portfolio_version" in item


def test_snapshot_time_fields_are_null_when_source_does_not_report_them(ready, pg):
    """行情快照的 `available_at` / `revision_id` **确实是 NULL**。

    为什么只能 NULL：`Quote`（数据源返回体）里**没有**这两个时刻 —— 腾讯 / hunter 网关
    不返回「实际可获取时间」，也不返回「数据修订号」。拿不到真值就留空（红线 5），
    **不拿 `created_at` / `now()` 冒充**。这条断言盯的就是「没有偷偷填」。
    """
    project, code = ready
    r = client.post("/api/v1/orders", json=order_body(project, code=code), headers=H)
    assert r.status_code == 200, r.text
    snap_id = r.json()["snapshot_id"]

    pg.execute(
        "SELECT snapshot_time, available_at, revision_id, created_at "
        "  FROM fin_snapshot WHERE snapshot_id = %s",
        (snap_id,),
    )
    row = pg.fetchone()
    pg.connection.rollback()
    assert row is not None
    # 事件时刻是真值（来自数据源），入库时刻也是真值。
    assert row["snapshot_time"] is not None
    assert row["created_at"] is not None
    # 数据源不给的两样：**必须是 NULL**（不是被冒充成 now()/created_at）。
    assert row["available_at"] is None
    assert row["revision_id"] is None
