"""命令级幂等（`05 §3.2` M-13 · `01方案 §11.1`）。

三条规则，逐条对应方案原文：

| 情形 | 行为 |
|---|---|
| 同一键、同一内容 | **返回原结果**，不再记一次账 |
| 同一键、不同内容 | **返回冲突**，不覆盖旧记录 |
| 已完成请求 | **先返回原回执，再处理新的账户版本校验** |

最后一条是最容易写反的：如果先校验账户版本，一次「已经成功、只是回执丢在路上」
的重试会被版本校验拒掉 —— 用户看到「账户版本冲突」，而他其实只是重试了一次
已经成功的下单（`01方案 §11.1` 末两条）。所以顺序必须是
**幂等命中 → 直接返回原回执 → 否则才校验版本**。

「幂等记录与账本变更在同一个事务里提交」：本模块只 `INSERT` 一行，
而调用方（`matching.engine.execute`）与账本变更共用 `db.cursor(commit=True)`
的那一个事务 —— 不存在「账记了、幂等没记」或反之的中间态。

`request_hash` 是 **canonical payload 的 sha256**：同一笔业务请求换个字段顺序、
换一种 Decimal 写法（`10.0` vs `10.00`）必须算出同一个 hash，否则「同内容的重试」
会被误判成「不同内容」而返回冲突。`canonical_payload` 负责这件事。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

import psycopg2.extras

# 参与请求摘要的字段：**只放业务语义**。
# 不放 created_at / 时间戳这类每次都会变的东西 —— 放了就等于「同内容的重试」永远
# 算不出同一个 hash。
HASH_FIELDS = (
    "project_id", "code", "side", "qty", "price_type", "limit_price",
    "source", "actor", "valid_until", "intent_ref", "decision_ref",
    "expected_version",
)


def _norm(value: Any) -> Any:
    """把值归一成可比较的规范形式。

    - `Decimal` → `str`，且**去掉尾随零**（`10.00` 与 `10.0` 是同一个价）；
    - `datetime` → ISO 8601（带时区偏移，`Z` 与 `+00:00` 归一）；
    - `dict` / `list` 递归。
    """
    if isinstance(value, Decimal):
        normalized = value.normalize()
        # normalize 会把 10 写成 1E+1，回退成定点写法免得两种表达撞不上
        return format(normalized, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _norm(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    return value


def canonical_payload(payload: dict) -> dict:
    """取出参与摘要的字段，归一化，按键排序。"""
    picked = {k: _norm(payload.get(k)) for k in HASH_FIELDS if payload.get(k) is not None}
    return {k: picked[k] for k in sorted(picked)}


def request_hash(payload: dict) -> str:
    body = json.dumps(canonical_payload(payload), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class IdempotencyConflict(Exception):
    """同一键、不同内容。调用方转成 409，**不覆盖**旧记录。"""

    def __init__(self, key: str):
        self.key = key
        super().__init__(f"幂等键 {key} 已被另一份内容使用过，拒绝覆盖（返回冲突）")


def lookup(cur, key: str) -> Optional[dict]:
    cur.execute(
        "SELECT idempotency_key, request_hash, response_ref, response, project_id, created_at "
        "FROM fin_idempotency WHERE idempotency_key = %s",
        (key,),
    )
    return cur.fetchone()


def _jsonable(value: Any) -> Any:
    """把回执转成能进 JSONB 的形状。

    `psycopg2.extras.Json` 走的是标准 `json.dumps`，**不认识 `Decimal`** ——
    直接塞会 TypeError。项目里金额一律 `Decimal`（`09 §六-8`），所以在这里
    统一转成字符串：与 `jsonresp.LedgerJSONResponse` 对外的口径**逐字一致**，
    于是「首次回执」与「重放回执」在客户端看来是同一个东西。
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def record(
    cur,
    key: str,
    req_hash: str,
    project_id: Optional[str],
    response: dict,
    response_ref: Optional[str] = None,
) -> None:
    """记一行幂等。**不 catch 主键冲突**：真撞了就让事务回滚 —— 那说明调用方
    没先 `lookup`，是代码 bug，不该被静默吞掉。"""
    cur.execute(
        """
        INSERT INTO fin_idempotency
          (idempotency_key, request_hash, response_ref, response, project_id)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (key, req_hash, response_ref, psycopg2.extras.Json(_jsonable(response)), project_id),
    )


def replay_or_conflict(cur, key: Optional[str], req_hash: str) -> Optional[dict]:
    """幂等命中 → 返回原回执（已带 `idempotent_replay`）；不同内容 → 抛冲突。

    返回 `None` 表示「这个键还没用过」，调用方继续走正常的四步链路。
    """
    if not key:
        return None
    existing = lookup(cur, key)
    if not existing:
        return None
    if existing["request_hash"] != req_hash:
        raise IdempotencyConflict(key)
    stored = existing.get("response") or {}
    return {
        **stored,
        "idempotent_replay": True,
        "order_id": stored.get("order_id") or existing.get("response_ref"),
    }
