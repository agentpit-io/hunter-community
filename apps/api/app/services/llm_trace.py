"""轻量、线程安全的大模型调用跟踪器（v2.2.1）。

职责：
1. 记录每次大模型调用的元数据（上海时间 ts、调用方 caller、脱敏网关 base_url、
   请求模型 req_model、实际响应模型 resp_model、状态码 status、耗时 latency_ms、
   输入/输出 token 数、错误摘要 error）；
2. 绝不记录任何 api_key 或用户对话隐私正文；
3. 同时输出到标准容器日志（`[llm-trace]`）与本地 JSONL 文件（默认 `/tmp/hunter-llm-trace.jsonl`，
   自动裁剪保留最近 500 条），供设置页查看与一键下载 `.log` 诊断包。
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
import json
import os
import threading
from urllib.parse import urlsplit, urlunsplit

from loguru import logger

from app.services import market_time

_MAX_MEMORY = 200
_MAX_FILE_LINES = 500
_LOCK = threading.Lock()
_BUFFER: deque[dict] = deque(maxlen=_MAX_MEMORY)


def _trace_path() -> str:
    return (os.getenv("HUNTER_LLM_TRACE_PATH") or "/tmp/hunter-llm-trace.jsonl").strip()


def _now_shanghai_str() -> str:
    tz = market_time.market_tz("CN_A")
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")


def sanitize_url(url: str) -> str:
    """脱敏 URL：去掉 userinfo 与 query，防止 URL 中夹带 key 或 token。"""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path.rstrip("/"), "", ""))
    except Exception:  # noqa: BLE001
        return raw.split("?")[0].rstrip("/")


def _clean_str(val: object, default: str = "") -> str:
    if isinstance(val, str):
        return val.strip()
    return default


def _clean_int(val: object, default: int = 0) -> int:
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return int(val)
    try:
        return int(str(val))
    except Exception:  # noqa: BLE001
        return default


def record_call(
    *,
    caller: str,
    base_url: str,
    req_model: str,
    resp_model: str = "",
    status: int = 200,
    latency_ms: int = 0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    error: str = "",
    extra: dict | None = None,
) -> dict:
    """记录一条大模型调用跟踪日志。"""
    entry: dict = {
        "ts": _now_shanghai_str(),
        "caller": _clean_str(caller, "unknown") or "unknown",
        "base_url": sanitize_url(_clean_str(base_url)),
        "req_model": _clean_str(req_model),
        "resp_model": _clean_str(resp_model),
        "status": _clean_int(status, 0),
        "latency_ms": _clean_int(latency_ms, 0),
        "tokens_in": _clean_int(tokens_in, 0),
        "tokens_out": _clean_int(tokens_out, 0),
        "error": _clean_str(error)[:300],
    }
    if extra and isinstance(extra, dict):
        safe_extra = {
            str(k): (v if isinstance(v, (str, int, float, bool)) or v is None else str(v))
            for k, v in extra.items()
            if "key" not in str(k).lower() and "secret" not in str(k).lower() and "token" not in str(k).lower()
        }
        if safe_extra:
            entry["extra"] = safe_extra

    logger.info(
        "[llm-trace] caller={} req_model={} resp_model={} status={} latency_ms={}ms tokens={}/{} err={}",
        entry["caller"],
        entry["req_model"] or "-",
        entry["resp_model"] or "-",
        entry["status"],
        entry["latency_ms"],
        entry["tokens_in"],
        entry["tokens_out"],
        entry["error"] or "-",
    )

    path = _trace_path()
    with _LOCK:
        _BUFFER.append(entry)
        try:
            lines: list[str] = []
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    lines = [ln for ln in f.read().splitlines() if ln.strip()]
            lines.append(json.dumps(entry, ensure_ascii=False, default=str))
            if len(lines) > _MAX_FILE_LINES:
                lines = lines[-_MAX_FILE_LINES:]
            os.makedirs(os.path.dirname(path) or "/tmp", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.debug("[llm-trace] 写跟踪文件失败(不影响主流程): {}", exc)

    return entry


def recent_calls(limit: int = 100) -> list[dict]:
    """合并读取磁盘 JSONL 与内存环形缓冲，按时间倒序返回最近调用记录。"""
    cap = max(1, min(int(limit or 100), _MAX_FILE_LINES))
    path = _trace_path()
    disk_items: list[dict] = []
    with _LOCK:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for ln in f:
                        ln = ln.strip()
                        if not ln:
                            continue
                        try:
                            obj = json.loads(ln)
                            if isinstance(obj, dict):
                                disk_items.append(obj)
                        except Exception:  # noqa: BLE001
                            continue
            except OSError:
                pass
        mem_items = list(_BUFFER)

    merged = disk_items if disk_items else mem_items
    merged.reverse()
    return merged[:cap]
