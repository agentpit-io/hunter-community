"""启动自检：不通过就**起不来**。

「缺表的实例看着是健康的、一点就 500」比「直接起不来」难排查得多
（`apps/api/boot.sh` 里同一条理由）。这里把几件事挡在进程起来之前：

1. `PAPER_MODE` 必须是 `PAPER`；
2. **读取凭证必须非空；执行凭证按回退后的结果必须非空**（L06 + L11）：行情读取凭证
   `HUNTER_INTERNAL_KEY` 非空是硬要求；下单执行凭证 `HUNTER_EXEC_KEY` **没配时自动
   沿用读取凭证**（`config.exec_key()`，老部署平滑升级）。所以「只有读取凭证、没有执行
   凭证」**不再拒绝启动**（回退成同一把），而「读取凭证都没有」照旧拒绝 —— 那种情况
   拉不到行情、也下不了单，是「看着起来了、其实不能用」；
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
    # M7 新增（数据面）。DDL 随代码走，启动时补建 —— 见 `_ensure_aux_tables`。
    "fin_data_gap",
    "fin_alert_log",
    # L06 新增：执行允许名单（只追加）。缺它就下不了单（默认拒绝）。
    "fin_exec_allowance",
)

# 追加式表：运行期角色**只能** INSERT / SELECT，不许 UPDATE / DELETE（`09 §一`）。
APPEND_ONLY_TABLES = (
    "fin_trade",
    "fin_cash_ledger",
    "fin_snapshot",
    "fin_valuation",
    "fin_recon_log",
    "fin_param_change_log",
    "fin_data_gap",
    # L06：执行允许名单 —— 只 SELECT / INSERT（撤销 = 追加一条 revoked，不 UPDATE / DELETE）。
    "fin_exec_allowance",
)

# 状态会变的表：需要 INSERT + UPDATE，但**任何表都不给 DELETE**。
STATEFUL_TABLES = (
    "fin_order",
    "fin_position",
    "fin_project",
    # 告警投递记录：`sent_at` 要能从 NULL 改成已发（投递回执）。
    "fin_alert_log",
)

# ── 随代码走的辅助表 DDL（M7 + L06）────────────────────────────────────────
# **随代码走**：启动时补一次，让「缺表的实例看着健康、一点就 500」这件事不会发生。
# L02 起 DDL 的**单一来源**是 `app/aux_ddl.py`（原来这里与 `data_gap.py` / `recon.py`
# 各写一遍，三份文本一漂就出「自检过了、调用点报缺列」）。L06 加了执行允许名单。
from app.aux_ddl import AUX_DDL as _AUX_DDL


def _ensure_aux_tables(conn) -> None:
    """建辅助表（M7 两张 + L06 允许名单）并授权。**失败不抛** —— 后面的检查会把缺失如实报出来。"""
    try:
        with conn.cursor() as cur:
            cur.execute("SET lock_timeout = '5s'")
            cur.execute(_AUX_DDL)
        conn.commit()
    except Exception:  # noqa: BLE001 —— 含测试里的假连接对象（没有 rollback）
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass


def check(conn) -> list[str]:
    """返回问题清单（空 = 通过）。**不抛异常**，便于单测直接断言。"""
    problems: list[str] = []

    # ① 模式
    if config.paper_mode() != config.PAPER_MODE_REQUIRED:
        problems.append(
            f"PAPER_MODE 必须固定为 {config.PAPER_MODE_REQUIRED!r}，当前 "
            f"{config.paper_mode()!r}（01方案 §7.4：不接受请求参数切换）"
        )

    # ② 凭证（L11 口径）：**读取凭证必须非空**；执行凭证按**回退后的结果**必须非空。
    #    没配 HUNTER_EXEC_KEY 时 `config.exec_key()` 自动沿用读取凭证（老部署平滑升级），
    #    所以「只有读取凭证」不再拒绝启动；两条同时报只发生在「两把都没有」时。
    if not config.read_key():
        problems.append(
            f"{config.READ_KEY_ENV} 为空 —— 行情读取凭证缺失，拉行情会一律 401，拒绝启动"
        )
    if not config.exec_key():
        problems.append(
            f"{config.EXEC_KEY_ENV} 为空且无读取凭证可回退（{config.READ_KEY_ENV} 也为空）"
            " —— 下单执行凭证缺失，服务间鉴权会形同虚设，拒绝启动"
        )

    # ③ 库
    try:
        _ensure_aux_tables(conn)
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
