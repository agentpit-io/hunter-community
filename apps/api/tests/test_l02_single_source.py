"""L02 · 同一事实只留一份 —— 守护用例（改造后再漂就会红）。

四类守护：

1. **时段**：`market_sessions.py` 在 api / fin-worker 逐字节相同；且与**迁移种子**
   （`db/migrations/0030` + `0034`，即 `fin_market_rule.sessions` 的值）逐项一致。
2. **时区**：三条 `market_time.py` 逐字节相同（这条在 paper 的 parity 用例里）；
   这里再证 `ZoneInfo("Asia/Shanghai") == timezone(timedelta(hours=8))`（替换前后逐值一致），
   并做**源码扫描**：业务代码里不许再出现手写固定时区偏移。
3. **DDL**：`ensure_schema` 的重复定义已消除（`0027` / `0028` 为唯一来源）。
4. **真库**：`fin_market_rule.sessions` 与代码里的常量逐项一致（要 `TEST_DATABASE_URL`）。

跑法::

    cd apps/api && PYTHONPATH=. .venv/bin/python -m pytest tests/test_l02_single_source.py -q
    TEST_DATABASE_URL=postgresql://hunter:hunter@127.0.0.1:5598/<跑过迁移的库> \
      PYTHONPATH=. .venv/bin/python -m pytest tests/test_l02_single_source.py -q
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("psycopg2")

_THIS = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.normpath(os.path.join(_THIS, "..", "..", ".."))

_API_SESSIONS = os.path.join(_REPO, "apps", "api", "app", "services", "fin", "market_sessions.py")
_FW_SESSIONS = os.path.join(_REPO, "apps", "fin-worker", "app", "market_sessions.py")
_MIG_0030 = os.path.join(_REPO, "db", "migrations", "0030_market_rule.sql")
_MIG_0034 = os.path.join(_REPO, "db", "migrations", "0034_schedule_ledger_market.sql")

from app.services.fin import market_calendar, market_sessions  # noqa: E402  业务侧同一来源

_CN = [{"open": "09:30", "close": "11:30"}, {"open": "13:00", "close": "15:00"}]
_HK_BASE = [{"open": "09:30", "close": "12:00"}, {"open": "13:00", "close": "16:00"}]
_HK_CAS = [{"open": "16:00", "close": "16:10"}]
_HK = _HK_BASE + _HK_CAS
_US = [{"open": "09:30", "close": "16:00"}]


def _compact(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _sql_compact(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return re.sub(r"\s+", "", fh.read())


# ── 1 · 时段：两份逐字节相同 + 与迁移种子逐项一致 ──────────────────────────

def test_market_sessions_modules_byte_identical():
    with open(_API_SESSIONS, "rb") as a, open(_FW_SESSIONS, "rb") as b:
        assert a.read() == b.read(), (
            "apps/api/.../market_sessions.py 与 apps/fin-worker/app/market_sessions.py "
            "不一致：同一份事实的两个镜像必须逐字节相同（改一处必须改两处）"
        )


def test_market_sessions_values():
    assert market_sessions.MARKET_SESSIONS == {"CN_A": _CN, "HK": _HK, "US": _US}
    assert market_sessions.A_SESSIONS == _CN


def test_market_sessions_match_migration_seed():
    """常量必须与 `fin_market_rule.sessions` 的迁移种子逐项一致。

    0030 种下持续交易时段，0034 给港股补上收市竞价 16:00–16:10（CAS）。
    改任一处、漏另一处，这里就红。
    """
    s030 = _sql_compact(_MIG_0030)
    s034 = _sql_compact(_MIG_0034)
    assert _compact(_CN) in s030, "0030 里找不到 A 股时段种子（与常量不一致）"
    assert _compact(_HK_BASE) in s030, "0030 里找不到港股持续交易时段种子"
    assert _compact(_US) in s030, "0030 里找不到美股时段种子"
    assert _compact(_HK_CAS) in s034, "0034 里找不到港股收市竞价 16:00–16:10 的补丁"
    # 结构明确：常量里的港股 = 0030 的持续时段 + 0034 补的收市竞价
    assert _HK == _HK_BASE + _HK_CAS


def test_market_calendar_sessions_come_from_single_module():
    """api 的 `market_calendar.MARKET_SESSIONS` 是**由单一来源导出的 HK/US 子集**，
    不许再各写一份字面量。"""
    assert market_calendar.MARKET_SESSIONS == {"HK": _HK, "US": _US}
    assert market_calendar.MARKET_SESSIONS["HK"] is market_sessions.MARKET_SESSIONS["HK"]


# ── 2 · 时区：值等价 + 源码扫描 ────────────────────────────────────────────

def test_shanghai_zoneinfo_equals_fixed_offset():
    """`ZoneInfo("Asia/Shanghai")` 与 `timezone(timedelta(hours=8))` 逐时刻同偏移。

    这是「改前 / 改后读出来是同一个值」的机器证明（替换的正是这类常量）。
    """
    old = timezone(timedelta(hours=8))
    new = ZoneInfo("Asia/Shanghai")
    for s in ("2026-01-15T00:00:00+00:00", "2026-06-15T12:00:00+00:00",
              "2026-03-08T07:01:00+00:00", "2026-11-01T06:00:00+00:00",
              "2026-12-31T16:00:00+00:00"):
        at = datetime.fromisoformat(s)
        assert old.utcoffset(at) == new.utcoffset(at)
        assert at.astimezone(old) == at.astimezone(new)


# 手写固定时区偏移的反模式：`timezone(timedelta(hours=...))` / `astimezone(timedelta(...))`
_OFFSET_ANTI = re.compile(
    r"(?:timezone|astimezone)\s*\(\s*(?:[\w]+\.)?timedelta\s*\(\s*hours\s*=")


def _business_files(app: str):
    root = os.path.join(_REPO, "apps", app)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".venv", "tests", "__pycache__")]
        if app == "api" and os.path.basename(dirpath) == "tests":
            continue
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


@pytest.mark.parametrize("app", ["api", "fin-worker", "paper"])
def test_no_manual_timezone_offset_in_business_code(app):
    """业务代码里不许再手写 `timezone(timedelta(hours=±N))`（M7 事故根因）。

    时长（如 `STALE_AFTER = timedelta(hours=4)`）不在本规则的射程内 —— 它不是一个
    时区；本规则只抓「用固定偏移当 tzinfo / astimezone 参数」。
    """
    bad = []
    for path in _business_files(app):
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if _OFFSET_ANTI.search(line):
                    bad.append(f"{os.path.relpath(path, _REPO)}:{i}")
    assert not bad, "业务代码里还有手写固定时区偏移，应改走 market_time：\n" + "\n".join(bad)


def test_api_business_has_no_hours_offset_at_all():
    """api 侧更强：业务代码里**一处** `timedelta(hours=` 都不许有（L02 已清零）。"""
    bad = []
    for path in _business_files("api"):
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if "timedelta(hours=" in line:
                    bad.append(f"{os.path.relpath(path, _REPO)}:{i}")
    assert not bad, "api 业务代码里还有 timedelta(hours=，应改走 market_time：\n" + "\n".join(bad)


_TZ_NAME_LITERAL = re.compile(r"""['"](Asia/Shanghai|Asia/Hong_Kong|America/New_York)['"]""")
_MARKET_TIME_MODULE = os.path.join("app", "services", "market_time.py")


def test_api_tz_name_literal_only_in_market_time():
    """api 业务代码里，三个市场的 IANA 名只许出现在 `market_time.py`（单一来源）。

    SQL 里的 `AT TIME ZONE '...'`、`AsyncIOScheduler(timezone=...)`、内联
    `ZoneInfo("America/New_York")` 都改走 `market_time.tz_name()` / `market_tz()`。
    """
    bad = []
    for path in _business_files("api"):
        rel = os.path.relpath(path, os.path.join(_REPO, "apps", "api"))
        if rel == _MARKET_TIME_MODULE:
            continue
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if _TZ_NAME_LITERAL.search(line):
                    bad.append(f"{os.path.relpath(path, _REPO)}:{i}")
    assert not bad, ("api 业务代码里还有手写的市场时区名字面量，应改走 market_time：\n"
                     + "\n".join(bad))


# ── 3 · DDL：重复定义已消除 ────────────────────────────────────────────────

def test_control_no_longer_defines_ddl():
    """`fin_param` 两列只由 `0027` 定义（api 启动时自动迁移），代码里不再抄一份。"""
    from app.services.fin import control

    assert not hasattr(control, "_DDL"), "control 里又出现了重复的 DDL 常量"
    assert not hasattr(control, "ensure_schema"), "control 里又出现了重复的 ensure_schema"


def test_paper_aux_ddl_single_source():
    """paper 的 fin_data_gap / fin_alert_log DDL 只有一份模块常量，且与 `0028` 一致。"""
    aux = os.path.join(_REPO, "apps", "paper", "app", "aux_ddl.py")
    assert os.path.exists(aux), "paper 应有一份 aux_ddl.py 作为 DDL 单一来源"
    with open(aux, encoding="utf-8") as fh:
        src = fh.read()
    for table in ("fin_data_gap", "fin_alert_log"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in src
    # 与迁移 0028 的关键结构一致（同一列集）
    with open(os.path.join(_REPO, "db", "migrations", "0028_fin_data_gap.sql"),
              encoding="utf-8") as fh:
        mig = re.sub(r"\s+", " ", fh.read())
    for frag in ("kind TEXT NOT NULL CHECK (kind IN ('no_data','no_timestamp','no_price'))",
                 "fin_alert_log_ref ON fin_alert_log (kind, ref_id)"):
        assert re.sub(r"\s+", " ", frag) in mig


# ── 4 · 真库：fin_market_rule.sessions 与常量一致（要 TEST_DATABASE_URL）───

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()


@pytest.mark.skipif(not TEST_DATABASE_URL,
                    reason="需要 TEST_DATABASE_URL 指向已跑过 0030/0034 迁移的库")
def test_db_market_rule_sessions_match_constant():
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT market, sessions FROM fin_market_rule")
            rows = {r["market"]: list(r["sessions"]) for r in cur.fetchall()}
    finally:
        conn.close()
    for m, sessions in market_sessions.MARKET_SESSIONS.items():
        assert rows.get(m) == sessions, (
            f"{m} 的 fin_market_rule.sessions 与代码常量不一致：库={rows.get(m)} 常量={sessions}"
        )
