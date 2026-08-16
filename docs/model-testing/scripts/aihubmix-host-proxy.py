#!/usr/bin/env python3
"""macOS host 侧的 aihubmix HTTP 代理 · 绕开 alpine 容器的 TLS 指纹问题
容器里 shim/opencode → http://host.docker.internal:3998/v1/...  → 本代理 → https://aihubmix.com/v1/...

macOS 系统 curl 的 TLS 指纹 aihubmix 认 · 容器 Alpine curl 不认。让 host 帮容器完成 TLS。
"""
import http.server, socketserver, urllib.request, urllib.error, sys, ssl

UPSTREAM = "https://aihubmix.com/v1"
PORT = 3998

class H(http.server.BaseHTTPRequestHandler):
    def _fwd(self, body):
        # 拼上游 URL: /v1/... → https://aihubmix.com/v1/...
        path = self.path
        if path.startswith("/v1"):
            path = path[len("/v1"):]
        url = UPSTREAM + path
        try:
            req = urllib.request.Request(url, data=body, method=self.command)
            for k in ("Authorization", "Content-Type", "Accept"):
                if k in self.headers:
                    req.add_header(k, self.headers[k])
            if body is not None:
                req.add_header("Content-Length", str(len(body)))
            # macOS Python 的 TLS 栈通过 · 不需要额外 tweak
            with urllib.request.urlopen(req, timeout=600) as r:
                self.send_response(r.status)
                for k, v in r.headers.items():
                    if k.lower() in ("content-length", "connection", "transfer-encoding"):
                        continue
                    self.send_header(k, v)
                self.end_headers()
                while True:
                    chunk = r.read(4096)
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk); self.wfile.flush()
                    except Exception:
                        return
        except urllib.error.HTTPError as e:
            data = e.read()
            print(f"[proxy] upstream {e.code}: {data[:200]}", flush=True)
            self.send_response(e.code); self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            print(f"[proxy] ERR {type(e).__name__}: {e}", flush=True)
            self.send_response(502); self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self._fwd(self.rfile.read(n) if n else b"")

    def do_GET(self):
        if self.path in ("/health", "/healthz"):
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}'); return
        self._fwd(None)

    def log_message(self, *a): return

class ThreadedTCPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

print(f"[host-proxy] listening 0.0.0.0:{PORT}/v1 → {UPSTREAM}", flush=True)
ThreadedTCPServer(("0.0.0.0", PORT), H).serve_forever()
