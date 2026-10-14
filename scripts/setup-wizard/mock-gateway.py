#!/usr/bin/env python3
"""截图用的本地模拟网关(OpenAI 兼容)。

**只用来出文档截图** —— 这样公开仓库里的截图不会带演示站网关的真实地址。
成果文档里的三项检测耗时是对**真实网关**测出来的,见测试用例表。
行为刻意和真实的 gemini 通道一致:带 $schema 的工具定义先拒,清洗后放行。
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

MODELS = ["deepseek-v4-pro", "qwen3.8-max", "claude-sonnet-5",
          "gemini-4-argon", "gemini-3.8-flash", "gpt-5.6-sol"]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            return self._send(200, {"data": [{"id": m} for m in MODELS]})
        self._send(404, {})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        b = json.loads(self.rfile.read(n) or b"{}")
        if b.get("model") not in MODELS:
            return self._send(404, {"error": {"message": f"model_not_found: {b.get('model')}"}})
        if b.get("tools"):
            pr = b["tools"][0]["function"]["parameters"]
            if "$schema" in pr or "additionalProperties" in pr:
                return self._send(400, {"error": {"message":
                    'Invalid JSON payload received. Unknown name "$schema"'}})
            return self._send(200, {"choices": [{"message": {"tool_calls": [
                {"function": {"name": "hunter_setup_probe_quote",
                              "arguments": '{"code":"600519"}'}}]}}]})
        return self._send(200, {"choices": [{"message": {"content": "收到"}}]})


HTTPServer(("0.0.0.0", 8899), H).serve_forever()
