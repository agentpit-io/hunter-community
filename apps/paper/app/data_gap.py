"""数据缺口（`08 §七`「缺口进 `fin_data_gap`」· `01方案 §11.2`「数据源中断 → 标记质量与缺口」）。

**为什么要有这张表。** 行情断流时 `snapshot.capture` 返回 `None` 且**不写 `fin_snapshot`**
（`snapshot_time` 是 NOT NULL 且只能来自数据源）。于是「今天 10:00 起沪深行情断了 40 分钟」
这件事在账本里**一个字都留不下来** —— 只有日志，日志会滚掉。估值、对账、报告都要能回答
「这个数是不是在缺口里算出来的」，所以缺口必须落库。

三种缺口，各记各的（`kind`）：

| kind | 含义 | 触发点 |
|---|---|---|
| `no_data` | 数据源没给这张票任何报价（断流 / 该票停牌 / 池里没有） | `capture` 拿不到 `Quote` |
| `no_timestamp` | 有报价，但**没有数据源时刻** → 不落快照 | `quote.quote_time is None` |
| `no_price` | 有报价，但没有最新价 → 不落快照 | `quote.last_price is None` |

`stale`（有价但旧）**不进这张表** —— 那种快照落在 `fin_snapshot` 里并带
`quality='stale' / missing_flag=true`，是「有信息的一行」，不该再记一条缺口。

DDL 随代码走（`CLAUDE.md` 铁律：`db/migrations/*.sql` 对已有部署不生效），
`db/migrations/0028_fin_data_gap.sql` 是同内容的留档。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import psycopg2.extras
from loguru import logger

_DDL = """
CREATE TABLE IF NOT EXISTS fin_data_gap (
  id          BIGSERIAL PRIMARY KEY,
  code        TEXT NOT NULL,
  market      TEXT,
  source      TEXT,
  kind        TEXT NOT NULL CHECK (kind IN ('no_data','no_timestamp','no_price')),
  detail      TEXT,
  event_time  TIMESTAMPTZ,
  observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fin_data_gap_code_time ON fin_data_gap (code, observed_at DESC);
"""

# GRANT 单独一条：新表必须显式授权，否则角色读不到（`09 §七` 权限一行）。
_GRANT = "GRANT SELECT, INSERT ON fin_data_gap TO fin_paper_rw; GRANT USAGE ON SEQUENCE fin_data_gap_id_seq TO fin_paper_rw;"

_ddl_done = False


def ensure_table(cur) -> None:
    """每进程只查一次表在不在；不在才补 DDL（同 `vcp.load_stats` 的 `_ddl_checked` 思路）。

    **先查存在性再决定要不要 DDL**：`fin_paper_rw` 是受限的运行期角色，跑
    `CREATE TABLE` / `GRANT` 会**把当前事务打成 aborted** —— 后面所有语句全废，
    而错误会在很远的调用点冒出来（M7 实测：一个测试的 INSERT 报「current
    transaction is aborted」，根因是几百毫秒前的 GRANT 没权限）。
    表由 api 的 `app.migrate`（管理员连接）建好，这里只兜底。
    """
    global _ddl_done
    if _ddl_done:
        return
    if _table_exists(cur, "fin_data_gap"):
        _ddl_done = True
        return
    try:
        cur.execute("SET lock_timeout = '5s'")
        cur.execute(_DDL)
        try:
            cur.execute(_GRANT)
        except Exception as exc:  # noqa: BLE001 —— 授权失败（角色不存在 / 无权限）不该挡住记账
            logger.warning("[paper.gap] 授权 fin_data_gap 失败（继续）：{}", exc)
        _ddl_done = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[paper.gap] 建 fin_data_gap 失败（本轮不记缺口）：{}", exc)


def _table_exists(cur, name: str) -> bool:
    """表在不在。**只读、不抛** —— 查不出来当"在"，避免误触发无权限的 DDL。"""
    try:
        cur.execute("SELECT to_regclass(%s) AS t", (f"public.{name}",))
        row = cur.fetchone()
    except Exception:  # noqa: BLE001
        return True
    if row is None:
        return False
    value = row.get("t") if isinstance(row, dict) else row[0]
    return value is not None


def record(cur, code: str, kind: str, *, market: Optional[str] = None,
           source: Optional[str] = None, detail: Optional[str] = None,
           event_time: Optional[datetime] = None) -> None:
    """记一条缺口。**失败只记日志**，绝不让一次缺口记录打断取数/撮合事务。"""
    ensure_table(cur)
    try:
        cur.execute(
            """
            INSERT INTO fin_data_gap (code, market, source, kind, detail, event_time)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (code, market, source, kind, detail, event_time),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[paper.gap] 记缺口失败 {} {} · {}", code, kind, exc)


def recent(cur, limit: int = 50) -> list[dict]:
    ensure_table(cur)
    cur.execute(
        "SELECT id, code, market, source, kind, detail, event_time, observed_at "
        "FROM fin_data_gap ORDER BY id DESC LIMIT %s",
        (limit,),
    )
    return cur.fetchall()


def summary_since(cur, since: datetime) -> dict:
    """**新鲜度概览**：某时刻以来每种缺口各多少条、涉及多少只票。

    给「数据面健康」看 —— 不猜「断没断」，直接数。
    """
    ensure_table(cur)
    cur.execute(
        """
        SELECT kind, COUNT(*) AS n, COUNT(DISTINCT code) AS codes,
               MAX(observed_at) AS last_at
          FROM fin_data_gap WHERE observed_at >= %s GROUP BY kind ORDER BY kind
        """,
        (since,),
    )
    rows = cur.fetchall()
    return {
        "since": since,
        "kinds": [{"kind": r["kind"], "count": int(r["n"]), "codes": int(r["codes"]),
                   "last_at": r["last_at"]} for r in rows],
        "total": sum(int(r["n"]) for r in rows),
    }


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def to_json(row: dict) -> dict:
    return {k: _jsonable(v) for k, v in row.items()}


__all__ = ["ensure_table", "record", "recent", "summary_since", "to_json",
           "psycopg2"]
