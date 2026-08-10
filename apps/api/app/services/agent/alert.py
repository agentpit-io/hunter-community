"""Agent Chat V2 · 简单告警到飞书群机器人 webhook

依赖环境变量：
  HERMES_ALERT_LARK_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/<token>
未配置时静默不告警（不阻塞主流程）。

滚动窗口指标（存 Redis list，最新 100 条）：
  agent:stats:duration_ms  · 每次 message_end 的 total_ms
  agent:stats:cost_cny     · 每次 message_end 的 cost_cny
  agent:stats:status       · 每次 tool_result 的 status (ok/error)

冷却时长：单类告警 10 分钟内不重复发。
"""
from __future__ import annotations
import os
import time
from typing import Optional
import httpx
from loguru import logger


_WEBHOOK = os.getenv("HERMES_ALERT_LARK_WEBHOOK", "")
_COOLDOWN_SEC = 600  # 10min

try:
    import redis as _redis_lib
    _redis = _redis_lib.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True, socket_connect_timeout=1, socket_timeout=1,
    )
except Exception:
    _redis = None


# ─────────────────────── 滚动窗口 ───────────────────────
def record_message_end(total_ms: int, cost_cny: float, fallback: bool):
    if _redis is None:
        return
    try:
        pipe = _redis.pipeline()
        pipe.lpush("agent:stats:duration_ms", total_ms)
        pipe.ltrim("agent:stats:duration_ms", 0, 199)
        pipe.lpush("agent:stats:cost_cny", cost_cny)
        pipe.ltrim("agent:stats:cost_cny", 0, 199)
        if fallback:
            pipe.incr("agent:stats:fallback_count")
        pipe.execute()
    except Exception as e:  # noqa: BLE001
        logger.warning("[alert] record_message_end 失败: {}", e)
    # 触发规则
    _check_and_alert()


def record_tool_result(status: str):
    if _redis is None:
        return
    try:
        pipe = _redis.pipeline()
        pipe.lpush("agent:stats:status", status)
        pipe.ltrim("agent:stats:status", 0, 199)
        pipe.execute()
    except Exception:
        pass


# ─────────────────────── 判断 + 发送 ───────────────────────
def _cooldown_key(name: str) -> str:
    return f"agent:alert:cooldown:{name}"


def _try_lock(alert_name: str) -> bool:
    if _redis is None:
        return False
    try:
        return bool(_redis.set(_cooldown_key(alert_name), "1",
                                 ex=_COOLDOWN_SEC, nx=True))
    except Exception:
        return False


def _check_and_alert():
    if not _WEBHOOK or _redis is None:
        return
    try:
        # 1. 错误率
        statuses = _redis.lrange("agent:stats:status", 0, 99)
        if len(statuses) >= 20:
            err_rate = sum(1 for s in statuses if s == "error") / len(statuses)
            if err_rate > 0.05 and _try_lock("error_rate"):
                _send(f"⚠ Hunter Agent Chat V2 错误率超阈值\n"
                       f"最近 {len(statuses)} 次工具调用错误率 = {err_rate*100:.1f}% (阈值 5%)")
        # 2. P95 延迟
        durations = _redis.lrange("agent:stats:duration_ms", 0, 99)
        if len(durations) >= 20:
            vals = sorted(int(x) for x in durations)
            p95 = vals[int(len(vals) * 0.95)]
            if p95 > 20000 and _try_lock("p95_latency"):
                _send(f"⚠ Hunter Agent Chat V2 P95 延迟超阈值\n"
                       f"最近 {len(vals)} 次会话 P95 = {p95/1000:.1f}s (阈值 20s)")
        # 3. 单会话成本
        costs = _redis.lrange("agent:stats:cost_cny", 0, 19)
        if costs:
            hi = max(float(x) for x in costs)
            if hi > 0.15 and _try_lock("cost_high"):
                _send(f"⚠ Hunter Agent Chat V2 单会话成本高\n"
                       f"最近 20 次最高 = ¥{hi:.3f} (阈值 ¥0.15)")
    except Exception as e:  # noqa: BLE001
        logger.warning("[alert] _check_and_alert 异常: {}", e)


def _send(text: str) -> None:
    try:
        with httpx.Client(timeout=5) as client:
            r = client.post(_WEBHOOK, json={"msg_type": "text",
                                              "content": {"text": text}})
        logger.info("[alert] webhook sent status={}", r.status_code)
    except Exception as e:  # noqa: BLE001
        logger.warning("[alert] send 失败: {}", e)
