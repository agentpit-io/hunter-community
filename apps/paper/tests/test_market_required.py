"""P1 · `MULTI` 项目的账本调用必须显式传 `market`（真 HTTP 路由 + 真库）。

由来：`apps/paper/app/ledger.py:resolve_market()` 原来把 `market_scope='MULTI'`
**静默兜底成 `CN_A`** —— 那是拿 A 股顶替港美股，撞「不许拿 A 股规则顶替港美股」这条红线。
现在 `MULTI` 项目不传 `market` 一律 **400**（「该项目有多个市场，请指定 market」）。

盯住两件事：

1. **单市场项目行为逐字节不变** —— `CN_A` / `HK` / `US` 项目不传 `market` 照旧
   （该市场就是唯一答案）。这条是「不许为了修 MULTI 把老路径改坏」的守门。
2. **`MULTI` 不传 market → 400，不是 200 落到 A 股** —— P1 出口标准 §三.7。

跑法（需要一个已跑过 0036 迁移的账本库，且用 `fin_paper_rw` 角色）::

    PAPER_TEST_DSN=postgresql://fin_paper_rw:...@127.0.0.1:5598/hunter \
      python -m pytest tests/test_market_required.py -q
"""

from __future__ import annotations

import os
import uuid

import pytest

os.environ.setdefault("HUNTER_INTERNAL_KEY", "test-internal-key")
os.environ.setdefault("PAPER_MODE", "PAPER")

pytestmark = pytest.mark.db

psycopg2 = pytest.importorskip("psycopg2")

_DSN = os.getenv("PAPER_TEST_DSN", "").strip()
if not _DSN:
    pytest.skip("未设置 PAPER_TEST_DSN，跳过 MULTI 市场必选用例", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from app import ledger  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
H = {"X-Hunter-Internal-Key": "test-internal-key"}


# ════════════════════════════════════════════════════════════════════════
# 一、纯函数（不连库）：判据只有一处 `require_market`
# ════════════════════════════════════════════════════════════════════════
def test_单市场项目_resolve_market_行为不变():
    for scope, cur in (("CN_A", "CNY"), ("HK", "HKD"), ("US", "USD")):
        assert ledger.resolve_market({"market_scope": scope}, None) == scope
    # 老口径：market_scope 认不出时兜底 CN_A（一期遗留，单市场路径不变）
    assert ledger.resolve_market({"market_scope": None}, None) == "CN_A"
    assert ledger.resolve_market(None, None) == "CN_A"


def test_MULTI项目_resolve_market_必须显式传market():
    with pytest.raises(ledger.MarketRequired) as exc:
        ledger.resolve_market({"market_scope": "MULTI"}, None)
    assert "请指定 market" in str(exc.value)
    # 显式传了就放行
    assert ledger.resolve_market({"market_scope": "MULTI"}, "HK") == "HK"
    assert ledger.resolve_market({"market_scope": "MULTI"}, "US") == "US"


def test_require_market_只看market_scope():
    # 单市场 / 无项目 → 不报错
    assert ledger.require_market({"market_scope": "CN_A"}, None) is None
    assert ledger.require_market(None, None) is None
    # MULTI → 报错；给了 market 就不报
    with pytest.raises(ledger.MarketRequired):
        ledger.require_market({"market_scope": "MULTI"}, None)
    assert ledger.require_market({"market_scope": "MULTI"}, "CN_A") is None


# ════════════════════════════════════════════════════════════════════════
# 二、真 HTTP 路由：MULTI 不传 market → 400
# ════════════════════════════════════════════════════════════════════════
def _insert_project(market_scope: str) -> str:
    """往库里插一个项目（只插 `fin_project` 一行，够路由判 market_scope 用）。"""
    pid = "prj_p1test_" + uuid.uuid4().hex[:20]
    uid = "p1test-" + uuid.uuid4().hex[:12]
    conn = psycopg2.connect(_DSN)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fin_project (project_id, user_id, tier, initial_capital,
                                         currency, market_scope)
                VALUES (%s, %s, 'play', 10000, %s, %s)
                """,
                (pid, uid, None if market_scope == "MULTI" else
                 {"CN_A": "CNY", "HK": "HKD", "US": "USD"}.get(market_scope, "CNY"),
                 market_scope),
            )
    finally:
        conn.close()
    return pid


def _cleanup(pid: str) -> None:  # noqa: ARG001  —— 见下面 fixture 的说明
    """**故意不删**：`fin_paper_rw` 没有任何表的 DELETE 权限（09 §一「不给 DELETE」）。

    账本侧用例的既有约定就是「测试库是一次性的，行只追加、删不掉」
    （见 `test_ledger_e2e.py` 文件头）。这里用随机 project_id，互不干扰。
    """


@pytest.fixture()
def multi_project():
    pid = _insert_project("MULTI")
    yield pid
    _cleanup(pid)


@pytest.fixture()
def single_project():
    pid = _insert_project("CN_A")
    yield pid
    _cleanup(pid)


def test_MULTI项目不传market_400(multi_project):
    """P1 出口标准 §三.7：**400，不是 200 落到 A 股**。"""
    r = client.get(f"/api/v1/projects/{multi_project}", headers=H)
    assert r.status_code == 400, r.text
    assert "请指定 market" in r.json()["detail"]


def test_MULTI项目传了market_正常(multi_project):
    r = client.get(f"/api/v1/projects/{multi_project}?market=HK", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["market"] == "HK"
    assert r.json()["currency"] == "HKD"


def test_MULTI项目各端点不传market都400(multi_project):
    """不是只在某一个接口上挡 —— 读 / 写端点一致。"""
    for method, path in (
        ("get", f"/api/v1/projects/{multi_project}/positions"),
        ("get", f"/api/v1/projects/{multi_project}/orders"),
        ("get", f"/api/v1/projects/{multi_project}/trades"),
        ("get", f"/api/v1/projects/{multi_project}/cash"),
        ("get", f"/api/v1/projects/{multi_project}/valuation/latest"),
        ("post", f"/api/v1/projects/{multi_project}/funding"),
    ):
        r = getattr(client, method)(path, headers=H)
        assert r.status_code == 400, f"{method.upper()} {path} → {r.status_code} {r.text}"
        assert "请指定 market" in r.json()["detail"]


def test_单市场项目不传market_行为不变(single_project):
    """A 股项目不传 `market` 照旧：落到 `CN_A`，不是 400。"""
    r = client.get(f"/api/v1/projects/{single_project}", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["market"] == "CN_A"
    assert r.json()["currency"] == "CNY"


def test_MULTI项目不传market的报错文案(multi_project):
    r = client.get(f"/api/v1/projects/{multi_project}/cash", headers=H)
    assert r.json()["detail"] == "该项目有多个市场，请指定 market"


# ════════════════════════════════════════════════════════════════════════
# P2 · 订单管理端点也按市场（多市场项目不传 market 一律 400）
#
# 由来：MULTI 项目从 P2 起会被**每个市场的时点各驱动一次**。原来 `confirm-t1` /
# `match-open` / `expire` 不收 `market`：
#   · `confirm-t1` 对 MULTI 项目直接 400（preopen 整条工作流失败，实测）；
#   · `match-open` / `expire` 会跨市场撮合 / 撤单（拿别市场的隔夜快照撮 A 股挂单）。
# ════════════════════════════════════════════════════════════════════════

def test_订单管理端点_MULTI不传market_400(multi_project):
    r = client.post(f"/api/v1/projects/{multi_project}/confirm-t1", headers=H)
    assert r.status_code == 400, r.text
    assert "请指定 market" in r.json()["detail"]
    r = client.post(f"/api/v1/projects/{multi_project}/orders/match-open", headers=H)
    assert r.status_code == 400, r.text
    r = client.post(f"/api/v1/projects/{multi_project}/orders/expire", headers=H,
                    json={"at": "2026-10-02T15:00:00+08:00", "reason": "close"})
    assert r.status_code == 400, r.text


def test_订单管理端点_MULTI传了market_正常(multi_project):
    r = client.post(f"/api/v1/projects/{multi_project}/confirm-t1?market=HK", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["positions_made_sellable"] == 0
    r = client.post(f"/api/v1/projects/{multi_project}/orders/match-open?market=HK", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["matched"] == 0


def test_订单管理端点_未知市场_400(multi_project):
    r = client.post(f"/api/v1/projects/{multi_project}/orders/match-open?market=XX",
                    headers=H)
    assert r.status_code == 400, r.text
    assert "未知市场" in r.json()["detail"]
