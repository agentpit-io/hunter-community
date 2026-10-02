"""账本库连接。

**没有连接池**：Paper Service 是单写者（`09 §六-4`），一个请求一条连接，
用完即关，事务边界就是请求边界。这样「一笔成交 = 一个事务」是显式可读的，
不需要靠池的行为去推断。并发靠 `fin_project.version` 乐观锁（M13 补强）。

连接用的角色由 `PAPER_DATABASE_URL` 决定 —— 生产上是 `fin_paper_rw`
（只 `INSERT`/`SELECT` 追加表，改不了历史）。自检会验证这一点。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg2
import psycopg2.extras

from app import config


def connect():
    conn = psycopg2.connect(config.database_url())
    # 时间一律按上海时区回显（`09 §二`：业务时刻以数据源为准，展示按上海时间）。
    # `TIMESTAMPTZ` 存的是绝对时刻，这里只影响读出来的呈现，不改数据。
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'Asia/Shanghai'")
    conn.commit()
    return conn


@contextmanager
def cursor(commit: bool = False) -> Iterator[psycopg2.extras.RealDictCursor]:
    """一个请求一个事务。

    `commit=True` 才提交 —— 读路径默认不提交（只读事务回滚即可，省一次写 WAL）。
    """
    conn = connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        if commit:
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()
