"""kpred_cache — Kronos 预测链路的通用 Redis 缓存封装。

设计要点:
  · Redis 不可用时**自动降级为不缓存** · 绝不阻塞主流程
  · JSON 序列化 · 支持任意 dict / list / 标量
  · TTL 加 ±10% 抖动 · 防批量过期雪崩
  · 单例 client · 惰性初始化 · socket_timeout=2s

Key 命名规范:
  · kpred:kronos:{code}:{days}     · Kronos 结果       · TTL 10 min
  · kpred:pe:{code}:{yyyymmdd}     · PE 评分           · TTL 24 hour
  · kpred:kline:{code}:{limit}     · K 线              · TTL 5 min(留给日后)
  · kpred:flow:{code}:5d           · 5 日资金流        · TTL 15 min(留给日后)
"""
import os
import json
import random
from loguru import logger

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_redis = None


def _client():
    """惰性单例 · 失败时返回 None(降级为不缓存)"""
    global _redis
    if _redis is None:
        try:
            import redis as _redis_mod
            _redis = _redis_mod.Redis.from_url(_REDIS_URL, socket_timeout=2)
            _redis.ping()
        except Exception as e:
            logger.warning("[kpred_cache] Redis 不可用 · 降级为不缓存 · {}", e)
            _redis = False   # 用 False 而非 None · 避免下次重复尝试
    return _redis or None


def get(key: str):
    """命中返回 JSON 反序列化后的对象 · 未命中/失败返回 None"""
    r = _client()
    if not r:
        return None
    try:
        raw = r.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.debug("[kpred_cache] get {} failed: {}", key, e)
        return None


def set(key: str, value, ttl: int) -> None:
    """写入 · TTL 加 ±10% 抖动 · 失败静默"""
    r = _client()
    if not r:
        return
    try:
        jittered = int(ttl * (1 + random.uniform(-0.1, 0.1)))
        r.setex(key, max(1, jittered), json.dumps(value, ensure_ascii=False))
    except Exception as e:
        logger.debug("[kpred_cache] set {} failed: {}", key, e)


def delete(key: str) -> None:
    """主动失效 · 失败静默"""
    r = _client()
    if not r:
        return
    try:
        r.delete(key)
    except Exception:
        pass
