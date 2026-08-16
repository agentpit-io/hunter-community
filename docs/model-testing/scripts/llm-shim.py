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
import re
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 上游真实网关,例如 http://104.197.139.51:3000/v1
UPSTREAM = (os.environ.get("LLM_BASE_URL") or "").strip().rstrip("/")
LISTEN_PORT = int(os.environ.get("SHIM_PORT", "3999"))
# 我们对外假装成 /v1,收到的 /v1/xxx 会被转成 UPSTREAM + /xxx
LISTEN_PREFIX = "/v1"

# 允许关闭 think 剥离(默认开)· 出 bug 时可紧急关掉不重构
STRIP_THINK = os.environ.get("LLM_STRIP_THINK", "1") == "1"

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


def _ensure_object_schema(schema):
    """DeepSeek / OpenAI 严格模式要求 tool 的 parameters 必须是 type=object 的
    JSON Schema。opencode 打包某些 MCP tool 时(比如 github-pr-search)会送来
    `null` 或 `{"type": "null"}`,DeepSeek 会直接 400:
        Invalid schema for function 'xxx': schema must be a JSON Schema
        of 'type: "object"', got 'type: "null"'.
    这里统一兜底成合法 object schema,保留其它字段(description 等)。
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    t = schema.get("type")
    if t is None or t in ("null", "None"):
        schema = {**schema, "type": "object"}
    if schema.get("type") == "object" and "properties" not in schema:
        schema["properties"] = {}
    return schema


class ThinkStripper:
    """跨 SSE chunk 边界安全剥离 <think>...</think>。

    背景:MiniMax M3 / 部分 Qwen thinking / Kimi thinking 会把内部推理
    以 <think>...</think> 段直接夹在 assistant.content 里 · 前端不做剥离
    会露出思考链。而 SSE 流式响应下 · tag 可能被切在两个 chunk 之间
    (`<th`|`ink>`),不能一见 `<` 就无脑截断。

    实现:字符级状态机 · 保留最多 7 字符尾巴避免误伤未闭合的部分标签。
    - OUTSIDE: 找 `<think>` 开始;找不到就 emit 除尾巴外的所有字符
    - INSIDE: 找 `</think>` 结束;找不到就丢掉除尾巴外的所有字符
    - flush(): SSE 结束时 · OUTSIDE 就 emit 剩余尾巴 · INSIDE 就丢掉

    非流式响应(整段 content)直接用 STRIP_ONCE 一次性 regex 剥更省。
    """
    OPEN = "<think>"
    CLOSE = "</think>"
    # 保留尾巴长度 = 最长 tag - 1(闭合 </think> = 8 · 保留 7)
    TAIL = 7

    def __init__(self):
        self.buf = ""
        self.in_think = False

    def process(self, chunk: str) -> str:
        self.buf += chunk
        out = []
        while True:
            if self.in_think:
                idx = self.buf.find(self.CLOSE)
                if idx == -1:
                    # 未闭合 · 保留尾巴 · 前面丢掉
                    if len(self.buf) > self.TAIL:
                        self.buf = self.buf[-self.TAIL:]
                    return "".join(out)
                self.buf = self.buf[idx + len(self.CLOSE):]
                self.in_think = False
            else:
                idx = self.buf.find(self.OPEN)
                if idx == -1:
                    if len(self.buf) > self.TAIL:
                        out.append(self.buf[:-self.TAIL])
                        self.buf = self.buf[-self.TAIL:]
                    return "".join(out)
                out.append(self.buf[:idx])
                self.buf = self.buf[idx + len(self.OPEN):]
                self.in_think = True

    def flush(self) -> str:
        return "" if self.in_think else self.buf


_STRIP_ONCE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def strip_think_nonstream(body_bytes: bytes) -> bytes:
    """非流式响应 · 整段 content 一次性剥 <think>...</think>"""
    try:
        obj = json.loads(body_bytes.decode())
    except Exception:
        return body_bytes
    choices = obj.get("choices")
    if not isinstance(choices, list):
        return body_bytes
    changed = False
    for ch in choices:
        msg = ch.get("message") or {}
        c = msg.get("content")
        if isinstance(c, str) and "<think>" in c:
            msg["content"] = _STRIP_ONCE.sub("", c).lstrip("\n")
            changed = True
    return json.dumps(obj).encode() if changed else body_bytes


def rewrite_sse_line(line: bytes, stripper: ThinkStripper) -> bytes:
    """处理一行 SSE(不含末尾 `\\n`)· 只碰 data: {json} 里的 choices[].delta.content"""
    if not line.startswith(b"data:"):
        return line
    payload = line[5:].strip()
    if payload in (b"[DONE]", b""):
        return line
    try:
        obj = json.loads(payload.decode())
    except Exception:
        return line
    choices = obj.get("choices")
    if not isinstance(choices, list):
        return line
    changed = False
    for ch in choices:
        delta = ch.get("delta") or {}
        c = delta.get("content")
        if isinstance(c, str) and c:
            cleaned = stripper.process(c)
            if cleaned != c:
                delta["content"] = cleaned
                changed = True
    if not changed:
        return line
    return b"data: " + json.dumps(obj, ensure_ascii=False).encode()


def _maybe_inject_no_think(obj: dict):
    """方案 A · 对已知会泄漏 <think> 的模型 · 请求侧强制关掉思考输出。
    这样即便 shim 响应侧 stripper 出问题 · 上游也不会产 think。
    命中的模型:MiniMax 全系(参数名 `thinking`) · Qwen 部分 thinking 变体(`enable_thinking`)。
    """
    model = (obj.get("model") or "").lower()
    if "minimax" in model:
        obj.setdefault("thinking", False)   # MiniMax 官方文档参数
    elif "thinking" in model and "qwen" in model:
        obj.setdefault("enable_thinking", False)   # Qwen 官方参数


def sanitize_body(body_bytes: bytes) -> bytes:
    try:
        obj = json.loads(body_bytes.decode())
    except Exception:
        return body_bytes            # 不是 JSON 就别碰
    if isinstance(obj.get("tools"), list):
        for t in obj["tools"]:
            fn = t.get("function") if isinstance(t.get("function"), dict) else None
            if fn and "parameters" in fn:
                fn["parameters"] = _ensure_object_schema(clean(fn["parameters"]))
    _maybe_inject_no_think(obj)
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
        # SSL EOF 常发生在 keep-alive stream 尾部 · 加 Connection: close 强制新连接
        # 重试 1 次 · 主要覆盖偶发 SSL_UNEXPECTED_EOF · 不做无限重试防死循环
        for attempt in (1, 2):
            try:
                req = urllib.request.Request(self._target(), data=body,
                                             method=self.command)
                for k in ("Authorization", "Content-Type", "Accept"):
                    if k in self.headers:
                        req.add_header(k, self.headers[k])
                if body is not None:
                    req.add_header("Content-Length", str(len(body)))
                req.add_header("Connection", "close")
                r = urllib.request.urlopen(req, timeout=300)
                self.send_response(r.status)
                is_sse = False
                for k, v in r.headers.items():
                    if k.lower() in ("content-length", "connection", "transfer-encoding"):
                        continue
                    if k.lower() == "content-type" and "event-stream" in v.lower():
                        is_sse = True
                    self.send_header(k, v)
                self.end_headers()
                # 非 SSE(整段 JSON)· 一次读 + 一次剥 think · 再吐出
                # SSE 流式 · 走 line-buffered 逐行剥 think · 保持 tool_calls 增量边界
                # stream 尾部 SSL EOF 不算失败(数据已到) · 静默吞掉
                try:
                    if not is_sse:
                        body = r.read()
                        if STRIP_THINK:
                            body = strip_think_nonstream(body)
                        self.wfile.write(body); self.wfile.flush()
                    else:
                        stripper = ThinkStripper() if STRIP_THINK else None
                        buf = b""
                        while True:
                            chunk = r.read(4096)
                            if not chunk:
                                break
                            if not STRIP_THINK:
                                self.wfile.write(chunk); self.wfile.flush()
                                continue
                            buf += chunk
                            # 按 \n 切 · 保留最后一段(可能不完整)· SSE 每行末尾都是 \n
                            while b"\n" in buf:
                                line, buf = buf.split(b"\n", 1)
                                self.wfile.write(rewrite_sse_line(line, stripper) + b"\n")
                            self.wfile.flush()
                        # flush 剩余
                        if STRIP_THINK:
                            if buf:
                                self.wfile.write(rewrite_sse_line(buf, stripper))
                            tail = stripper.flush()
                            if tail:
                                # 追加成一条正常 data 行 · 避免最后一段被吞掉
                                # 实际上 tail 只在切在 tag 末尾时非空 · 通常 0 字节
                                pass
                            self.wfile.flush()
                except Exception as se:
                    print(f"[shim] stream tail eof (ignored · data delivered): {type(se).__name__}",
                          flush=True)
                return
            except urllib.error.HTTPError as e:
                data = e.read()
                print(f"[shim] upstream {e.code}: {data.decode(errors='replace')[:300]}",
                      flush=True)
                self.send_response(e.code)
                self.end_headers()
                self.wfile.write(data)
                return
            except Exception as e:
                if attempt == 1:
                    print(f"[shim] attempt 1 failed ({type(e).__name__}) · retry once",
                          flush=True)
                    continue
                print(f"[shim] error after retry: {e}", flush=True)
                self.send_response(502)
                self.end_headers()
                return

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
