#!/usr/bin/env python3
"""HCA hook 常驻服务（I2）—— 把 hook 的固定开销从「每次起一个 python」降到一次连接。

## 为什么

上游每触发一个 hook 事件就 `spawn` 一次 `command`（`cc_hooks.rs`）。本发行版的
hook 是 python 脚本，而**每一次工具调用都要跑两个**（PreToolUse 的 guard +
PostToolUse 的 audit）。容器里实测：python 空转 158 ms、guard 一次 1 236 ms、
audit 374 ms —— 一道要 22 次工具调用的题，光 hook 就是 35 秒。

这里把五个 hook 预加载进一个常驻进程，`.sh` 包装脚本改成一个 **bash 客户端**
（bash 的 `/dev/tcp`，不再起 python）。判定逻辑一个字没抄、没改 ——
服务端直接调同一份 `guard.py` / `audit.py` / … 的 `main()`。

## 边界

* 只听 **127.0.0.1**（容器内回环）。容器里的进程本来就能直接跑 guard.py，
  所以这里没有新增任何攻击面；**绝不能绑 0.0.0.0**。
* **连不上就退回原路**：客户端 fallback 成 `python3 guard.py`，判定结果一致。
  guard 是安全边界 —— 宁可慢，不可漏。
* 一次请求处理不了（模块抛异常 / SystemExit）时，返回空输出 + 退出码 1，
  客户端据此退回原路重跑一遍，**不会把「服务坏了」变成「放行」**。

## 协议

    客户端 → 服务端：  "<hook 名>\\n" + <payload JSON>
    服务端 → 客户端：  该 hook 的 stdout 原文，然后关连接

payload 用 `json.JSONDecoder.raw_decode` 增量解析，**读到一个完整 JSON 就开工**，
不依赖客户端半关连接（bash 做不到半关）。
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import socket
import socketserver
import sys
import threading
import traceback

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
HOST = "127.0.0.1"                       # 见文件头：绝不能改成 0.0.0.0
PORT = int(os.environ.get("HCA_HOOKD_PORT", "13458"))
MAX_PAYLOAD = 4 * 1024 * 1024
HOOKS = {"guard": "guard.py", "audit": "audit.py", "context": "context.py",
         "lang": "lang.py", "budget": "budget.py"}

_mods: dict = {}
_lock = threading.Lock()          # 保护模块加载
_run_lock = threading.Lock()      # 保护 sys.stdin/stdout 的换手（见 run_hook）


def load(name: str):
    """按需加载一个 hook 模块，加载过就复用（省掉每次的 import 开销）。"""
    with _lock:
        if name in _mods:
            return _mods[name]
        path = os.path.join(HOOKS_DIR, HOOKS[name])
        spec = importlib.util.spec_from_file_location(f"hca_hook_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        _mods[name] = mod
        return mod


def run_hook(name: str, payload: str) -> str:
    """跑一个 hook，返回它写到 stdout 的东西。异常一律往上抛给调用方。

    ⚠️ **整段串行**。hook 脚本是按「自己独占一个进程」写的，用 `sys.stdin` /
    `sys.stdout` 收发 —— 而这两个是**全局**的。并发跑两个请求会互相把对方的
    stdout 抢走：用例 `test_并发请求互不串扰` 第一次跑就抓到了（一个请求拿回
    半截 JSON）。UserPromptSubmit 的几条 hook 上游本来就是并发触发的
    （cc_hooks.rs:636），所以这不是理论问题。
    锁的代价可以忽略：进程内跑一次 guard 是毫秒级，真正贵的是原来那次 python 启动。
    """
    mod = load(name)
    with _run_lock:
        old_in, old_out = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = io.StringIO(payload), io.StringIO()
        try:
            try:
                mod.main()
            except SystemExit as e:      # 脚本形态里的 sys.exit(main())
                if e.code not in (0, None):
                    raise RuntimeError(f"{name} 以退出码 {e.code} 结束")
            return sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = old_in, old_out


class Handler(socketserver.StreamRequestHandler):
    timeout = 60

    def handle(self):
        try:
            head = self.rfile.readline().decode("utf-8", "replace").strip()
            if head not in HOOKS:
                self.wfile.write(b"")
                return
            dec, buf = json.JSONDecoder(), ""
            while True:
                chunk = self.rfile.read1(65536)
                if not chunk:
                    break
                buf += chunk.decode("utf-8", "replace")
                if len(buf) > MAX_PAYLOAD:
                    raise ValueError("payload 太大")
                try:
                    dec.raw_decode(buf.lstrip())
                    break                  # 读到一个完整 JSON 就够了
                except ValueError:
                    continue
            out = run_hook(head, buf)
            # 状态行必须有：**空输出是 guard 的合法结果**（verdict=None 就是放行，
            # 什么都不打印）。没有状态行的话客户端分不出「放行」和「服务坏了」，
            # 只能把每一次放行都退回去重跑 python，这一层就白做了。
            self.wfile.write(b"OK\n" + out.encode("utf-8"))
        except Exception:                  # noqa: BLE001
            # 不输出任何内容 —— 客户端看到空回应就退回「自己起 python 跑一遍」。
            # **绝不能在这里编一个「放行」的回应出来。**
            print(f"[hookd] 处理失败：\n{traceback.format_exc()}", file=sys.stderr, flush=True)
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    for name in HOOKS:
        path = os.path.join(HOOKS_DIR, HOOKS[name])
        if not os.path.isfile(path):
            print(f"[hookd] ⚠ 没有 {path}，{name} 将走客户端的退回路径",
                  file=sys.stderr, flush=True)
            continue
        try:
            load(name)                     # 预热：启动时就把 import 的账付掉
        except Exception as e:             # noqa: BLE001
            print(f"[hookd] ⚠ 预加载 {name} 失败（走退回路径）：{type(e).__name__}: {e}",
                  file=sys.stderr, flush=True)
    with Server((HOST, PORT), Handler) as srv:
        print(f"[hookd] 已就绪 {HOST}:{PORT}，预加载 {sorted(_mods)}", flush=True)
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
