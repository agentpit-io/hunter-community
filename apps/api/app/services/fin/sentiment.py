"""情绪 —— 表 + 登记口 + 口径（五期 L09 · 五期方案 §四 `L09` · 技术方案 §6.1 / §6.2）。

## 这一段刻意**只做三件事**（`plan/五期追加规则.md` §3.2 第 2 条 / 方案 §6.1 的缩小范围）

1. **建表**：`fin_sentiment`（迁移 `0053`，**只追加** · BEFORE UPDATE/DELETE 触发器抛异常）；
2. **登记口**：`register_sentiment`（写）+ `read_sentiment`（读回），**留痕**（改不了、删不掉）；
3. **口径**：每一行必须带清楚 **模型版本 / 证据引用 / 生成时间 / 质量标记**（§6.2 末句）。

## ⛔ 情绪**数据源未接** —— 这是如实的，不是缺陷

**当前没有任何情绪数据源接入**（`grep sentiment/情绪` 命中的只有 `online_analysis` 的
新闻情绪分析器，它服务的是「在线分析」那条产品线，不产出可落库的市场情绪，也没接调度）。
按方案 §6.1「数据源没有的，只做表 + 登记口」，本模块**只提供登记口**，由人工 / 未来的
程序写入。缺什么、为什么、将来接哪，见 `docs/开发文档/L09-采集补齐.md`「未接数据源清单」。

## ⛔ 红线 5：**绝不编情绪值**

- `sentiment` 可为 `NULL` —— 只有标签没有数值时留空，**不填 0**（0.0 看起来像个结论）。
- **不用涨跌幅 / 成交量 / 任何派生量冒充情绪** —— 那些是行情，不是情绪。
- `generated_at` 是**必填**：它是「这条记录被生成的时刻」，由登记方给出。
  ⚠️ 与 `available_at`（数据源实际可获取时间）**不是一回事** —— 后者拿不到就留 `NULL`
  （红线 5 · 见 `L03`）：**不允许**拿 `generated_at` 去冒充一个「数据源可获取时间」。
- `evidence_ref`（证据引用）与 `model_version`、`quality`、`source` 都是**必填**：
  一行说不清来路的情绪没有价值，宁可拒绝（400），也不写一行「无来源的情绪」。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import psycopg2
from loguru import logger

# 连接方式与 `app/services/fin/data_snapshot.py` / `app/services/fin/collection.py` 同口径。
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://hunter:hunter@localhost:5432/hunter")

MARKET_VALUES = ("CN_A", "HK", "US")


def get_conn():
    return psycopg2.connect(DATABASE_URL)


class SentimentError(ValueError):
    """情绪登记入参不合法（路由层翻 400）。**拒绝，不猜、不填默认值。**"""


def _parse_ts(value, field: str) -> datetime:
    """ISO8601 → 带时区 datetime。空 / 解析不出 / 无时区 → 400（**不猜、不用 now() 顶替**）。"""
    raw = (value or "").strip() if isinstance(value, str) else value
    if not raw:
        raise SentimentError(f"{field} 必填（ISO8601 带时区，如 2026-10-04T12:00:00+08:00）")
    if isinstance(raw, datetime):
        dt = raw
    else:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError as exc:
            raise SentimentError(f"{field} 不是合法 ISO8601 时间：{value!r}") from exc
    if dt.tzinfo is None:
        # 没有时区的时间在跨市场场景里是歧义的 —— 让它 400，别替调用方选一个时区。
        raise SentimentError(f"{field} 必须带时区偏移（收到无时区的 {value!r}）")
    return dt


def _require_text(payload: dict, field: str) -> str:
    val = payload.get(field)
    if not isinstance(val, str) or not val.strip():
        raise SentimentError(f"{field} 必填（非空字符串）")
    if len(val) > 200:
        raise SentimentError(f"{field} 过长（≤200 字）")
    return val.strip()


def _optional_text(payload: dict, field: str, limit: int = 2000) -> Optional[str]:
    val = payload.get(field)
    if val is None:
        return None
    if not isinstance(val, str):
        raise SentimentError(f"{field} 必须是字符串")
    val = val.strip()
    if not val:
        return None
    if len(val) > limit:
        raise SentimentError(f"{field} 过长（≤{limit} 字）")
    return val


def _optional_number(payload: dict, field: str):
    val = payload.get(field)
    if val is None or val == "":
        return None
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise SentimentError(f"{field} 必须是数字或省略（拿不到值就留空，别填 0）")
    return val


def register_sentiment(payload: dict) -> dict:
    """登记一条情绪（**只追加**）。返回落库后的那一行。

    必填：`code` · `as_of` · `model_version` · `quality` · `source` · `generated_at`。
    可空：`sentiment`（拿不到 → NULL）· `sentiment_label` · `evidence_ref` · `market` · `note`。
    """
    if not isinstance(payload, dict):
        raise SentimentError("请求体必须是对象")
    code = _require_text(payload, "code")
    market = _optional_text(payload, "market")
    if market is not None and market not in MARKET_VALUES:
        raise SentimentError(f"market 必须是 {list(MARKET_VALUES)} 之一或省略，收到 {market!r}")
    as_of = _parse_ts(payload.get("as_of"), "as_of")
    generated_at = _parse_ts(payload.get("generated_at"), "generated_at")
    model_version = _require_text(payload, "model_version")
    quality = _require_text(payload, "quality")
    source = _require_text(payload, "source")
    sentiment = _optional_number(payload, "sentiment")
    label = _optional_text(payload, "sentiment_label")
    evidence_ref = _optional_text(payload, "evidence_ref")
    note = _optional_text(payload, "note")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fin_sentiment
                  (code, market, as_of, sentiment, sentiment_label, model_version,
                   evidence_ref, generated_at, quality, source, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, code, market, as_of, sentiment, sentiment_label,
                          model_version, evidence_ref, generated_at, quality, source,
                          note, created_at
                """,
                (code, market, as_of, sentiment, label, model_version,
                 evidence_ref, generated_at, quality, source, note),
            )
            row = cur.fetchone()
        conn.commit()
        logger.info("[sentiment] 登记 {} 市场={} 值={} 模型={} 质量={} 来源={}",
                    code, market, sentiment, model_version, quality, source)
        return to_json(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_COLS = ("id, code, market, as_of, sentiment, sentiment_label, model_version, "
         "evidence_ref, generated_at, quality, source, note, created_at")


def read_sentiment(code: str, limit: int = 20) -> list[dict]:
    """按标的读回最近的情绪（`as_of` 倒序）。**只读**。"""
    code = (code or "").strip()
    if not code:
        raise SentimentError("code 必填")
    if limit < 1 or limit > 500:
        raise SentimentError("limit 必须在 1..500")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLS} FROM fin_sentiment WHERE code = %s "
                "ORDER BY as_of DESC, id DESC LIMIT %s",
                (code, limit),
            )
            rows = cur.fetchall()
        return [to_json(r) for r in rows]
    finally:
        conn.close()


def freshness(limit: int = 1) -> dict:
    """情绪表的新鲜度读数：行数 + 最新一行（验收「登记口能写」时贴它）。

    **没有行时如实返回 0 / null** —— 不假装「有情绪数据」。
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), max(generated_at), max(as_of) FROM fin_sentiment")
            n, gen, asof = cur.fetchone()
            cur.execute(f"SELECT {_COLS} FROM fin_sentiment ORDER BY id DESC LIMIT %s", (limit,))
            rows = [to_json(r) for r in cur.fetchall()]
        return {"rows": int(n),
                "latest_generated_at": gen.isoformat() if gen else None,
                "latest_as_of": asof.isoformat() if asof else None,
                "latest": rows[0] if rows else None}
    except psycopg2.errors.UndefinedTable:
        # 表还没建（迁移未应用）—— 如实说，不 500。
        return {"rows": 0, "latest_generated_at": None, "latest_as_of": None,
                "latest": None, "reason": "fin_sentiment 表不存在（迁移 0053 未应用）"}
    finally:
        conn.close()


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        # NUMERIC 回来是 Decimal —— 转 float 便于 JSON（只影响展示精度，不改库存值）。
        # ⚠️ 只转 Decimal，**不要**顺手把所有 int 也转 float：`id` 是 BIGINT，
        #    变成 `1.0` 会被前端当成浮点 id（一读就看得出来）。
        return float(value)
    return value


def to_json(row) -> dict:
    if row is None:
        return {}
    if isinstance(row, dict):
        items = row.items()
    else:
        items = zip(["id", "code", "market", "as_of", "sentiment", "sentiment_label",
                     "model_version", "evidence_ref", "generated_at", "quality",
                     "source", "note", "created_at"], row)
    return {k: _jsonable(v) for k, v in items}


__all__ = ["SentimentError", "register_sentiment", "read_sentiment", "freshness",
           "to_json", "MARKET_VALUES"]
