#!/usr/bin/env python3
"""HCA 审计 hook · PostToolUse / PostToolUseFailure · 追加一行 JSONL。

## 记什么（M4 全量）

| 字段 | 来源 | 拿不到时 |
|---|---|---|
| `tool` | 事件里的 `tool_name` | `null` |
| `ok` | `PostToolUse` → true，`PostToolUseFailure` → false | —— 事件名本身决定 |
| `args_keys` / `args_head` | **guard 在 PreToolUse 留的起始记录** | `null` + `args_src="—"` |
| `duration_ms` | 同上（now − 起始时刻） | `null` + `duration_src="—"` |
| `response_len` / `response_head` | 事件里的 `tool_response` | 无该字段就不写 |
| `session_id` | 事件里的 `session_id` | `null` |
| `user_id` | 工作区 `.atomcode/session-user.json` 缓存（guard 查过就有）→ 回落 `HUNTER_USER_ID` | `null` + `user_src="—"` |

**为什么参数与耗时要绕一道 guard**：上游 `cc_hooks.rs:897-903` 的 PostToolUse payload
只有 `session_id` / `hook_event_name` / `tool_name` / `tool_response` / `cwd` ——
**既没有 `tool_input`，也没有耗时，而且两个事件都不带 call_id**，
没有任何字段能把 pre 和 post 配起来。guard 已经在每次 PreToolUse 跑一遍、
手里就有参数，所以由它落一份 `.atomcode/tool-start/<key>.json`，这里来配。
guard 被关掉时这两列就是 `—`（总控红线 1：拿不到写 `—`，不猜）。

## 写入策略：本地同步落盘 + 可选异步上报

* 本地：一次 `open(..., "a")` 追加，不 fsync、不读旧内容。失败（磁盘满 / 只读挂载）
  一律吞掉 —— 审计不参与权限判定，绝不能拖垮对话。恒 `exit 0`、不输出决策。
* 轮转：文件超过 `HCA_AUDIT_MAX_MB`（默认 64）就改名成 `.1` 再重开，只留一代。
* 上报（`HCA_AUDIT_WEBHOOK`，默认不配 = 不上报）：**真正异步** —— 把这条记录写进一个
  临时文件，然后 `start_new_session=True` 派一个脱离进程组的子进程去 POST，父进程立刻退。
  网络那一跳绝不能占住 hook 的 timeout（上游 `run_command_hook` 用 `kill_on_drop`，
  超时会连子进程一起杀，所以必须脱离进程组）。

安全（总控红线 3）：只做 `json.loads`，不 eval，不把输入拼进命令行；
子进程调用用参数数组。
"""
from __future__ import annotations

import datetime
import json
import os
import sys

# ⚠️ `subprocess` / `tempfile` / `urllib.request` **故意不在这里 import** ——
# 它们只在配了 HCA_AUDIT_WEBHOOK 时才用得上，而这个 hook 每次工具调用都要跑一遍。
# 测试机实测（2 核、容器内）：python3 空转 82 ms，`import urllib.request` 498 ms，
# `import subprocess,tempfile` 258 ms。默认不上报的部署等于白付这些时间。

MAX_MB = float(os.environ.get("HCA_AUDIT_MAX_MB") or 64)
RESP_HEAD = int(os.environ.get("HCA_AUDIT_RESP_HEAD") or 200)
WEBHOOK = (os.environ.get("HCA_AUDIT_WEBHOOK") or "").strip()
WEBHOOK_TIMEOUT_S = float(os.environ.get("HCA_AUDIT_WEBHOOK_TIMEOUT_S") or 10)


def now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _start_record(workspace: str, sid: str, tool: str):
    """取 guard 在 PreToolUse 留的起始记录；取完删掉（一次调用只配一次）。"""
    if not sid or not tool:
        return None
    try:
        import hashlib
        key = hashlib.sha1(("{}\x1f{}".format(sid, tool)).encode("utf-8")).hexdigest()[:16]
        path = os.path.join(workspace, ".atomcode", "tool-start", key + ".json")
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        os.remove(path)
    except Exception:  # noqa: BLE001  没有就是没有
        return None
    return rec if isinstance(rec, dict) else None


def _user_id(workspace: str, sid: str):
    """用户标识。**只读缓存、绝不联网** —— 审计不该给每次工具调用加一跳 HTTP。

    缓存由 guard 的身份反查写（`.atomcode/session-user.json`）。
    """
    if sid:
        try:
            with open(os.path.join(workspace, ".atomcode", "session-user.json"),
                      encoding="utf-8") as f:
                rec = (json.load(f) or {}).get(sid)
            uid = (rec or {}).get("uid")
            if uid:
                return str(uid), "session-cache"
        except Exception:  # noqa: BLE001
            pass
    env_uid = (os.environ.get("HUNTER_USER_ID") or "").strip()
    if env_uid:
        return env_uid, "env"
    return None, "—"


def _rotate(log: str) -> None:
    try:
        if MAX_MB > 0 and os.path.getsize(log) > MAX_MB * 1024 * 1024:
            os.replace(log, log + ".1")
    except OSError:
        pass


def _report_async(rec: dict) -> None:
    """派一个脱离进程组的子进程去 POST。父进程不等结果。"""
    if not WEBHOOK:
        return
    try:
        import subprocess  # noqa: PLC0415  见文件头的延迟导入说明
        import tempfile
        fd, tmp = tempfile.mkstemp(prefix="hca-audit-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--post", tmp],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except Exception:  # noqa: BLE001  上报失败不影响审计与对话
        pass


def post_mode(path: str) -> int:
    """`--post <file>`：子进程里真正发 HTTP，发完删临时文件。"""
    try:
        with open(path, encoding="utf-8") as f:
            body = f.read().encode("utf-8")
    except OSError:
        return 0
    import urllib.request  # noqa: PLC0415  见文件头
    req = urllib.request.Request(
        WEBHOOK, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    token = (os.environ.get("HCA_AUDIT_WEBHOOK_TOKEN") or "").strip()
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT_S).read()
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return 0


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--post":
        return post_mode(sys.argv[2])

    log = (sys.argv[1] if len(sys.argv) > 1 else "") or ""
    raw = sys.stdin.read()
    try:
        ev = json.loads(raw)
    except Exception:  # noqa: BLE001
        ev = {"_parse_error": True, "_raw_len": len(raw)}
    if not isinstance(ev, dict):
        ev = {"_parse_error": True, "_raw_len": len(raw)}

    cwd = ev.get("cwd") or os.getcwd()
    workspace = os.path.normpath(os.environ.get("HCA_WORKSPACE") or cwd)
    if not log:
        log = os.environ.get("HCA_AUDIT_LOG") or os.path.join(workspace, ".atomcode", "audit.jsonl")

    event = ev.get("hook_event_name")
    sid = ev.get("session_id") or ""
    tool = ev.get("tool_name") or ""

    rec = {
        "ts": now_utc().isoformat(timespec="milliseconds"),
        "session_id": ev.get("session_id"),
        "event": event,
        # 失败与成功走的是两个事件（上游 after()：is_error → PostToolUseFailure）
        "ok": (None if event is None else event != "PostToolUseFailure"),
        "tool": ev.get("tool_name"),
        "cwd": cwd,
    }

    uid, uid_src = _user_id(workspace, sid)
    rec["user_id"] = uid
    rec["user_src"] = uid_src

    st = _start_record(workspace, sid, tool)
    if st:
        rec["args_keys"] = st.get("args_keys")
        rec["args_head"] = st.get("args_head")
        rec["args_src"] = "guard-start"
        began = st.get("at")
        if isinstance(began, (int, float)):
            rec["duration_ms"] = int(round((now_utc().timestamp() - began) * 1000))
            rec["duration_src"] = "guard-start(may-overlap)"
        else:
            rec["duration_ms"] = None
            rec["duration_src"] = "—"
    else:
        # 拿不到就写 —，不猜（总控红线 1）。常见原因：guard 关了 / 这次调用被 guard 拒了 /
        # 同一会话同一工具并行调用互相覆盖了起始记录。
        rec["args_keys"] = None
        rec["args_head"] = None
        rec["args_src"] = "—"
        rec["duration_ms"] = None
        rec["duration_src"] = "—"

    # tool_response 可能很大（一次 akshare 返回几十 KB），只记长度与前 N 字符
    resp = ev.get("tool_response")
    if resp is not None:
        s = resp if isinstance(resp, str) else json.dumps(resp, ensure_ascii=False)
        rec["response_len"] = len(s)
        rec["response_head"] = s[:RESP_HEAD]
    if ev.get("_parse_error"):
        rec["parse_error"] = True

    # 阶段 M4：如果是券商 MCP 调用（`mcp__broker_*`），提取信封元数据并写入 broker_audit.jsonl
    is_broker_mcp = bool(tool and tool.startswith("mcp__broker_"))
    if is_broker_mcp:
        parts = tool.split("__")
        mcp_srv = parts[1] if len(parts) > 1 else "broker_unknown"
        mcp_tool = parts[2] if len(parts) > 2 else tool
        rec["broker_mcp_server"] = mcp_srv
        rec["broker_tool_name"] = mcp_tool
        rec["broker_audit_logged"] = True
        try:
            broker_log_path = os.path.join(workspace, ".atomcode", "broker_audit.jsonl")
            os.makedirs(os.path.dirname(broker_log_path), exist_ok=True)
            with open(broker_log_path, "a", encoding="utf-8") as bf:
                bf.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            pass

    try:
        parent = os.path.dirname(log)
        if parent:
            os.makedirs(parent, exist_ok=True)
        _rotate(log)
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        # 写日志失败（磁盘满 / 只读挂载）绝不能拖垮对话
        pass

    _report_async(rec)
    return 0


if __name__ == "__main__":
    sys.exit(main())
