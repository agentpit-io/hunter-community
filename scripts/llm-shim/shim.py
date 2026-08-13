#!/usr/bin/env python3
"""LLM schema shim · 把 opencode 的 tool schema 洗成 Gemini 收得下的形状。

为什么需要它:opencode 送出的 function parameters 是完整 JSON Schema,带
`$schema` / `additionalProperties` / `anyOf` 之类关键字。Gemini(经 OneAPI 之类
的 OpenAI 兼容网关)只认 OpenAPI 子集,收到就整个请求报错:

    Invalid JSON payload received. Unknown name "$schema" at
    'tools[0].function_declarations[0].parameters': Cannot find field.

表现是"聊天一发就失败",但错误藏在 assistant 消息的 error 字段里,前端只看到没回复。
镜像里的 hunter-guard 插件做了一部分清洗,但不覆盖 `$schema`,所以还要这一层。

只碰 `tools[].function.parameters`,其余原样转发;SSE 分块透传,tool_calls 的
增量累积不受影响。

移植自生产实例的 /opt/opencode-conf/oneapi_shim.py,改动:
  · 上游地址从环境变量读,不再写死
  · 支持 GET(opencode 会拉 /v1/models)
  · 路径按 base_url 的前缀重写,兼容非 /v1 的网关
"""
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 上游真实网关,例如 http://104.197.139.51:3000/v1
UPSTREAM = (os.environ.get("LLM_BASE_URL") or "").strip().rstrip("/")
LISTEN_PORT = int(os.environ.get("SHIM_PORT", "3999"))
# 我们对外假装成 /v1,收到的 /v1/xxx 会被转成 UPSTREAM + /xxx
LISTEN_PREFIX = "/v1"

# Gemini 不认的 JSON Schema 关键字。删掉不影响语义 —— 它们只是更严格的约束,
# 而工具调用的正确性由 tool 自身的参数校验兜底。
STRIP = {
    "additionalProperties", "exclusiveMinimum", "exclusiveMaximum",
    "const", "patternProperties", "dependentRequired", "dependentSchemas",
    "if", "then", "else", "not", "minContains", "maxContains",
    "unevaluatedItems", "unevaluatedProperties", "propertyNames",
    "maxProperties", "minProperties",
    "$schema", "$id", "$defs", "$ref",
}


def clean(node):
    if isinstance(node, dict):
        # allOf/oneOf/anyOf 一律塌缩成第一个分支 —— Gemini 不支持组合子句,
        # 保留第一支比整个丢掉更接近原意
        for k in ("allOf", "oneOf", "anyOf"):
            if k in node and isinstance(node[k], list) and node[k]:
                first = clean(node[k][0]) or {}
                sib = {kk: clean(vv) for kk, vv in node.items()
                       if kk not in ("allOf", "oneOf", "anyOf")}
                sib.update(first)
                return clean(sib)
        out = {}
        for k, v in node.items():
            if k in STRIP:
                continue
            out[k] = clean(v)
        # Gemini 要求 array 必须声明 items
        if out.get("type") == "array" and "items" not in out:
            out["items"] = {"type": "string"}
        return out
    if isinstance(node, list):
        return [clean(x) for x in node]
    return node


def sanitize_body(body_bytes: bytes) -> bytes:
    try:
        obj = json.loads(body_bytes.decode())
    except Exception:
        return body_bytes            # 不是 JSON 就别碰
    if isinstance(obj.get("tools"), list):
        for t in obj["tools"]:
            fn = t.get("function") if isinstance(t.get("function"), dict) else None
            if fn and "parameters" in fn:
                fn["parameters"] = clean(fn["parameters"]) or {
                    "type": "object", "properties": {}}
    return json.dumps(obj).encode()


class Handler(BaseHTTPRequestHandler):
    def _target(self) -> str:
        path = self.path
        if path.startswith(LISTEN_PREFIX):
            path = path[len(LISTEN_PREFIX):]
        return UPSTREAM + path

    def _proxy(self, body: bytes | None):
        if body is not None and self.path.endswith("/chat/completions"):
            body = sanitize_body(body)
        try:
            req = urllib.request.Request(self._target(), data=body,
                                         method=self.command)
            for k in ("Authorization", "Content-Type", "Accept"):
                if k in self.headers:
                    req.add_header(k, self.headers[k])
            if body is not None:
                req.add_header("Content-Length", str(len(body)))
            r = urllib.request.urlopen(req, timeout=300)
            self.send_response(r.status)
            for k, v in r.headers.items():
                if k.lower() in ("content-length", "connection", "transfer-encoding"):
                    continue
                self.send_header(k, v)
            self.end_headers()
            # 4KB 流式转发 · 保持 SSE 分块边界,否则 tool_calls 增量拼不起来
            while True:
                chunk = r.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except urllib.error.HTTPError as e:
            data = e.read()
            print(f"[shim] upstream {e.code}: {data.decode(errors='replace')[:300]}",
                  flush=True)
            self.send_response(e.code)
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            print(f"[shim] error: {e}", flush=True)
            self.send_response(502)
            self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self._proxy(self.rfile.read(n) if n else b"")

    def do_GET(self):
        # opencode 启动时会拉 /v1/models 探活
        if self.path in ("/health", "/healthz"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return
        self._proxy(None)

    def log_message(self, *args):
        return          # 默认每请求一行访问日志,太吵


if __name__ == "__main__":
    if not UPSTREAM:
        print("[shim] LLM_BASE_URL 未设置,无法转发", file=sys.stderr, flush=True)
        sys.exit(1)
    print(f"[shim] listening 0.0.0.0:{LISTEN_PORT}{LISTEN_PREFIX} -> {UPSTREAM}",
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler).serve_forever()
