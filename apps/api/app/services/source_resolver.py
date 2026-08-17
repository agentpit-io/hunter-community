"""数据源解析链 —— 「优先用户的 → 失败降级官方 → 标明用了谁」(`_21` §6)。

**这是整个 `_21` 里真正的架构改动。**在这之前取数走的是
`providers/data_source/__init__.py` 的 `get_data_source()`,那是个
**全局 env 单例**:

    _INSTANCE = None
    provider = os.getenv("DATA_SOURCE_PROVIDER") or "hunter"

三个问题,每一个都直接挡住"用户脱离我们也能玩转":
  1. 没有 user_id → 无法"优先用户自己的"
  2. 一次只选一个 → 没有"失败了降级"的概念,只有"启动时选定"
  3. 进程级单例 → A 用户配的源会影响 B 用户

**这里采用的是"加一层在前面"而不是"重写取数层"。** 理由:

    用户没配任何源时(现在 100% 的情况),这一层直接放行,
    走的还是原来那条久经使用的路径,行为**逐字节不变**。

重写 `finance_data_client` 里那十几个函数才能做到"统一降级",
但那会把一条从没出过问题的热路径整个换掉,换来的是当下没人用到的能力。
等真有用户配了源、跑出问题了,再谈重写。

## 熔断为什么必须有

降级链是"用户的失败了才走我们的"。没有熔断的话,用户配了个连不上的源,
**每一次请求**都要先卡满超时再降级 —— 表现是"整个平台变慢了",
而根因藏在一个他自己填错的地址里,极难联想。

熔断状态存在库里(`fail_streak` / `cooldown_until`)而不是进程内存:
多 worker 部署时进程内存各算各的,一个源要被 N 个 worker 分别熔断 N 次。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from loguru import logger

from app.services import request_ctx, source_mapping
from app.services.mcp_crypto import decrypt
from app.services.request_ctx import SourceUse

# 连续失败几次进冷却 · 冷却多久
FAIL_THRESHOLD = 3
COOLDOWN_MIN = 10


@dataclass
class UserSource:
    id: int
    name: str
    upstream: str
    endpoint: str
    requires_key: bool
    key_in: str
    key_name: str
    key_prefix: str
    key_enc: str | None
    headers: dict
    field_map: dict
    timeout_ms: int


def _candidates(uid: str, market: str, kind: str) -> list[UserSource]:
    """当前用户在这个 (市场,类型) 槽位上**可用**的源。

    SQL 里就把冷却中的排除掉 —— 在 Python 里过滤的话,冷却判断会散在
    调用方,早晚有一处忘了。
    """
    try:
        from app.services.database import get_conn
    except Exception:
        return []
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, upstream, endpoint, requires_key, key_in, key_name, "
            "       key_prefix, api_key_enc, headers, field_map, timeout_ms "
            "FROM user_data_sources "
            "WHERE user_id=%s AND market=%s AND kind=%s AND enabled "
            "  AND (cooldown_until IS NULL OR cooldown_until < NOW()) "
            "ORDER BY updated_at DESC",
            (uid, market, kind),
        )
        rows = cur.fetchall()
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[resolver] 查用户源失败(按无处理): {}", e)
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return [UserSource(*r) for r in rows]


def _mark(sid: int, ok: bool, err: str = "") -> None:
    """记一次调用结果 —— 成功清零,失败累加并可能进冷却。"""
    try:
        from app.services.database import get_conn
    except Exception:
        return
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        if ok:
            cur.execute(
                "UPDATE user_data_sources SET fail_streak=0, cooldown_until=NULL, "
                "last_ok_at=NOW(), last_err='', call_count=call_count+1 WHERE id=%s",
                (sid,),
            )
        else:
            # 冷却时间在 SQL 里算,避免 Python 与数据库时区不一致 ——
            # asyncpg/psycopg 那边为时区问题已经栽过一次
            cur.execute(
                "UPDATE user_data_sources SET fail_streak=fail_streak+1, "
                "  last_err=%s, call_count=call_count+1, error_count=error_count+1, "
                "  cooldown_until = CASE WHEN fail_streak+1 >= %s "
                "                        THEN NOW() + (%s || ' minutes')::interval "
                "                        ELSE cooldown_until END "
                "WHERE id=%s",
                (err[:400], FAIL_THRESHOLD, str(COOLDOWN_MIN), sid),
            )
        conn.commit()
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[resolver] 记录调用结果失败: {}", e)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _fetch_one(src: UserSource, symbol: str) -> dict:
    """打一次用户的源并映射。任何一步失败都抛异常,由上层降级。"""
    ep = src.endpoint.replace("{symbol}", symbol).replace("{code}", symbol)
    headers = {k: str(v) for k, v in (src.headers or {}).items()}
    params: dict = {}
    body: dict | None = None

    if src.requires_key and src.key_enc:
        raw = decrypt(src.key_enc)
        val = f"{src.key_prefix}{raw}" if src.key_prefix else raw
        if src.key_in == "header":
            headers[src.key_name] = val
        elif src.key_in == "query":
            params[src.key_name] = val
        else:
            body = {src.key_name: val}

    timeout = min(max(src.timeout_ms, 1000), 30000) / 1000
    with httpx.Client(timeout=timeout, follow_redirects=True) as c:
        r = (c.post(ep, headers=headers, params=params, json=body)
             if body is not None else c.get(ep, headers=headers, params=params))
    if not r.is_success:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.json()


def try_user(market: str, kind: str, symbol: str) -> dict | None:
    """先试用户自己的源。**没有可用的就返回 None,由调用方走原路径。**

    返回 None 与抛异常是两件事:
      · None  —— 用户压根没配 / 全在冷却 → 这是**正常状态**,不是降级
      · 走到最后仍失败 → 记 provenance,让 UI 能说"你的源没用上,原因是…"

    这个区分直接决定徽章弹不弹。全程走官方(用户没配)不该弹,
    那是常态;试过用户的没成功才该弹。
    """
    uid = request_ctx.user_id()
    if not uid:
        return None
    srcs = _candidates(uid, market, kind)
    if not srcs:
        return None

    tried: list[dict] = []
    t0 = time.time()
    for s in srcs:
        try:
            raw = _fetch_one(s, symbol)
            data = source_mapping.apply(s.upstream, kind, raw, s.field_map or None)
            _mark(s.id, True)
            request_ctx.record(SourceUse(
                market=market, kind=kind, used=f"user:{s.id}", used_label=s.name,
                ok=True, tried=tried, ms=int((time.time() - t0) * 1000),
            ))
            return data
        except source_mapping.MappingError as e:
            # 映射失败**不计入熔断** —— 这不是"源连不上",是我们的映射和
            # 它的格式对不上。熔断它没用(下次一样对不上),而且会让用户
            # 以为是网络问题。原因照实记下来,让他能去详情里改映射
            reason = str(e)
            _mark(s.id, False, reason)
            tried.append({"label": s.name, "reason": reason})
            logger.info("[resolver] {} 映射失败: {}", s.name, reason)
        except Exception as e:                                # noqa: BLE001
            reason = f"{type(e).__name__}: {str(e)[:120]}"
            _mark(s.id, False, reason)
            tried.append({"label": s.name, "reason": reason})
            logger.info("[resolver] {} 取数失败: {}", s.name, reason)

    # 全试完了都没成 —— 记成"降级到官方",这条会让徽章亮起来
    request_ctx.record(SourceUse(
        market=market, kind=kind, used="official", used_label="官方源",
        ok=True, tried=tried, ms=int((time.time() - t0) * 1000),
    ))
    return None
