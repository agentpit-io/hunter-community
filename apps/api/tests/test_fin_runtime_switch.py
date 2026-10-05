# -*- coding: utf-8 -*-
"""R20 · 经验库「按项目开关」（`app/services/fin/switches.py` 的覆盖层 + `0046` 迁移）。

方案依据 `plan/00记忆新方案/10…`（工作开关搬上界面）· 任务书 `R20.md` §3.2 的十一条。

分两类：

1. **不连库**（任何环境都跑）：天花板闸门、模式超上限、未知 key、reason 必填、
   库不通回落 env、展示表（`meta`）的 `allowed` 随天花板变、迁移文件本身的性质。
2. **真库**（`TEST_DATABASE_URL` 未设时整体 skip）：真落库（跨连接读回）、
   缓存写完立刻失效、硬开关零写入、没变不写流水、每次改动恰好多一行、迁移连跑两遍幂等。

跑法（本机 · 测试库 m1-fin-pg）::

    cd apps/api && TEST_DATABASE_URL=postgresql://hunter:hunter@127.0.0.1:5599/r20_test \
        PYTHONPATH=. python -m pytest tests/test_fin_runtime_switch.py -q
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import psycopg2
import psycopg2.extras
import pytest

_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from app.services.fin import switches  # noqa: E402

_URL = os.getenv("TEST_DATABASE_URL", "").strip()
_needs_db = pytest.mark.skipif(
    not _URL, reason="需要 TEST_DATABASE_URL 指向一个跑过 0046 迁移的 postgres")

# 覆盖层自己开连接时读的是模块级 `switches.DATABASE_URL`（import 期取的 env）——
# 不指过去的话，`_read_override` 会连到默认库上（读不到行 → 静默回落天花板，测不出东西）。
# 与 `test_fin_project_router.py` 覆盖 `store.DATABASE_URL` 同一个做法。
if _URL:
    switches.DATABASE_URL = _URL

_MIGRATION_0046 = (Path(__file__).resolve().parents[3]
                   / "db" / "migrations" / "0046_memory_switch.sql")


# ════════════════════════════════════════════════════════════════════════
# 夹具
# ════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _clear_cache():
    """每条用例前后都清掉覆盖层缓存 —— 用例之间不许互相串。"""
    switches.invalidate_switch_cache()
    yield
    switches.invalidate_switch_cache()


@pytest.fixture()
def db():
    """一个真项目（`fin_memory_switch.project_id` 有外键到 `fin_project`），用完删干净。"""
    if not _URL:
        pytest.skip("需要 TEST_DATABASE_URL")
    conn = psycopg2.connect(_URL)
    pid = "prj_r20_" + uuid.uuid4().hex[:16]
    uid = "u_r20_" + uuid.uuid4().hex[:16]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO fin_project (project_id, user_id, tier, status, initial_capital) "
            "VALUES (%s, %s, 'play', 'active', 100000)", (pid, uid))
    conn.commit()
    try:
        yield {"conn": conn, "pid": pid, "uid": uid}
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM fin_memory_switch_log WHERE project_id = %s", (pid,))
            cur.execute("DELETE FROM fin_memory_switch WHERE project_id = %s", (pid,))
            cur.execute("DELETE FROM fin_project WHERE project_id = %s", (pid,))
        conn.commit()
        conn.close()


def _count(table: str, pid: str) -> int:
    """开一条新连接数行 —— 跨连接，证明真落了盘。"""
    conn = psycopg2.connect(_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {table} WHERE project_id = %s", (pid,))
            return int(cur.fetchone()[0])
    finally:
        conn.close()


def _row(pid: str):
    conn = psycopg2.connect(_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT memory_enabled, evolution_mode, updated_by "
                        "FROM fin_memory_switch WHERE project_id = %s", (pid,))
            return cur.fetchone()
    finally:
        conn.close()


def _logs(pid: str):
    conn = psycopg2.connect(_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT switch_key, old_value, new_value, actor, reason, created_at "
                        "FROM fin_memory_switch_log WHERE project_id = %s "
                        "ORDER BY created_at, log_id", (pid,))
            return cur.fetchall()
    finally:
        conn.close()


def _to_regclass(name: str):
    conn = psycopg2.connect(_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (name,))
            return cur.fetchone()[0]
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════════
# 一 · 天花板闸门与参数校验（不连库 —— 这些在碰 conn 之前就抛）
# ════════════════════════════════════════════════════════════════════════

def test_ceiling_zero_blocks_read_and_write(monkeypatch):
    """① 天花板 0 → 读为 False；写 True 抛 ValueError（报错写明是部署侧锁死）。"""
    monkeypatch.setenv(switches.MEMORY_ENABLED_ENV, "0")
    switches.invalidate_switch_cache()
    assert switches.memory_enabled("prj-anyone") is False
    with pytest.raises(ValueError) as ei:
        switches.set_project_switch(
            None, "prj-anyone", switch_key="memory_enabled", value=True,
            actor="u", reason="想开")
    assert "部署侧已锁死" in str(ei.value)
    assert switches.MEMORY_ENABLED_ENV in str(ei.value)


def test_ceiling_zero_still_allows_writing_false(monkeypatch, db):
    """天花板 0 时写 `false` 不越界 —— 只能往下、不能往上。"""
    monkeypatch.setenv(switches.MEMORY_ENABLED_ENV, "0")
    switches.invalidate_switch_cache()
    out = switches.set_project_switch(
        db["conn"], db["pid"], switch_key="memory_enabled", value=False,
        actor=db["uid"], reason="关掉（天花板本来就是 0）")
    assert out["new_value"] is False
    assert switches.memory_enabled(db["pid"]) is False


def test_mode_over_ceiling_rejected_names_ceiling(monkeypatch):
    """⑥ 天花板 observe 硬塞 paper → ValueError，报错里写明天花板是 observe。"""
    monkeypatch.setenv(switches.EVOLUTION_MODE_ENV, "observe")
    switches.invalidate_switch_cache()
    with pytest.raises(ValueError) as ei:
        switches.set_project_switch(
            None, "prj-anyone", switch_key="evolution_mode", value="paper",
            actor="u", reason="想上模拟盘")
    assert "observe" in str(ei.value)
    assert switches.EVOLUTION_MODE_ENV in str(ei.value)


def test_unknown_key_rejected():
    """⑦ 未知 key → ValueError。"""
    with pytest.raises(ValueError) as ei:
        switches.set_project_switch(
            None, "prj-anyone", switch_key="nope", value=True, actor="u", reason="r")
    assert "未知开关" in str(ei.value)


def test_reason_required(monkeypatch):
    """⑦ reason 为空 / 只有空白 → ValueError（每次改动都要写清为什么）。"""
    monkeypatch.setenv(switches.MEMORY_ENABLED_ENV, "1")
    for bad in ("", "   ", None):
        with pytest.raises(ValueError) as ei:
            switches.set_project_switch(
                None, "prj-anyone", switch_key="memory_enabled", value=False,
                actor="u", reason=bad)
        assert "reason" in str(ei.value)


def test_empty_project_id_rejected():
    with pytest.raises(ValueError):
        switches.set_project_switch(
            None, "  ", switch_key="memory_enabled", value=False, actor="u", reason="r")


def test_bad_value_type_rejected(monkeypatch):
    """`memory_enabled` 非布尔值 → ValueError（不许把字符串当真值写进去）。"""
    monkeypatch.setenv(switches.MEMORY_ENABLED_ENV, "1")
    with pytest.raises(ValueError):
        switches.set_project_switch(
            None, "prj-anyone", switch_key="memory_enabled", value="yes",
            actor="u", reason="r")
    with pytest.raises(ValueError):
        switches.set_project_switch(
            None, "prj-anyone", switch_key="evolution_mode", value="sometimes",
            actor="u", reason="r")


# ════════════════════════════════════════════════════════════════════════
# 二 · 库不通 → 回落 env（不抛、不 500）
# ════════════════════════════════════════════════════════════════════════

def test_db_down_falls_back_to_env(monkeypatch):
    """⑩ 库不通 → 覆盖层读不到 → 回落天花板，**不抛异常**。"""
    monkeypatch.setenv(switches.MEMORY_ENABLED_ENV, "1")
    monkeypatch.setenv(switches.EVOLUTION_MODE_ENV, "observe")
    monkeypatch.setattr(switches, "DATABASE_URL",
                        "postgresql://hunter:hunter@127.0.0.1:1/hunter_nope")
    switches.invalidate_switch_cache()
    assert switches.memory_enabled("prj-anyone") is True          # 回落天花板（开）
    assert switches.evolution_mode_requested("prj-anyone") == "observe"
    assert switches.selected_switch("prj-anyone") == {
        switches.SWITCH_MEMORY: None, switches.SWITCH_MODE: None,
        switches.SWITCH_AUTO_APPLY: None}
    # 连不上库时「能不能改」仍由天花板回答（不崩）
    assert switches.can_change()["memory_enabled"] is True


# ════════════════════════════════════════════════════════════════════════
# 三 · 展示表（前端不写死枚举，全部来自后端 `meta`）
# ════════════════════════════════════════════════════════════════════════

def test_meta_options_allowed_follow_ceiling(monkeypatch):
    """`meta.evolution_mode.options[].allowed` 随天花板变（界面据此画灰）。"""
    monkeypatch.setenv(switches.EVOLUTION_MODE_ENV, "observe")
    meta = switches.switch_meta()
    allowed = {o["value"]: o["allowed"] for o in meta["evolution_mode"]["options"]}
    assert allowed == {"off": True, "observe": True, "paper": False}
    assert meta["evolution_mode"]["ceiling_note"]
    assert "只观察" in meta["evolution_mode"]["ceiling_note"]


def test_meta_paper_allows_everything(monkeypatch):
    monkeypatch.setenv(switches.EVOLUTION_MODE_ENV, "paper")
    meta = switches.switch_meta()
    assert all(o["allowed"] for o in meta["evolution_mode"]["options"])
    assert meta["evolution_mode"]["ceiling_note"] is None


def test_meta_off_locks_mode(monkeypatch):
    monkeypatch.setenv(switches.EVOLUTION_MODE_ENV, "off")
    meta = switches.switch_meta()
    assert [o["value"] for o in meta["evolution_mode"]["options"]] == ["off", "observe", "paper"]
    assert [o["allowed"] for o in meta["evolution_mode"]["options"]] == [True, False, False]


def test_meta_has_all_display_names():
    """展示表里有枚举取值、短标签、长说明、硬开关文案与「为什么灰」的说明。"""
    meta = switches.switch_meta()
    for key in (switches.SWITCH_MEMORY, switches.SWITCH_MODE,
                "auto_apply", "live_order_enabled", "hard_note"):
        assert key in meta, key
    for o in meta["evolution_mode"]["options"]:
        assert o["label"] and o["full"] and o["value"] in switches.EVOLUTION_MODES
    assert meta[switches.SWITCH_MEMORY]["on_text"] == "已启用"
    assert meta[switches.SWITCH_MEMORY]["off_text"] == "未启用"
    # L12：auto_apply 从只读文字变成可改的真条目（带开/关标签与说明）。
    # 旧断言 `off_text == "关（本方案恒为关闭）"` 与新口径不符，故改。
    assert meta["auto_apply"]["on_label"] == "开"
    assert meta["auto_apply"]["off_label"] == "关"
    assert meta["auto_apply"]["off_text"] == "未启用"
    assert "收紧" in meta["auto_apply"]["desc"], "文案要说清「只对收紧方向放行」"


def test_runtime_state_carries_meta_and_ceiling(monkeypatch):
    monkeypatch.setenv(switches.EVOLUTION_MODE_ENV, "observe")
    monkeypatch.setenv(switches.MEMORY_ENABLED_ENV, "1")
    state = switches.runtime_state()
    assert state["ceiling"] == {"memory_enabled": True, "evolution_mode": "observe",
                                "auto_apply": True}
    assert state["can_change"] == {"memory_enabled": True, "evolution_mode": True,
                                   "auto_apply": True}
    assert state["meta"]["evolution_mode"]["options"][2]["allowed"] is False


# ════════════════════════════════════════════════════════════════════════
# 四 · 真库：落库 / 缓存 / 流水
# ════════════════════════════════════════════════════════════════════════

@_needs_db
def test_ceiling_one_no_row_is_true(monkeypatch, db):
    """② 天花板 1 + 表里没行 → `memory_enabled(pid)` 为 True（跟随天花板）。"""
    monkeypatch.setenv(switches.MEMORY_ENABLED_ENV, "1")
    switches.invalidate_switch_cache()
    assert switches.memory_enabled(db["pid"]) is True
    assert switches.selected_switch(db["pid"]) == {
        switches.SWITCH_MEMORY: None, switches.SWITCH_MODE: None,
        switches.SWITCH_AUTO_APPLY: None}


@_needs_db
def test_write_false_persists_across_connection(monkeypatch, db):
    """③ 写 false → 跨连接读回 False（真落库，不是内存里改了个数）。"""
    monkeypatch.setenv(switches.MEMORY_ENABLED_ENV, "1")
    switches.invalidate_switch_cache()
    out = switches.set_project_switch(
        db["conn"], db["pid"], switch_key="memory_enabled", value=False,
        actor=db["uid"], reason="测试关闭")
    assert out["changed"] is True
    assert out["new_value"] is False
    assert switches.memory_enabled(db["pid"]) is False
    row = _row(db["pid"])
    assert row is not None and row["memory_enabled"] is False
    assert row["updated_by"] == db["uid"]


@_needs_db
def test_cache_invalidated_immediately_no_sleep(monkeypatch, db):
    """④ 缓存写完立刻失效 —— 测试里**没有 sleep**，读完立刻就是新值。"""
    monkeypatch.setenv(switches.MEMORY_ENABLED_ENV, "1")
    switches.invalidate_switch_cache()
    assert switches.memory_enabled(db["pid"]) is True      # 先读一次，把 5 秒缓存填上
    switches.set_project_switch(
        db["conn"], db["pid"], switch_key="memory_enabled", value=False,
        actor=db["uid"], reason="立刻生效")
    assert switches.memory_enabled(db["pid"]) is False     # 不等 5 秒


@_needs_db
def test_hard_switch_key_rejected_and_no_rows(monkeypatch, db):
    """⑤ 硬开关 key → ValueError，且库里一行都没写（现值表与流水表都是 0）。

    L12：硬开关**只剩实盘那一条**（`auto_apply` 已移出名单，成为可改的真开关）。
    旧用例的 `for key in ("auto_apply", "live_order_enabled")` 与新口径不符，故只留实盘。
    """
    monkeypatch.setenv(switches.MEMORY_ENABLED_ENV, "1")
    switches.invalidate_switch_cache()
    with pytest.raises(ValueError) as ei:
        switches.set_project_switch(
            db["conn"], db["pid"], switch_key="live_order_enabled", value=True,
            actor=db["uid"], reason="r")
    assert "硬开关" in str(ei.value)
    assert _count("fin_memory_switch", db["pid"]) == 0
    assert _count("fin_memory_switch_log", db["pid"]) == 0


@_needs_db
def test_auto_apply_override_written_and_read_back(monkeypatch, db):
    """L12：`auto_apply` 现在是可改的真开关 —— 写 false 落库、跨连接读回、流水如实追加。

    同时验「按项目关掉 → 该项目生效值为假、**全局（不带 project_id）仍为真**」。
    """
    monkeypatch.setenv(switches.MEMORY_ENABLED_ENV, "1")
    monkeypatch.setenv(switches.AUTO_APPLY_ENV, "1")          # 天花板开
    switches.invalidate_switch_cache()
    assert switches.auto_apply_effective(db["pid"]) is True   # 未设置 → 跟随天花板
    out = switches.set_project_switch(
        db["conn"], db["pid"], switch_key="auto_apply", value=False,
        actor=db["uid"], reason="这个项目先别自动生效")
    assert out["changed"] is True and out["new_value"] is False
    assert out["auto_apply"] is False
    # 跨连接读回（真落库，不是内存里改了个数）
    assert switches.auto_apply_effective(db["pid"]) is False
    assert switches.selected_switch(db["pid"])[switches.SWITCH_AUTO_APPLY] is False
    # 流水恰好多一行，key 是 auto_apply
    rows = _logs(db["pid"])
    assert [r["switch_key"] for r in rows] == ["auto_apply"]
    assert rows[0]["old_value"] is None and rows[0]["new_value"] is False
    assert rows[0]["actor"] == db["uid"] and rows[0]["reason"] == "这个项目先别自动生效"
    # 全局（不带 project_id）不受影响，仍是天花板 = 开
    assert switches.auto_apply_effective() is True


@_needs_db
def test_auto_apply_over_ceiling_rejected(monkeypatch, db):
    """天花板 `FIN_AUTO_APPLY=0` → 界面写 true 被 400（`ValueError`），且**零写入**。"""
    monkeypatch.setenv(switches.MEMORY_ENABLED_ENV, "1")
    monkeypatch.setenv(switches.AUTO_APPLY_ENV, "0")
    switches.invalidate_switch_cache()
    with pytest.raises(ValueError) as ei:
        switches.set_project_switch(
            db["conn"], db["pid"], switch_key="auto_apply", value=True,
            actor=db["uid"], reason="想开")
    assert "部署侧已锁死" in str(ei.value) and switches.AUTO_APPLY_ENV in str(ei.value)
    assert _count("fin_memory_switch", db["pid"]) == 0
    assert _count("fin_memory_switch_log", db["pid"]) == 0


@_needs_db
def test_unchanged_adds_no_log_row(monkeypatch, db):
    """⑧ 值没变 → changed=false，且**不追加**流水行。"""
    monkeypatch.setenv(switches.MEMORY_ENABLED_ENV, "1")
    switches.invalidate_switch_cache()
    switches.set_project_switch(
        db["conn"], db["pid"], switch_key="memory_enabled", value=False,
        actor=db["uid"], reason="第一次")
    n1 = _count("fin_memory_switch_log", db["pid"])
    out = switches.set_project_switch(
        db["conn"], db["pid"], switch_key="memory_enabled", value=False,
        actor=db["uid"], reason="再说一次（没变）")
    assert out["changed"] is False
    assert _count("fin_memory_switch_log", db["pid"]) == n1 == 1


@_needs_db
def test_each_change_appends_exactly_one_log(monkeypatch, db):
    """⑨ 每次真改动 → 流水账**恰好多一行**（key / old / new / actor / reason / 时刻）。"""
    monkeypatch.setenv(switches.MEMORY_ENABLED_ENV, "1")
    monkeypatch.setenv(switches.EVOLUTION_MODE_ENV, "paper")
    switches.invalidate_switch_cache()
    switches.set_project_switch(
        db["conn"], db["pid"], switch_key="memory_enabled", value=False,
        actor=db["uid"], reason="先关掉")
    switches.set_project_switch(
        db["conn"], db["pid"], switch_key="evolution_mode", value="observe",
        actor=db["uid"], reason="降一档")
    rows = _logs(db["pid"])
    assert [r["switch_key"] for r in rows] == ["memory_enabled", "evolution_mode"]
    assert rows[0]["old_value"] is None and rows[0]["new_value"] is False
    assert rows[0]["actor"] == db["uid"] and rows[0]["reason"] == "先关掉"
    assert rows[0]["created_at"] is not None
    assert rows[1]["new_value"] == "observe" and rows[1]["reason"] == "降一档"


@_needs_db
def test_mode_written_and_capped(monkeypatch, db):
    """模式：天花板 paper 时写入 observe 生效；写入后被 `selected` 如实记下。"""
    monkeypatch.setenv(switches.EVOLUTION_MODE_ENV, "paper")
    monkeypatch.setenv(switches.MEMORY_ENABLED_ENV, "1")
    switches.invalidate_switch_cache()
    switches.set_project_switch(
        db["conn"], db["pid"], switch_key="evolution_mode", value="observe",
        actor=db["uid"], reason="先观察")
    assert switches.evolution_mode_requested(db["pid"]) == "observe"
    assert switches.selected_switch(db["pid"])[switches.SWITCH_MODE] == "observe"


@_needs_db
def test_evolution_gate_per_project_but_ceiling_for_apply(monkeypatch, db):
    """§1.3 的不对称：提提案按项目判；生效 / 回滚只看天花板（无 project_id）。"""
    monkeypatch.setenv(switches.EVOLUTION_MODE_ENV, "paper")
    monkeypatch.setenv(switches.MEMORY_ENABLED_ENV, "1")
    switches.invalidate_switch_cache()
    switches.set_project_switch(
        db["conn"], db["pid"], switch_key="evolution_mode", value="off",
        actor=db["uid"], reason="这个项目先别学")
    assert switches.evolution_enabled(db["pid"]) is False     # 本项目：不再提案
    assert switches.evolution_enabled() is True               # 天花板：生效 / 回滚照跑


# ════════════════════════════════════════════════════════════════════════
# 五 · 迁移 0046 本身（幂等 / 无事务边界 / 无 DELETE）
# ════════════════════════════════════════════════════════════════════════

def test_migration_0046_source_properties():
    sql = _MIGRATION_0046.read_text(encoding="utf-8")
    # 只看**代码**：注释里会提到 "不带 BEGIN;/COMMIT;" 这条纪律，不能当违例。
    code = "\n".join(ln.split("--", 1)[0] for ln in sql.splitlines())
    low = code.lower()
    assert "begin;" not in low and "commit;" not in low, "迁移自带事务边界会破坏 migrate.apply_one"
    assert "delete from" not in low and "drop table" not in low, "只做加法，不许删"
    assert low.count("create table if not exists") == 2
    assert "fin_project(project_id)" in sql.replace(" ", "") or \
        "fin_project (project_id)" in sql, "现值表要对项目建外键"
    for col in ("memory_enabled", "evolution_mode", "updated_by", "updated_at"):
        assert col in sql, col
    # 模式有 CHECK 约束（只许 off / observe / paper）
    assert "('off','observe','paper')" in sql.replace(" ", "")


@_needs_db
def test_migration_0046_runs_twice_without_error():
    """⑪ 迁移 `0046` 连跑两遍不报错（幂等）。"""
    sql = _MIGRATION_0046.read_text(encoding="utf-8")
    conn = psycopg2.connect(_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)      # 第一遍
            cur.execute(sql)      # 第二遍（必须同样成功）
        conn.commit()
    finally:
        conn.close()
    assert _to_regclass("fin_memory_switch") == "fin_memory_switch"
    assert _to_regclass("fin_memory_switch_log") == "fin_memory_switch_log"


# ════════════════════════════════════════════════════════════════════════
# 六 · 迁移 0055（`auto_apply` 列 + 流水 CHECK）本身
# ════════════════════════════════════════════════════════════════════════

_MIGRATION_0055 = (Path(__file__).resolve().parents[3]
                   / "db" / "migrations" / "0055_auto_apply_switch.sql")


def test_migration_0055_source_properties():
    sql = _MIGRATION_0055.read_text(encoding="utf-8")
    code = "\n".join(ln.split("--", 1)[0] for ln in sql.splitlines())
    low = code.lower()
    assert "begin;" not in low and "commit;" not in low, "迁移自带事务边界会破坏 migrate.apply_one"
    assert "drop table" not in low, "只做加法，不许删表"
    assert "add column if not exists auto_apply" in low, "加列要幂等（IF NOT EXISTS）"
    assert "'auto_apply'" in code, "流水账的 CHECK 要放行 auto_apply"


@_needs_db
def test_migration_0055_runs_twice_without_error():
    """L12：迁移 `0055` 连跑两遍不报错（幂等）—— 列存在、CHECK 已放宽。"""
    sql = _MIGRATION_0055.read_text(encoding="utf-8")
    conn = psycopg2.connect(_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)      # 第一遍
            cur.execute(sql)      # 第二遍（必须同样成功）
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'fin_memory_switch' AND column_name = 'auto_apply'")
            assert cur.fetchone(), "fin_memory_switch.auto_apply 列不存在"
            # CHECK 已放宽：插一行 switch_key='auto_apply' 的流水不该被拒
            cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conname = 'fin_memory_switch_log_key_chk'")
            row = cur.fetchone()
            assert row and "auto_apply" in row[0], "流水账 CHECK 没放行 auto_apply"
        conn.rollback()
    finally:
        conn.close()
