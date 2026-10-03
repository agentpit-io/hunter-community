# -*- coding: utf-8 -*-
"""P3 · 「界面按已选市场出」的两个后端读模型（真库；`TEST_DATABASE_URL` 未设时整体 skip）。

P3 是界面轮，但界面要「按市场出参数」就必须**从表里读**（三期红线 2：不许拿 A 股规则顶替港美股）。
这一轮为此加了**两个只读**的读模型，这个文件就盯它们：

1. `markets_svc.market_rules(cur)` + `GET /api/v1/fin/markets` 的 `rule` / `constraints`
   —— 向导「选市场」步与自动交易页的时点、涨跌停模式、T+N、整手、费率都从这里取。
   **逐字段与 `fin_market_rule` 那三行对照**，防止有人把中文/默认值写死回前端。
2. `markets_svc.project_market_accounts(cur, project_id)` + `GET /api/v1/fin/account` 的
   `market_accounts` —— 「市场与子账户」卡片只列**这个项目选中的**市场。
   断言：三市场项目回三行、单市场项目只回一行（**不回落 A 股**）、
   没有估值的市场 `nav` 是 `None`（**不补 0**）。

跑法（指向一个**已跑过 0037 迁移的库**，不要指向在用的库）::

    TEST_DATABASE_URL=postgresql://hunter:hunter@127.0.0.1:5598/p3_test \\
      cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_markets_rule_view.py -q
"""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

# ── sys.path 归一（同 test_fin_market_scope.py 的说明）────────────────────
_API_ROOT = Path(__file__).resolve().parents[1]
for _p in ("", "/", str(_API_ROOT)):
    while _p in sys.path:
        sys.path.remove(_p)
sys.path.insert(0, str(_API_ROOT))
_bad_app = sys.modules.get("app")
if _bad_app is not None and Path(getattr(_bad_app, "__file__", "") or "").parent == _API_ROOT:
    del sys.modules["app"]

psycopg2 = pytest.importorskip("psycopg2")
import psycopg2.extras  # noqa: E402

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="需要 TEST_DATABASE_URL 指向一个已跑过 0037 迁移的 postgres（见 test_migrate.py）",
)

MARKETS = ("CN_A", "HK", "US")


@pytest.fixture
def conn():
    c = psycopg2.connect(TEST_DATABASE_URL)
    c.autocommit = True
    try:
        yield c
    finally:
        c.close()


def _q(conn, sql, args=None):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, args)
        return cur.fetchall()


# ════════════════════════════════════════════════════════════════════════
# ① market_rules：逐字段与 fin_market_rule 对照
# ════════════════════════════════════════════════════════════════════════
def test_市场规则事实与表逐字段一致(conn):
    """`market_rules()` 回的每个字段都必须**等于表里那一列** —— 界面据此显示，不能自己编。"""
    from app.services.fin import markets as svc

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        got = svc.market_rules(cur)

    assert set(got) == set(MARKETS), f"应恰有 {MARKETS}，实为 {sorted(got)}"
    for m in MARKETS:
        row = _q(conn, "SELECT * FROM fin_market_rule WHERE market = %s", (m,))[0]
        for col in ("points", "sellable_rule", "sellable_days", "lot_rule", "lot_fixed",
                    "price_limit_mode", "market_order_supported", "fee_model_version"):
            assert got[m][col] == row[col], f"{m}.{col}: {got[m][col]!r} != {row[col]!r}"


def test_三市场的关键差异与现状一致(conn):
    """把三条最容易写反的差异钉住（改口径要一起改用例）。"""
    from app.services.fin import markets as svc

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        r = svc.market_rules(cur)

    # 只有 A 股接市价单（0029/0037 的既有口径，港美股只接限价单）
    assert r["CN_A"]["market_order_supported"] is True
    assert r["HK"]["market_order_supported"] is False
    assert r["US"]["market_order_supported"] is False
    # 涨跌停模式：A 股按板块百分比，港美股 none（**不做价格带校验**，界面标「未做」的依据）
    assert r["CN_A"]["price_limit_mode"] == "pct"
    assert r["HK"]["price_limit_mode"] == "none"
    assert r["US"]["price_limit_mode"] == "none"
    # 可卖规则：A 股 T+1，港美股 T+0
    assert r["CN_A"]["sellable_rule"] == "t_plus_n" and r["CN_A"]["sellable_days"] == 1
    assert r["HK"]["sellable_rule"] == "same_day"
    assert r["US"]["sellable_rule"] == "same_day"
    # 整手：A 股固定 100；港股按标的（**没有固定值**）；美股 1 股起
    assert r["CN_A"]["lot_rule"] == "fixed" and r["CN_A"]["lot_fixed"] == 100
    assert r["HK"]["lot_rule"] == "per_instrument"
    assert r["US"]["lot_rule"] == "one" and r["US"]["lot_fixed"] == 1
    # 交易时点：三市场各六个（向导第 6 步按市场出时点的依据）
    for m in MARKETS:
        assert len(r[m]["points"]) == 6, f"{m} 的时点不是 6 个：{r[m]['points']}"


def test_查不到规则的市场不出现在结果里_不回落A股(conn):
    """`market_rules()` 只回表里有的市场 —— 查不到就不出现，**不替它编一行 A 股**。"""
    from app.services.fin import markets as svc

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        got = svc.market_rules(cur)
    assert "JP" not in got
    assert set(got).issubset(set(MARKETS))


# ════════════════════════════════════════════════════════════════════════
# ② project_market_accounts：只列选中的市场
# ════════════════════════════════════════════════════════════════════════
@pytest.fixture()
def project():
    """建一个三市场的项目（`manage` 档）；跑完把它的行全清掉。"""
    from app.services.fin import store as fin_store

    _prev = fin_store.DATABASE_URL
    fin_store.DATABASE_URL = TEST_DATABASE_URL
    uid = "p3-" + uuid.uuid4().hex
    made = fin_store.create_project(uid, "manage", markets=["US", "CN_A", "HK"])
    pid = made["project"]["project_id"]
    try:
        yield {"uid": uid, "project_id": pid, "capital": Decimal(str(made["project"]["initial_capital"]))}
    finally:
        c = psycopg2.connect(TEST_DATABASE_URL)
        try:
            with c.cursor() as cur:
                cur.execute("DELETE FROM fin_valuation WHERE project_id = %s", (pid,))
                cur.execute("DELETE FROM fin_project_market WHERE project_id = %s", (pid,))
                cur.execute("DELETE FROM fin_param_change_log WHERE project_id = %s", (pid,))
                cur.execute("DELETE FROM fin_param WHERE project_id = %s", (pid,))
                cur.execute("DELETE FROM fin_project WHERE project_id = %s", (pid,))
                cur.execute("DELETE FROM fin_account WHERE user_id = %s", (uid,))
            c.commit()
        finally:
            c.close()
        fin_store.DATABASE_URL = _prev


def test_三市场项目回三行_顺序固定(conn, project):
    from app.services.fin import markets as svc

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        rows = svc.project_market_accounts(cur, project["project_id"])

    assert [r["market"] for r in rows] == list(MARKETS), "顺序恒为 CN_A → HK → US"
    by_m = {r["market"]: r for r in rows}
    assert by_m["CN_A"]["currency"] == "CNY"
    assert by_m["HK"]["currency"] == "HKD"
    assert by_m["US"]["currency"] == "USD"
    # 每市场各一份档位本金（用户口径）
    for m in MARKETS:
        assert by_m[m]["initial_capital"] == float(project["capital"]), f"{m} 本金不等于档位金额"
    # 还没有估值 → nav 是 None（**不补 0**）
    for m in MARKETS:
        assert by_m[m]["nav"] is None and by_m[m]["total_assets"] is None


def test_没估值的市场nav为None_有估值才给数(conn, project):
    """写一行 HK 估值 → 只有香港那一行带上净值，其余两个市场仍是 None。"""
    from app.services.fin import markets as svc

    pid = project["project_id"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO fin_valuation (project_id, market, currency, as_of,
                                       cash_available, cash_frozen, market_value,
                                       total_assets, nav, price_source, quality)
            VALUES (%s, 'HK', 'HKD', '2026-10-02 08:15:00+00', 1000, 0, 99000,
                    100000, 1.0, 'snapshot', 'ok')
            """, (pid,))

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        rows = {r["market"]: r for r in svc.project_market_accounts(cur, pid)}

    assert rows["HK"]["nav"] == 1.0
    assert rows["HK"]["total_assets"] == 100000.0
    assert rows["HK"]["as_of"].startswith("2026-10-02")
    assert rows["CN_A"]["nav"] is None
    assert rows["US"]["nav"] is None


def test_单市场项目只回一行_不回落A股(conn):
    """只选港股的项目**只回港股一行** —— 这是「界面只列已选市场」的后端保证。"""
    from app.services.fin import markets as svc
    from app.services.fin import store as fin_store

    _prev = fin_store.DATABASE_URL
    fin_store.DATABASE_URL = TEST_DATABASE_URL
    uid = "p3-" + uuid.uuid4().hex
    made = fin_store.create_project(uid, "manage", markets=["HK"])
    pid = made["project"]["project_id"]
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            rows = svc.project_market_accounts(cur, pid)
        assert [r["market"] for r in rows] == ["HK"]
        assert rows[0]["currency"] == "HKD"
    finally:
        c = psycopg2.connect(TEST_DATABASE_URL)
        try:
            with c.cursor() as cur:
                cur.execute("DELETE FROM fin_project_market WHERE project_id = %s", (pid,))
                cur.execute("DELETE FROM fin_param_change_log WHERE project_id = %s", (pid,))
                cur.execute("DELETE FROM fin_param WHERE project_id = %s", (pid,))
                cur.execute("DELETE FROM fin_project WHERE project_id = %s", (pid,))
                cur.execute("DELETE FROM fin_account WHERE user_id = %s", (uid,))
            c.commit()
        finally:
            c.close()
        fin_store.DATABASE_URL = _prev


def test_没有市场行的项目回空列表(conn):
    """`fin_project_market` 里没有这个项目 → 空列表（**不回落 market_scope**）。"""
    from app.services.fin import markets as svc

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        rows = svc.project_market_accounts(cur, "prj_p3_does_not_exist")
    assert rows == []


# ════════════════════════════════════════════════════════════════════════
# ③ 两条 HTTP 路径真的带上去了（`/markets` 与 `/account`）
# ════════════════════════════════════════════════════════════════════════
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _client_for_uid(uid: str) -> TestClient:
    """挂假身份中间件 + 三个 fin 路由；`DATABASE_URL` 指向测试库。"""
    from app.routers import fin_dashboard, fin_project  # noqa: E402
    from app.services.fin import store as fin_store  # noqa: E402

    fin_store.DATABASE_URL = TEST_DATABASE_URL
    app = FastAPI()

    @app.middleware("http")
    async def _fake_auth(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or uid
        return await call_next(request)

    app.include_router(fin_dashboard.router, prefix="/api")
    app.include_router(fin_project.router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def test_HTTP_markets带rule与constraints(project):
    """向导「选市场」步的三张卡片就靠这个接口 —— 每项必须有 `rule` 与六条 `constraints`。"""
    cli = _client_for_uid(project["uid"])
    r = cli.get("/api/v1/fin/markets")
    assert r.status_code == 200, r.text
    items = {m["market"]: m for m in r.json()["markets"]}
    assert set(items) == set(MARKETS)
    for m, it in items.items():
        assert isinstance(it.get("rule"), dict) and it["rule"], f"{m} 没有 rule"
        assert len(it.get("constraints") or []) == 6, f"{m} 的 constraints 不是六条"
        for c in it["constraints"]:
            assert c.get("title") and c.get("text"), f"{m} 的 constraint 缺字段"

    # 港美股的价格带那一条必须**明说「未做」**（红线：不许静默跳过）
    for m in ("HK", "US"):
        limit = next(c for c in items[m]["constraints"] if c["key"] == "limit")
        assert "未做" in limit["title"] or "未做" in limit["text"], f"{m} 没标「未做」"
    # A 股那条不该有「未做」
    cn_limit = next(c for c in items["CN_A"]["constraints"] if c["key"] == "limit")
    assert "未做" not in cn_limit["text"]

    # 既有字段一个没少（只加字段的兼容性）
    assert items["CN_A"]["currency"] == "CNY"
    assert items["CN_A"]["timezone"] == "Asia/Shanghai"
    assert isinstance(items["CN_A"]["sessions"], list) and items["CN_A"]["sessions"]


def test_HTTP_account带market_accounts(project):
    """「我的账户」页的「市场与子账户」卡片读的就是它。"""
    cli = _client_for_uid(project["uid"])
    r = cli.get("/api/v1/fin/account?market=HK")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [x["market"] for x in body["market_accounts"]] == list(MARKETS)
    assert body["market_accounts"][1]["currency"] == "HKD"
    # 既有字段还在
    assert "constraints" in body and "markets" in body and body["project"]["tier"] == "manage"


def test_A股费用卡片用的是A股费率行_不拿别的市场冒充(project):
    """A 股「费用」卡片必须引 `fee-cn-a-v1`，**不许拿别的市场的费率行顶**。

    2026-10-03 演示站实测：`fin_fee_model` 缺 `CN_A` 行时，`_fee_model` 原来的
    「回落最新一行」取到字典序最大的 `fee-us-v1`，把美股费率
    （佣金万1 / 印花税 — / 过户费万0.2）当成 A 股费率渲染了出来 —— 用户看到的
    是**编的** A 股费率。现在列在、缺行 → 返回 None（降级成「费率行尚未配置」），
    绝不再跨市场回落。这条把「来源行」与「文案里的数字」一起钉死。
    """
    cli = _client_for_uid(project["uid"])
    items = {m["market"]: m for m in cli.get("/api/v1/fin/markets").json()["markets"]}
    fee = next(c for c in items["CN_A"]["constraints"] if c["key"] == "fee")
    assert fee.get("source") == "fin_fee_model:fee-cn-a-v1", fee
    # A 股费率的形状：佣金万2.5、最低 5 元、印花税千0.5 —— 不是美股那套
    assert "万分之 2.5" in fee["text"], fee["text"]
    assert "印花税 —" not in fee["text"], fee["text"]


def test_缺该市场费率行时返回None而不是别的市场的行(project):
    """**列在、行缺** → `_fee_model` 返回 `None`，绝不回落取别的市场的行。

    这才是 2026-10-03 演示站那个缺陷的直接复现：删掉 A 股费率行后，旧代码的
    `ORDER BY version DESC LIMIT 1` 会取到字典序最大的 `fee-us-v1`，把它当 A 股费率。
    现在必须返回 None（`constraints` 于是渲染「费率行尚未配置」的降级文案）。
    """
    from app.services.fin import views

    conn = psycopg2.connect(os.environ["TEST_DATABASE_URL"])
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM fin_fee_model WHERE market = 'CN_A'")
        with conn.cursor() as cur:
            assert views._fee_model(cur, "CN_A") is None, "缺行时不许回落取别的市场"
            fee = next(c for c in views.constraints(cur, "CN_A") if c["key"] == "fee")
            assert fee["source"] is None, fee
            assert "尚未配置" in fee["text"], fee["text"]
            # 美股费率（佣金万1 / 过户费万0.2）一个字都不许出现
            assert "万分之 1" not in fee["text"] and "万分之 0.2" not in fee["text"], fee["text"]
    finally:
        conn.rollback()          # 别把测试库改脏（其它用例还要用这一行）
        conn.close()
