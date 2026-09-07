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

    实现:字符级状态机 · **只在尾部真的像半个 tag 时**才扣留那几个字符。
    - OUTSIDE: 找 `<think>` 开始;找不到就 emit 除"疑似半个 tag"外的所有字符
    - INSIDE: 找 `</think>` 结束;找不到就丢掉除"疑似半个 tag"外的所有字符
    - flush(): SSE 结束时 · OUTSIDE 就 emit 剩余尾巴 · INSIDE 就丢掉

    ⚠️ 2026-09-07 事故:原实现**无条件**保留最后 7 个字符(`buf[:-TAIL]`),
    于是整条流稳定滞后 7 字符,全指望结束时 flush() 补回 —— 而下面
    `_proxy()` 里那个 flush 分支写的是 `pass`。结果:**每条流式回答的
    末尾都被吞掉 7 个字符**,表现是回答在句子中间断掉(实测断在
    「…高分红/高壁垒资」),没有任何报错。gemini 全系走 shim,即全量命中。
    现在改成按需扣留:正文里没有 `<` 时 keep=0,一个字都不滞留,
    不再依赖 flush 兜底(flush 仍然写出去,见 _proxy,双保险)。

    非流式响应(整段 content)直接用 STRIP_ONCE 一次性 regex 剥更省。
    """
    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self):
        self.buf = ""
        self.in_think = False
        self._template = None

    @staticmethod
    def _partial_tag_len(s: str, tag: str) -> int:
        """s 的末尾有多少个字符可能是 `tag` 被切断的前半截。没有返回 0。

        例:s 以 `<thi` 结尾 → 4(要等下一个 chunk 才知道是不是 `<think>`);
            s 以 `资产?` 结尾 → 0(压根不像 tag,全部可以放行)。
        """
        for k in range(min(len(tag) - 1, len(s)), 0, -1):
            if tag.startswith(s[-k:]):
                return k
        return 0

    def process(self, chunk: str) -> str:
        self.buf += chunk
        out = []
        while True:
            if self.in_think:
                idx = self.buf.find(self.CLOSE)
                if idx == -1:
                    # 未闭合 · 只留可能是半个 </think> 的尾巴 · 前面丢掉
                    keep = self._partial_tag_len(self.buf, self.CLOSE)
                    self.buf = self.buf[len(self.buf) - keep:] if keep else ""
                    return "".join(out)
                self.buf = self.buf[idx + len(self.CLOSE):]
                self.in_think = False
            else:
                idx = self.buf.find(self.OPEN)
                if idx == -1:
                    keep = self._partial_tag_len(self.buf, self.OPEN)
                    if keep < len(self.buf):
                        out.append(self.buf[:len(self.buf) - keep])
                        self.buf = self.buf[len(self.buf) - keep:]
                    return "".join(out)
                out.append(self.buf[:idx])
                self.buf = self.buf[idx + len(self.OPEN):]
                self.in_think = True

    def flush(self) -> str:
        return "" if self.in_think else self.buf

    def remember_template(self, obj: dict):
        """记住上游 chunk 的外层字段(id/model/created/object…),
        供 tail_frame() 拼一条字段齐全、下游 SDK 一定认得的补发帧。"""
        if self._template is None:
            self._template = {k: v for k, v in obj.items() if k != "choices"}

    def tail_frame(self) -> bytes | None:
        """把 flush() 剩下的尾巴包成一条合法 SSE data 帧。

        必须包成 `data: {...}` —— 直接写裸文本的话下游 SSE 解析器会当噪音丢掉,
        等于没补。调用方还必须保证这一帧排在 `data: [DONE]` **之前**,
        [DONE] 之后的内容 OpenAI 兼容 SDK 一律不再读。
        """
        tail = self.flush()
        if not tail:
            return None
        self.buf = ""
        obj = dict(self._template or {})
        obj["choices"] = [{"index": 0, "delta": {"content": tail}, "finish_reason": None}]
        return b"data: " + json.dumps(obj, ensure_ascii=False).encode() + b"\n\n"


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
    stripper.remember_template(obj)
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
                        # `data: [DONE]` 必须延后写:补发帧要排在它前面,
                        # 否则 OpenAI 兼容 SDK 读到 [DONE] 就收工,补发被无视。
                        done_line = None
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
                                if line.strip().replace(b" ", b"") == b"data:[DONE]":
                                    done_line = line
                                    continue
                                self.wfile.write(rewrite_sse_line(line, stripper) + b"\n")
                            self.wfile.flush()
                        # 收尾:残帧 → 补发扣留的尾巴 → 最后才放行 [DONE]
                        if STRIP_THINK:
                            if buf:
                                self.wfile.write(rewrite_sse_line(buf, stripper) + b"\n")
                            # 正常情况下 stripper 按需扣留 · 这里多半是空;
                            # 只有流恰好断在半个 <think> 上才非空。**不能写 pass** ——
                            # 2026-09-07 就是这行 pass 把每条回答的末尾吞了 7 个字符。
                            frame = stripper.tail_frame()
                            if frame:
                                self.wfile.write(frame)
                                print(f"[shim] 补发被扣留的尾巴 {len(frame)}B", flush=True)
                            if done_line is not None:
                                self.wfile.write(done_line + b"\n\n")
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
