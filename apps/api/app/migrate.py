"""数据库迁移执行器 · api 启动时自动跑（见 docs/setup-wizard/design.md 3.4）

为什么要有这个模块
------------------
改造之前，迁移靠 postgres 的 ``docker-entrypoint-initdb.d`` 挂载执行，而那个目录
**只在数据卷第一次创建时执行一次**。结果是：老用户 ``git pull`` 升级后，数据卷还是
旧的，新增的迁移一个都不会跑 —— 表和列缺着，实例「看着是健康的、一点就 500」。
本机实测踩过一次：8 月建的数据卷，升级后 ``/api/compliance/status`` 报 500，原因是
``users`` 表缺 ``compliance_ack_at`` 列（0006 迁移没跑过），手工补跑 21 个迁移才恢复。

改成由 api 启动时执行（``boot.sh`` → ``python -m app.migrate`` → uvicorn），
迁移就跟着**镜像版本**走而不是跟着数据卷走：拉新镜像 = 补齐迁移。

两个阶段
--------
① **基础表 DDL** —— 调 ``app.services.database.init_db()``。
② **增量迁移** —— 按文件名顺序跑 ``db/migrations/*.sql`` 里没跑过的。

顺序不能反：有 7 个迁移文件 ALTER/建视图的目标表是 ``init_db()`` 建的，不是迁移文件
建的（见下面 ``BASELINE_REQUIRED_TABLES`` 处的注释与实测报错）。

直接运行::

    python -m app.migrate              # 正常执行（容器里工作目录是 /app，即 apps/api/）
    python -m app.migrate --dry-run    # 只打印待执行清单，不动数据库

退出码：全部成功 0；任何一步失败非 0。``boot.sh`` 里 ``set -e``，非 0 就让容器起不来
—— 这是**故意**的：缺表的实例比起不来的实例难排查得多。
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import time
from pathlib import Path

import psycopg2

# ── 日志 ────────────────────────────────────────────────────────────────
# 这段代码跑在 uvicorn 之前，日志就是用户唯一能看到的东西，格式简单直白即可。
logger = logging.getLogger("migrate")


def _setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | 迁移 | %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


# ── 连接参数 ────────────────────────────────────────────────────────────
# 连接方式与 app/services/database.py 的 get_conn() 保持一致（那边就是
# psycopg2.connect(DATABASE_URL)，没有连接池、不依赖 FastAPI 上下文）。
# 这里不 import 它，是为了让迁移这条启动路径尽量少依赖 —— 迁移跑在 uvicorn
# 起来之前，import 链越短，出问题时越好排查。默认值也与那边一致。
DEFAULT_DATABASE_URL = "postgresql://hunter:hunter@localhost:5432/hunter"

# 连不上就重试：compose 里有 depends_on: service_healthy，但云平台（Zeabur /
# Sealos / Railway）不保证这一点，postgres 比 api 慢起来是常态。
CONNECT_TIMEOUT_SEC = 60.0
CONNECT_RETRY_INTERVAL_SEC = 2.0

# ── advisory lock ───────────────────────────────────────────────────────
# 多副本（或滚动更新时新旧两个 api 容器）同时启动，会同时跑迁移。用**会话级**
# advisory lock 串行化：先拿到锁的跑，后来的等着，等到了发现已全部记录、0 个待执行。
#
# 锁号怎么来的：
#     sha256(b"hunter-community.schema_migrations").digest()[:8]
#     取大端 int 后与 (1<<63)-1 按位与（pg 的 advisory lock 参数是有符号 bigint，
#     掐到 63 位保证为正数，免得不同语言算出来的符号不一致）
#     = f8bd413b d8568f8d…  →  8700181780438093709
# 校验：python -c "import hashlib;d=hashlib.sha256(b'hunter-community.schema_migrations').digest();print(int.from_bytes(d[:8],'big')&((1<<63)-1))"
#
# ⚠️ 不要改这个值。advisory lock 只按数值排斥，改了之后**新旧版本的 api 同时启动时
#    会各自拿到不同的锁、互相不排斥**，等于没加锁 —— 而这正是滚动更新时最需要它的时刻。
ADVISORY_LOCK_NAMESPACE = "hunter-community.schema_migrations"
ADVISORY_LOCK_ID = 8700181780438093709

# ── 逃生舱 ──────────────────────────────────────────────────────────────
# HUNTER_MIGRATIONS_SKIP="0010_daily_close_view.sql,0014_daily_close_v2.sql"
# 把列出的文件**标记为已应用但不执行**。
#
# 什么时候需要它：库里已经是「比这个迁移更新」的状态，而账本里没有记录。典型是
# 有人手工 psql 补跑过迁移（0013/0014 的文件头就写着「apply(用户手动 · 别自动跑)」），
# 之后升级到带账本的版本，migrate 从 0001 重跑，撞上不可重复执行的文件。
# 已知例子见 docs/setup-wizard/M1-B-自动迁移.md：0010 在 0014 之后重跑会报
# 「cannot drop columns from view」。
#
# 这是**排障手段，不是日常配置**：跳过意味着「我确认这条 DDL 的效果库里已经有了」。
ENV_SKIP = "HUNTER_MIGRATIONS_SKIP"

# ── 迁移文件目录 ────────────────────────────────────────────────────────
# 镜像里的位置（apps/api/Dockerfile 把 db/migrations 拷到这里，与子任务 A 约定死，
# 不要改名）。本地直接跑（没有这个目录）时回落到仓库内相对路径。
DEFAULT_MIGRATIONS_DIR = "/opt/hunter-migrations"

# ── 阶段 ① 基础表 ──────────────────────────────────────────────────────
# `db/migrations/*.sql` 里有 7 个文件依赖的表**不是迁移文件建的**，而是
# app/services/database.py 的 init_db() 建的，而 init_db() 挂在 FastAPI 的
# lifespan 上 —— 也就是在本模块**之后**才跑。于是在全新空库上直接灌迁移会报：
#     ERROR: relation "stocks" does not exist            (0007 / 0015)
#     ERROR: relation "klines" does not exist            (0008 / 0010 / 0014)
#     ERROR: relation "backtest_result" does not exist   (0013 / 0016)
# 这不是本次改造引入的问题：改造之前 compose 把 db/migrations 挂成 postgres 的
# docker-entrypoint-initdb.d，initdb 同样跑在 api 之前，同样报这些错，只是 initdb
# 的 psql 默认不带 ON_ERROR_STOP，错误被静默吞掉，于是「老库缺列」年久失修。
#
# 所以顺序必须是：① init_db() 建基础表 → ② 跑增量迁移。
# init_db() 自身幂等（全是 IF NOT EXISTS），FastAPI 启动时还会再跑一次，跑两遍没关系。
#
# ⚠️ init_db() 内部 try/except 吞异常、只打 ERROR 日志不抛，所以本模块跑完必须
#    自己复查关键表在不在，不在就报清楚的错并非 0 退出。
BASELINE_REQUIRED_TABLES = ("stocks", "klines", "backtest_result")

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  filename   TEXT PRIMARY KEY,
  checksum   TEXT NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


# ════════════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════════════
def resolve_migrations_dir() -> Path:
    """定位迁移文件目录：环境变量 > 镜像内路径 > 仓库内相对路径。"""
    env_dir = os.getenv("HUNTER_MIGRATIONS_DIR", "").strip()
    if env_dir:
        path = Path(env_dir)
        if not path.is_dir():
            raise RuntimeError(
                f"HUNTER_MIGRATIONS_DIR 指向的目录不存在：{path}。"
                "要么把迁移文件放进去，要么去掉这个环境变量改用默认路径。"
            )
        return path

    candidates = [Path(DEFAULT_MIGRATIONS_DIR)]
    # 仓库内相对路径：本模块在 apps/api/app/migrate.py，往上三级是仓库根。
    # （容器里是 /app/app/migrate.py，往上三级不存在，所以要防越界。）
    here = Path(__file__).resolve()
    if len(here.parents) > 3:
        candidates.append(here.parents[3] / "db" / "migrations")
    # 工作目录为 apps/api/ 时的相对路径（容器里是 /app，本地直接跑也常是这个）。
    candidates.append(Path.cwd() / ".." / ".." / "db" / "migrations")

    for path in candidates:
        if path.is_dir():
            return path.resolve()

    raise RuntimeError(
        "找不到迁移文件目录。依次找过：" + "、".join(str(p) for p in candidates) +
        "。请设置环境变量 HUNTER_MIGRATIONS_DIR 指向 db/migrations。"
    )


def list_migration_files(migrations_dir: Path) -> list[Path]:
    """按文件名排序列出全部 .sql。

    排序用纯文件名字典序：现有文件是 0001_… 这种定宽编号，字典序即执行序。
    注意历史上有两个 0020_（rs_history 与 screen_learned_phrase），字典序下
    rs_history 在前，结果确定且可复现，不影响正确性。
    """
    return sorted((p for p in migrations_dir.glob("*.sql") if p.is_file()), key=lambda p: p.name)


def file_checksum(path: Path) -> str:
    """迁移文件内容的 sha256（十六进制）。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def connect_with_retry(dsn: str, timeout: float = CONNECT_TIMEOUT_SEC,
                       interval: float = CONNECT_RETRY_INTERVAL_SEC):
    """连库，连不上就重试，直到超时才放弃。"""
    deadline = time.monotonic() + timeout
    attempt = 0
    last_err: Exception | None = None
    while True:
        attempt += 1
        try:
            conn = psycopg2.connect(dsn)
            if attempt > 1:
                logger.info("数据库已就绪（第 %d 次尝试连上）", attempt)
            return conn
        except psycopg2.OperationalError as exc:
            last_err = exc
            if time.monotonic() >= deadline:
                break
            logger.info("数据库还没起来，%.0f 秒后重试（第 %d 次）：%s",
                        interval, attempt, str(exc).strip().splitlines()[0] if str(exc).strip() else exc)
            time.sleep(interval)
    raise RuntimeError(
        f"连接数据库失败：{timeout:.0f} 秒内重试 {attempt} 次都没连上。"
        f"请检查 DATABASE_URL 指向的主机/端口/账号是否正确、postgres 是否已启动。"
        f"最后一次错误：{last_err}"
    )


def _missing_baseline_tables(conn) -> list[str]:
    with conn.cursor() as cur:
        missing = []
        for table in BASELINE_REQUIRED_TABLES:
            cur.execute("SELECT to_regclass(%s)", (table,))
            if cur.fetchone()[0] is None:
                missing.append(table)
    conn.rollback()
    return missing


def run_baseline_ddl(conn) -> None:
    """阶段 ① · 执行 app/services/database.py 的 init_db() 建基础表。

    每次启动都跑（它幂等、约 1 秒）。不做「缺表才跑」的条件判断，是因为基础表清单
    以后还会长，条件判断只能盯住固定几张表，漏了就又是一次线上事故。
    """
    logger.info("① 基础表 DDL（app.services.database.init_db）开始")
    started = time.monotonic()
    try:
        import asyncio

        from app.services.database import init_db  # 只读调用，不修改该模块

        asyncio.run(init_db())
    except Exception as exc:  # noqa: BLE001 —— 失败要带原因往上抛
        raise RuntimeError(
            f"① 基础表 DDL 执行失败：{exc}\n"
            f"  说明：基础表没建出来的话，后面的增量迁移必然一片红（一堆 relation does not exist）。"
        ) from exc

    missing = _missing_baseline_tables(conn)
    if missing:
        # init_db() 内部 try/except 吞异常、只打 ERROR 日志不抛，所以必须自己复查。
        raise RuntimeError(
            "① 基础表 DDL 跑完之后这些表仍然不存在：" + "、".join(missing) +
            "。init_db() 是整个一个大事务，中间任何一步失败会全部回滚，而它只打 ERROR 日志不抛异常，"
            "所以真正的原因在上面 app.services.database 的日志里，请照着排查。"
        )
    logger.info("① 基础表 DDL 完成，耗时 %.2f 秒", time.monotonic() - started)


def parse_skip_list(files: list[Path]) -> set[str]:
    """解析 HUNTER_MIGRATIONS_SKIP，顺便挡住写错的文件名。"""
    raw = os.getenv(ENV_SKIP, "")
    names = {n.strip() for n in raw.replace(",", " ").split() if n.strip()}
    if not names:
        return set()
    known = {p.name for p in files}
    unknown = sorted(names - known)
    if unknown:
        raise RuntimeError(
            f"{ENV_SKIP} 里有不存在的文件名：" + "、".join(unknown) +
            "。请填 db/migrations 下的完整文件名（含 .sql）。"
        )
    return names


def _fix_hint(exc: psycopg2.Error, filename: str) -> str:
    """针对已知坑给一句能照做的修复建议。"""
    # 42P16 invalid_object_definition · CREATE OR REPLACE VIEW 少了已有的列
    if getattr(exc, "pgcode", None) == "42P16" and "view" in str(exc).lower():
        return (
            f"\n  可能原因：这个视图在库里已经是**更新的版本**（列更多），"
            f"而 CREATE OR REPLACE VIEW 不允许减列。多半是有人手工补跑过后面的迁移。\n"
            f"  两种修法（任选一种，都要在确认库里的视图定义确实更新之后再做）：\n"
            f"    a) 让它重建一遍：psql 里执行 DROP VIEW IF EXISTS <视图名> CASCADE; 然后重启 api\n"
            f"    b) 直接跳过：给 api 加环境变量 {ENV_SKIP}={filename} 后重启"
        )
    return ""


def _error_location(sql_text: str, exc: psycopg2.Error) -> str:
    """把 psycopg2 报的字符偏移换算成「第几行」，顺便带上那一行的内容。"""
    position = None
    diag = getattr(exc, "diag", None)
    for attr in ("statement_position", "internal_position"):
        raw = getattr(diag, attr, None) if diag else None
        if raw:
            try:
                position = int(raw)
                break
            except (TypeError, ValueError):
                pass
    if not position:
        return "（报错位置未知）"
    head = sql_text[: position - 1]
    line_no = head.count("\n") + 1
    lines = sql_text.splitlines()
    line_text = lines[line_no - 1].strip() if 0 < line_no <= len(lines) else ""
    return f"第 {line_no} 行：{line_text}"


# ════════════════════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════════════════════
def fetch_applied(conn) -> dict[str, str]:
    """读已记录的迁移；表还没建时返回空。"""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('schema_migrations')")
        if cur.fetchone()[0] is None:
            conn.rollback()
            return {}
        cur.execute("SELECT filename, checksum FROM schema_migrations")
        rows = cur.fetchall()
    conn.rollback()
    return {name: checksum for name, checksum in rows}


def warn_checksum_drift(applied: dict[str, str], files: list[Path]) -> None:
    """已记录但内容变了 → 只 WARNING，绝不重跑。

    为什么不重跑：已记录意味着这条 DDL 在**这个库**上生效过了，重跑最好的情况是
    白跑一遍，最坏的情况是把线上数据改坏（比如迁移里带 UPDATE 的 0006）。
    迁移文件一旦发出去就该当成不可变的。
    """
    for path in files:
        recorded = applied.get(path.name)
        if recorded is None:
            continue
        current = file_checksum(path)
        if current != recorded:
            logger.warning(
                "迁移文件被改过：%s（记录 %s… → 现在 %s…）。**不会重跑**，"
                "因为它在本库上已经生效过，重跑可能改坏已有数据。"
                "如果确实需要让这次改动生效，请新增一个迁移文件（例如 %s），不要改老文件。",
                path.name, recorded[:12], current[:12], "00XX_fix_xxx.sql",
            )


def apply_one(conn, path: Path) -> float:
    """在**独立事务**里执行一个迁移文件，成功后记账。返回耗时（秒）。

    为什么每个文件一个事务而不是整批一个事务：整批一个事务的话，第 20 个文件失败会把
    前 19 个一起回滚，修好之后还得从头重跑一遍 —— 而 DDL 重跑虽然幂等但白等；更糟的是
    「已经成功的部分到底算不算数」这件事会变得说不清。一个文件一个事务，失败点前面的
    全部已提交并记账，修完只需要跑剩下的。
    """
    sql_text = path.read_text(encoding="utf-8")
    checksum = file_checksum(path)
    started = time.monotonic()
    try:
        with conn.cursor() as cur:
            cur.execute(sql_text)
            cur.execute(
                "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s) "
                "ON CONFLICT (filename) DO NOTHING",
                (path.name, checksum),
            )
        conn.commit()
    except psycopg2.Error as exc:
        conn.rollback()
        raise RuntimeError(
            f"迁移 {path.name} 执行失败\n"
            f"  位置：{_error_location(sql_text, exc)}\n"
            f"  错误：{str(exc).strip()}\n"
            f"  说明：本文件已整体回滚，它**之前**的迁移都已提交并记账，"
            f"修好这个文件后重启 api 会从它开始继续跑。"
            + _fix_hint(exc, path.name)
        ) from exc
    return time.monotonic() - started


def mark_applied(conn, path: Path) -> None:
    """只记账、不执行（HUNTER_MIGRATIONS_SKIP 用）。"""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s) "
            "ON CONFLICT (filename) DO NOTHING",
            (path.name, file_checksum(path)),
        )
    conn.commit()


def run(dry_run: bool = False) -> int:
    overall_started = time.monotonic()
    dsn = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    migrations_dir = resolve_migrations_dir()
    files = list_migration_files(migrations_dir)
    logger.info("迁移目录：%s", migrations_dir)
    if not files:
        raise RuntimeError(f"迁移目录 {migrations_dir} 里一个 .sql 都没有，这多半是镜像打包漏了文件。")
    skip = parse_skip_list(files)
    if skip:
        logger.warning("%s 生效：%s 将只记账不执行", ENV_SKIP, "、".join(sorted(skip)))

    conn = connect_with_retry(dsn)
    locked = False
    try:
        if dry_run:
            # dry-run 不建表、不加锁、不动任何东西：它的用途是「线上出问题时先看一眼
            # 到底差哪些迁移」，加锁反而可能卡在正在跑迁移的另一个副本后面。
            applied = fetch_applied(conn)
            pending = [p for p in files if p.name not in applied]
            warn_checksum_drift(applied, files)
            logger.info("② 增量迁移：共 %d 个迁移文件，已应用 %d 个，本次待执行 %d 个（--dry-run，不执行）",
                        len(files), len(applied), len(pending))
            for path in pending:
                logger.info("  待执行：%s", path.name)
            return 0

        # ① 会话级 advisory lock —— 多副本并发保护
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_ID,))
        conn.commit()
        locked = True
        logger.debug("已取得 advisory lock %d", ADVISORY_LOCK_ID)

        # ② 阶段 ① · 基础表 DDL（必须在增量迁移之前，理由见 BASELINE_REQUIRED_TABLES 注释）
        run_baseline_ddl(conn)

        # ③ 账本表（migrate.py 自己建一份，内容与 db/migrations/0022_schema_migrations.sql 一致；
        #    它得先有表才能查有哪些迁移跑过，没法靠迁移文件自己把自己建出来）
        with conn.cursor() as cur:
            cur.execute(SCHEMA_MIGRATIONS_DDL)
        conn.commit()

        # ④ 阶段 ② · 算出待执行清单
        applied = fetch_applied(conn)
        warn_checksum_drift(applied, files)
        pending = [p for p in files if p.name not in applied]
        logger.info("② 增量迁移（db/migrations）：共 %d 个迁移文件，已应用 %d 个，本次待执行 %d 个",
                    len(files), len(applied), len(pending))

        # ⑤ 逐个执行，每个一个独立事务
        for index, path in enumerate(pending, start=1):
            if path.name in skip:
                mark_applied(conn, path)
                logger.warning("  [%d/%d] %s 按 %s 跳过：只记账、不执行。"
                               "这意味着你确认这条 DDL 的效果库里已经有了。",
                               index, len(pending), path.name, ENV_SKIP)
                continue
            elapsed = apply_one(conn, path)
            logger.info("  [%d/%d] %s 完成，耗时 %.3f 秒", index, len(pending), path.name, elapsed)

        logger.info("② 增量迁移完成：本次执行 %d 个；两阶段总耗时 %.2f 秒",
                    len(pending), time.monotonic() - overall_started)
        return 0
    finally:
        if locked:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_ID,))
                conn.commit()
            except psycopg2.Error:
                pass  # 连接马上就关了，会话级锁会跟着连接一起释放
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.migrate",
        description="按文件名顺序执行 db/migrations/*.sql，已执行过的跳过。",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印待执行清单，不执行任何 SQL")
    args = parser.parse_args(argv)

    _setup_logging()
    try:
        return run(dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 —— 顶层兜底：打人话再非 0 退出
        logger.error("迁移失败，api 不会启动。原因如下：\n%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
