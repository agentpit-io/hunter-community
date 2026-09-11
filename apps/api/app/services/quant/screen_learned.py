"""扫描筛选的对照表 —— 从 AI 识别里学来的说法(全站共享)。

2026-09-11 用户要求:「只要用户用了 AI 识别,就把结果存进我们的对照表,
让本地识别越来越聪明」。

纯逻辑(怎么挖数字空位、怎么对齐多句、怎么查)全在 `screen_kw.py`,
tests/test_screen_learn.py 不连库就能测。这里只管存取。

## 为什么全站共享、又怎么防学错

「我们的对照表」—— 一个人的 AI 识别让所有人受益,这是用户要的。
代价是学错一条会被所有人反复用,所以四道保险:

  1. **本地规则永远优先**。对照表只补规则认不出的句子(screen_kw.translate),
     规则逐条测过的 130 条行为一条都不会被它改掉。
  2. **能对上才学**。多句话只有「句数 = 条件数」且每句的数字都在对应条件里,
     才逐句记;否则只记整句(screen_kw.learn_entries)。
  3. **重放前重新编译**。字段下线了、周期不支持了,编译过不去就当没命中,
     落回 AI —— AI 的新结果会覆盖掉这条旧的。
  4. **命中时明示,且能一键忘掉**。前端在折叠区**外面**常驻显示
     「这句来自之前的 AI 识别」+「这条不对,忘掉它」。忘掉是软删(disabled),
     行留着备查;下次这句话会重新交给 AI,新结果会重新启用它。

## 为什么不缓存

最初是整张表读进内存、缓存 30 秒。端到端实测踩到:「忘掉」只清得掉**处理这次请求的
那个进程**的缓存,多 worker 部署下别的进程还拿着旧记录 —— 用户刚点完「忘掉」、
一重新生成,又看到那条学错的结果,正好发生在他最想确认纠错生效的那一刻。

现在每次只查「这句话可能用到的那几个 key」(screen_kw.candidate_keys,
每句最多几十个),一条 `WHERE key = ANY(...)`。永远是最新的,表再大也不用整张读。
"""
from __future__ import annotations

import json
from loguru import logger

from app.services.database import get_conn

# 幂等 DDL · 与 db/migrations/0020_screen_learned_phrase.sql 一致。
# db/migrations 对已有部署**不生效**且不报错,真正建表的是这里(见仓内 CLAUDE.md)。
_DDL = """
CREATE TABLE IF NOT EXISTS screen_learned_phrase (
  id           BIGSERIAL PRIMARY KEY,
  key          TEXT NOT NULL UNIQUE,
  exprs        JSONB NOT NULL,
  source_text  TEXT,
  model        TEXT,
  market       TEXT,
  created_by   TEXT,
  hits         INTEGER NOT NULL DEFAULT 0,
  last_hit_at  TIMESTAMPTZ,
  disabled     BOOLEAN NOT NULL DEFAULT FALSE,
  disabled_by  TEXT,
  disabled_at  TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""
# created_by / disabled_by 故意用 TEXT 不挂外键:单用户模式下的 uid 未必在 users 表里,
# 挂了外键会让「学」这一步在那种部署上直接失败 —— 而学失败不该影响任何人用。

_ddl_applied = False

# 单条上限:一句话的 key 太长说明是整段复杂描述,记下来命中率极低,不值得占表
MAX_KEY_LEN = 400
MAX_EXPRS = 12


def _ensure_table() -> None:
    global _ddl_applied
    if _ddl_applied:
        return
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_DDL)
        conn.commit()
        _ddl_applied = True
    finally:
        conn.close()


def table_for(keys: list[str]) -> dict:
    """{key: {"id", "exprs"}},只含 keys 里、且没被忘掉的那几条。

    **出错返回空表** —— 对照表是锦上添花,库连不上时本地规则照常工作,
    不能因为它把「生成」整个打成 500。
    """
    keys = [k for k in (keys or []) if k and len(k) <= MAX_KEY_LEN]
    if not keys:
        return {}
    try:
        _ensure_table()
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, key, exprs FROM screen_learned_phrase "
                        "WHERE key = ANY(%s) AND NOT disabled", (keys,))
            tb = {}
            for i, k, ex in cur.fetchall():
                if isinstance(ex, str):
                    ex = json.loads(ex)
                tb[k] = {"id": i, "exprs": list(ex or [])}
            return tb
        finally:
            conn.close()
    except Exception as e:                                        # noqa: BLE001
        logger.warning("[screen_learned] 查对照表失败,本次只用本地规则: {}", e)
        return {}


def learn(entries: list[tuple[str, list[str]]], source_text: str, model: str | None,
          market: str | None, user_id: str | None) -> int:
    """记下来。同 key 覆盖(并重新启用 —— 被「忘掉」的句子重新问过 AI,就用新结果)。"""
    entries = [(k, ex) for k, ex in entries
               if k and ex and len(k) <= MAX_KEY_LEN and len(ex) <= MAX_EXPRS]
    if not entries:
        return 0
    _ensure_table()
    conn = get_conn()
    try:
        cur = conn.cursor()
        for k, ex in entries:
            cur.execute(
                "INSERT INTO screen_learned_phrase (key, exprs, source_text, model, market, created_by) "
                "VALUES (%s, %s::jsonb, %s, %s, %s, %s) "
                "ON CONFLICT (key) DO UPDATE SET "
                "  exprs = EXCLUDED.exprs, source_text = EXCLUDED.source_text, "
                "  model = EXCLUDED.model, market = EXCLUDED.market, "
                "  disabled = FALSE, disabled_by = NULL, disabled_at = NULL, updated_at = NOW()",
                (k, json.dumps(ex, ensure_ascii=False), (source_text or "")[:1000],
                 model, market, str(user_id) if user_id else None))
        conn.commit()
    finally:
        conn.close()
    logger.info("[screen_learned] 记下 {} 条 · 来自「{}」· {}", len(entries),
                (source_text or "")[:60], [k for k, _e in entries])
    return len(entries)


def record_hits(ids: list[int]) -> None:
    """命中计数。尽力而为,失败不影响识别。"""
    ids = [int(i) for i in ids if i]
    if not ids:
        return
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE screen_learned_phrase SET hits = hits + 1, last_hit_at = NOW() "
                        "WHERE id = ANY(%s)", (ids,))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:                                        # noqa: BLE001
        logger.warning("[screen_learned] 记命中失败(不影响识别): {}", e)


def forget(entry_id: int, user_id: str | None) -> bool:
    """忘掉一条(软删)。行留着备查:谁、什么时候忘的,原来学的是什么。"""
    _ensure_table()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE screen_learned_phrase SET disabled = TRUE, disabled_by = %s, disabled_at = NOW() "
            "WHERE id = %s AND NOT disabled",
            (str(user_id) if user_id else None, int(entry_id)))
        n = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    if n:
        logger.info("[screen_learned] 忘掉 #{} · by {}", entry_id, user_id)
    return n > 0
