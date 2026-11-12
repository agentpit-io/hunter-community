#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L07 · 本机假接收端 —— 给发布适配器当靶子（**只用于测试 / 故障注入，不进生产**）。

用途（`plan/L07.md` §三.6 / §四）：

  · 证明「Webhook 发出去了」—— 接收端记下收到的 JSON，落库回执就是凭据；
  · **故障注入第 7 项**「内容已发布，但返回超时」—— `/deliver_then_hang` 先把请求体
    **完整收下并记账**，再故意拖到客户端超时。发送方看到超时 → 判 `UNKNOWN`；
    接收端日志证明**其实已经收到** → 回查 `/status` 就能落定 `SUCCESS`。

端点：
  · `POST /deliver`              —— 收下并立刻 200（正常送达）；
  · `POST /deliver_then_hang`    —— **收下、记账，再挂起 `--hang` 秒**（制造「已送达但超时」）；
  · `GET  /status?receipt=<KEY>` —— 回 `{"received": bool, "count": n}`（发布方核实用）；
  · `GET  /dump`                 —— 把收到的全部记录吐出来（贴证据用）。

命令行：
    python scripts/fake_publish_receiver.py --port 8799 --hang 3
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Store:
    """收到的请求记录（内存，进程内共享）。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.by_receipt: dict[str, list[dict]] = {}
        self.order: list[dict] = []

    def add(self, receipt_key: str, path: str, body: bytes, headers: dict) -> int:
        rec = {"receipt_key": receipt_key, "path": path, "nbytes": len(body),
               "at": time.time(), "headers": headers,
               "body_head": body[:200].decode("utf-8", "replace")}
        with self.lock:
            self.by_receipt.setdefault(receipt_key, []).append(rec)
            self.order.append(rec)
            return len(self.by_receipt[receipt_key])

    def received(self, receipt_key: str) -> bool:
        with self.lock:
            return bool(self.by_receipt.get(receipt_key))


class FakeReceiver:
    """线程内起一个假接收端。测试里用 `port=0` 拿随机端口，读完 `self.url`。"""

    def __init__(self, port: int = 0, hang_s: float = 3.0) -> None:
        self.store = _Store()
        self.hang_s = hang_s
        store, hang = self.store, hang_s

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):        # 静音，别把测试输出冲乱
                pass

            def _read_body(self) -> bytes:
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def _json(self, obj: dict, code: int = 200) -> None:
                data = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Receipt-Id", "recv-" + str(id(obj)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self) -> None:  # noqa: N802
                body = self._read_body()
                try:
                    key = (json.loads(body or b"{}") or {}).get("receipt_key") or "-"
                except Exception:
                    key = "-"
                n = store.add(key, self.path, body, dict(self.headers))
                if self.path.startswith("/deliver_then_hang"):
                    # **先记账，再挂起** —— 发送方超时了，但内容确实到了。
                    time.sleep(hang)
                self._json({"ok": True, "receipt_id": f"recv-{key[:12]}-{n}", "count": n})

            def do_GET(self) -> None:  # noqa: N802
                if self.path.startswith("/status"):
                    key = "-"
                    if "?" in self.path:
                        for kv in self.path.split("?", 1)[1].split("&"):
                            if kv.startswith("receipt="):
                                key = kv.split("=", 1)[1]
                    got = store.received(key)
                    with store.lock:
                        cnt = len(store.by_receipt.get(key, []))
                    self._json({"received": got, "count": cnt})
                    return
                if self.path.startswith("/dump"):
                    with store.lock:
                        self._json({"order": store.order})
                    return
                self._json({"error": "not found"}, 404)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread: threading.Thread | None = None

    def start(self) -> "FakeReceiver":
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self) -> "FakeReceiver":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def main() -> None:
    ap = argparse.ArgumentParser(description="L07 假发布接收端")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--hang", type=float, default=3.0, help="/deliver_then_hang 挂起秒数")
    args = ap.parse_args()
    recv = FakeReceiver(port=args.port, hang_s=args.hang).start()
    print(f"假接收端已起：{recv.url}  （/deliver · /deliver_then_hang · /status · /dump）", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        recv.stop()


if __name__ == "__main__":
    main()
