"""Sub-agent / MCP tool 结果缓存 · Redis 版

对应 03 §8.2 与 04 T-P2-15：
  - sub-agent 结果：TTL 5min（同 code + 同 tool + 同 args_hash）
  - MCP 数据类工具：TTL 60s

失败静默降级（Redis 不可用时 cache miss 全部通过）。
"""
from __future__ import annotations
import hashlib
import json
import os
import time
from typing import Optional
from loguru import logger

try:
    import redis as _redis_lib
    _redis = _redis_lib.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True, socket_connect_timeout=1, socket_timeout=1,
    )
except Exception as e:  # noqa: BLE001
    logger.warning("[agent-cache] Redis 初始化失败，缓存全 miss: {}", e)
    _redis = None


# 各 tool 的 TTL 秒数（默认 60）
_TTL_MAP: dict[str, int] = {
    # MCP tools
    "get_quote":      60,
    "get_kline":      120,
    "get_pe_history": 900,
    # Sub-agents（贵，长 TTL）
    "research":        300,
    "scout":           300,
    "quant_predict":   300,
    "hold_judge":      300,   # 尤其贵（15-30s），命中收益大
    "event_interpret": 180,
}


def _key(name: str, args: dict) -> str:
    """稳定 key = agent-cache:{name}:{sha256(sorted-args-json)[:16]}"""
    body = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    h = hashlib.sha256(body.encode()).hexdigest()[:16]
    return f"agent-cache:{name}:{h}"


def get(name: str, args: dict) -> Optional[dict]:
    """命中返回 dict（含 summary + detail_ref）；miss 返回 None"""
    if _redis is None:
        return None
    try:
        raw = _redis.get(_key(name, args))
        if not raw:
            return None
        return json.loads(raw)
    except Exception as e:  # noqa: BLE001
        logger.warning("[agent-cache] get 失败 name={}: {}", name, e)
        return None


def put(name: str, args: dict, summary: dict,
        detail_ref: Optional[dict] = None) -> None:
    if _redis is None:
        return
    ttl = _TTL_MAP.get(name, 60)
    try:
        payload = {
            "summary": summary,
            "detail_ref": detail_ref,
            "cached_at": int(time.time()),
        }
        _redis.setex(_key(name, args), ttl, json.dumps(payload, ensure_ascii=False, default=str))
    except Exception as e:  # noqa: BLE001
        logger.warning("[agent-cache] put 失败 name={}: {}", name, e)


def clear(name: str = "*") -> int:
    """清理某个 tool（或全部）的缓存。返回删除条数。"""
    if _redis is None:
        return 0
    try:
        pat = f"agent-cache:{name}:*" if name != "*" else "agent-cache:*"
        keys = list(_redis.scan_iter(match=pat, count=200))
        if not keys:
            return 0
        return int(_redis.delete(*keys))
    except Exception as e:  # noqa: BLE001
        logger.warning("[agent-cache] clear 失败: {}", e)
        return 0
