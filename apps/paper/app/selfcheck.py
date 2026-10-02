"""启动自检：不通过就**起不来**。

「缺表的实例看着是健康的、一点就 500」比「直接起不来」难排查得多
（`apps/api/boot.sh` 里同一条理由）。这里把三件事挡在进程起来之前：

1. `PAPER_MODE` 必须是 `PAPER`；
2. `HUNTER_INTERNAL_KEY` 必须非空（否则服务间鉴权形同虚设）；
3. 账本库必须连得上、**必需的表都在**、而且连的角色**恰好只有该有的权限**——
   追加表上不能有 `UPDATE`/`DELETE`。这一条是「唯一账本」在技术上的兑现：
   如果谁把 DSN 指到了管理员连接，自检直接拒绝启动，而不是「先跑起来再说」。

第 3 条的判据用 `has_table_privilege`（跟着实际 GRANT 走，不看角色名），
所以它同时挡住两件事：权限没授到位，以及权限给多了。

命令行用法：`python -m app.selfcheck`，有问题打印后 `exit 1`。
"""

from __future__ import annotations

import sys
from typing import Iterable

import psycopg2

from app import config

# 必需的表（缺一张就起不来）。改名 / 加表时同步这里。
REQUIRED_TABLES = (
    "fin_project",
    "fin_account",
    "fin_param",
    "fin_order",
    "fin_trade",
    "fin_cash_ledger",
    "fin_position",
    "fin_valuation",
    "fin_recon_log",
    "fin_snapshot",
    "fin_instrument",
    "fin_market_calendar",
    "fin_fee_model",
    "fin_execution_model",
)

# 追加式表：运行期角色**只能** INSERT / SELECT，不许 UPDATE / DELETE（`09 §一`）。
APPEND_ONLY_TABLES = (
    "fin_trade",
    "fin_cash_ledger",
    "fin_snapshot",
    "fin_valuation",
    "fin_recon_log",
    "fin_param_change_log",
)

# 状态会变的表：需要 INSERT + UPDATE，但**任何表都不给 DELETE**。
STATEFUL_TABLES = (
    "fin_order",
    "fin_position",
    "fin_project",
)


def check(conn) -> list[str]:
    """返回问题清单（空 = 通过）。**不抛异常**，便于单测直接断言。"""
    problems: list[str] = []

    # ① 模式
    if config.paper_mode() != config.PAPER_MODE_REQUIRED:
        problems.append(
            f"PAPER_MODE 必须固定为 {config.PAPER_MODE_REQUIRED!r}，当前 "
            f"{config.paper_mode()!r}（01方案 §7.4：不接受请求参数切换）"
        )

    # ② 内部口令
    if not config.internal_key():
        problems.append("HUNTER_INTERNAL_KEY 为空 —— 服务间鉴权会形同虚设，拒绝启动")

    # ③ 库
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user, current_database()")
            role, dbname = cur.fetchone()
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = ANY(%s)",
                (list(REQUIRED_TABLES),),
            )
            present = {r[0] for r in cur.fetchall()}
            missing = [t for t in REQUIRED_TABLES if t not in present]
            if missing:
                problems.append(f"账本库 {dbname} 缺少必需的表：{', '.join(missing)}")

            if not missing:
                problems.extend(_privilege_problems(cur, role))
    except psycopg2.Error as exc:  # 连不上 / 查询失败
        problems.append(f"连接账本库失败：{exc}")

    return problems


def _privilege_problems(cur, role: str) -> list[str]:
    problems: list[str] = []

    def has(table: str, priv: str) -> bool:
        cur.execute("SELECT has_table_privilege(%s, %s, %s)", (role, table, priv))
        return bool(cur.fetchone()[0])

    for table in APPEND_ONLY_TABLES:
        if not has(table, "INSERT"):
            problems.append(f"角色 {role} 在追加表 {table} 上没有 INSERT 权限")
        if not has(table, "SELECT"):
            problems.append(f"角色 {role} 在追加表 {table} 上没有 SELECT 权限")
        for priv in ("UPDATE", "DELETE"):
            if has(table, priv):
                problems.append(
                    f"角色 {role} 在追加表 {table} 上有 {priv} 权限 —— "
                    "账本必须只追加不回改，拒绝启动（09 §一：GRANT 就是边界）"
                )

    for table in STATEFUL_TABLES:
        if not has(table, "INSERT") or not has(table, "UPDATE"):
            problems.append(f"角色 {role} 在 {table} 上缺少 INSERT/UPDATE 权限（状态列要能改）")
        if has(table, "DELETE"):
            problems.append(f"角色 {role} 在 {table} 上有 DELETE 权限 —— 任何角色都不给 DELETE")

    # BIGSERIAL 主键背后的序列：表权限给了、序列没给的话，第一次 INSERT 才报错
    # （M2 实测：`permission denied for sequence fin_cash_ledger_entry_id_seq`）。
    cur.execute(
        """
        SELECT c.relname, t.relname
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_depend d ON d.objid = c.oid AND d.deptype IN ('a', 'i')
          JOIN pg_class t ON t.oid = d.refobjid
         WHERE c.relkind = 'S' AND n.nspname = 'public' AND t.relname LIKE 'fin\\_%'
        """
    )
    for seq, table in cur.fetchall():
        cur.execute("SELECT has_sequence_privilege(%s, %s, 'USAGE')", (role, seq))
        if not cur.fetchone()[0]:
            problems.append(
                f"角色 {role} 在序列 {seq}（{table} 的 BIGSERIAL）上没有 USAGE 权限 —— "
                "需 GRANT USAGE ON SEQUENCE（见 0024_fin_paper_sequence.sql）"
            )

    return problems


def main() -> int:
    try:
        conn = psycopg2.connect(config.database_url())
    except psycopg2.Error as exc:
        print(f"[paper.selfcheck] 连接账本库失败：{exc}", file=sys.stderr)
        return 1

    try:
        problems = check(conn)
    finally:
        conn.close()

    if problems:
        print("[paper.selfcheck] 启动自检未通过，拒绝启动：", file=sys.stderr)
        for item in problems:
            print(f"  · {item}", file=sys.stderr)
        return 1

    print("[paper.selfcheck] OK · PAPER_MODE=PAPER · 账本库已连接且权限边界正确")
    return 0


if __name__ == "__main__":
    sys.exit(main())
