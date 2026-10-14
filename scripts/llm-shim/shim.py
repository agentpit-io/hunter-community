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

M1(设计方案 3.3)又加了三件事:
  · **缺 LLM_BASE_URL 不再退出** —— 配置可能存在数据库里,地址由请求头带来
  · 上游地址优先取请求头 `X-Hunter-Upstream`,并做白名单校验(check_upstream):
    只允许 http/https,拒绝内部服务名、回环、链路本地(云元数据)与内网地址,
    防止 shim 被当成访问内网的跳板。自建内网网关请显式设 LLM_SHIM_ALLOW_PRIVATE=1
  · 没配置 / 地址不让用时**立刻**回一个 OpenAI 兼容错误(流式回合法 SSE),
    绝不拖到上游超时 —— R0 §1.5 实测上游不可达时对话会挂住 100 秒以上,
    用户完全看不出发生了什么
schema 清洗规则挪去了 schema_clean.py(向导的工具调用检测要用同一份)。
"""
import collections
import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from schema_clean import clean, clean_tools, ensure_object_schema  # noqa: F401

_TRACE_LOG: collections.deque = collections.deque(maxlen=100)


def _record_trace(
    *,
    method: str,
    path: str,
    upstream: str,
    req_model: str = "",
    resp_model: str = "",
    status: int = 200,
    duration_ms: int = 0,
    error: str = "",
) -> None:
    entry = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "method": method,
        "path": path,
        "upstream": (upstream or "").split("?")[0],
        "req_model": req_model or "",
        "resp_model": resp_model or "",
        "status": int(status or 0),
        "duration_ms": int(duration_ms or 0),
        "error": (error or "")[:200],
    }
    _TRACE_LOG.append(entry)
    print(
        f"[shim-trace] {method} {path} upstream={entry['upstream']} "
        f"req_model={entry['req_model'] or '-'} resp_model={entry['resp_model'] or '-'} "
        f"status={entry['status']} duration_ms={entry['duration_ms']}ms err={entry['error'] or '-'}",
        flush=True,
    )

# 环境变量里的默认上游网关。**空着也能启动**(M1):配置可能存在数据库里,
# 由 opencode 的 provider 通过 `X-Hunter-Upstream` 请求头逐次带过来。
UPSTREAM = (os.environ.get("LLM_BASE_URL") or "").strip().rstrip("/")
LISTEN_PORT = int(os.environ.get("SHIM_PORT", "3999"))
# 监听地址默认 0.0.0.0;Railway 老环境私有网络 IPv6-only,那里设 HUNTER_BIND_HOST=::
LISTEN_HOST = os.environ.get("HUNTER_BIND_HOST") or "0.0.0.0"
# 我们对外假装成 /v1,收到的 /v1/xxx 会被转成 上游 + /xxx
LISTEN_PREFIX = "/v1"
# opencode 送来的上游地址走这个头(gen-config.py / opencode_admin.apply_llm 写的)
UPSTREAM_HEADER = "X-Hunter-Upstream"

# 允许关闭 think 剥离(默认开)· 出 bug 时可紧急关掉不重构
STRIP_THINK = os.environ.get("LLM_STRIP_THINK", "1") == "1"

# 放行内网上游。**默认关**:shim 是容器内服务,能访问 docker 网络里的一切,
# 上游地址又是用户可填的 —— 不校验就等于给了一个「让 shim 替我访问内网」的按钮
# (SSRF)。确实要用自建内网网关的人显式打开它,并且自己承担风险。
# 打开之后**仍然**拒绝:内部服务名、链路本地地址(含 169.254.169.254 云元数据)。
ALLOW_PRIVATE = (os.environ.get("LLM_SHIM_ALLOW_PRIVATE") or "").strip() in ("1", "true", "yes", "on")

# 这套部署自己的容器名。它们没有一个是大模型网关:
# api/web 是我们自己的服务(拿它当上游 = 让 shim 替人打内部接口),
# llm-shim 是自己(无限套娃),postgres/redis/opencode 同理。
# **ALLOW_PRIVATE 也不放行这一组** —— 没有任何正当理由要往这儿转发。
INTERNAL_HOSTS = {
    "api", "web", "postgres", "redis", "opencode", "llm-shim", "llm_shim", "shim",
}

# 未配置时的占位模型名(见 scripts/opencode/gen-config.py)
PLACEHOLDER_MODEL = "hunter-unconfigured"

# ⚠️ 措辞以**不撒谎**为准:写进去的每一条路都得真的存在。
# M2 起首启向导(/setup)已经上线,所以这里改成指向它;M1 时期那句
# 「或等初始化向导上线后在首页完成配置」已经作废。
UNCONFIGURED_MSG = (
    "大模型尚未配置:这套部署还没有可用的大模型地址。"
    "打开浏览器访问这台实例的 /setup 完成首启向导即可(不用改任何文件);"
    "也可以在 .env 里填好 LLM_BASE_URL / LLM_API_KEY / LLM_DEFAULT_MODEL 后 docker compose up -d。"
)


def check_upstream(url: str, allow_private=None):
    """校验上游地址 → (是否放行, 中文原因)。

    只允许 http/https;拒绝内部服务名、回环、链路本地(含云元数据
    169.254.169.254)、私有网段、保留与组播地址。

    ⚠️ **必须解析一次 DNS 再判断 IP**,不能只看字符串:攻击者可以让一个公网域名
    解析到 127.0.0.1 或 169.254.169.254,字符串上完全看不出来。
    """
    if allow_private is None:
        allow_private = ALLOW_PRIVATE
    raw = (url or "").strip()
    if not raw:
        return False, "上游地址为空"
    try:
        p = urllib.parse.urlparse(raw)
    except ValueError as e:
        return False, f"上游地址解析失败({type(e).__name__})"
    if p.scheme not in ("http", "https"):
        return False, f"上游地址只允许 http/https,收到 {p.scheme or '(没有协议头)'}"
    try:
        host = (p.hostname or "").strip().rstrip(".").lower()
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError as e:
        return False, f"上游地址里的主机名/端口不合法({e})"
    if not host:
        return False, "上游地址里没有主机名"
    if host in INTERNAL_HOSTS:
        return False, f"上游地址指向本部署的内部服务 {host},不允许"
    if host == "localhost" or host.endswith(".localhost"):
        if not allow_private:
            return False, "上游地址指向本机回环(localhost),不允许"
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        # 解析不了就不放行。转发过去也是失败,而这里能给一句看得懂的话。
        return False, f"解析不了上游主机名 {host}"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        # ⚠️ 判断顺序是从具体到笼统,别调换:IPv4 的 is_private 把回环与链路本地也
        # 算在内,而 IPv6 的 `::1` 连 is_reserved 都为真 —— 先判笼统的,报出来的
        # 原因就会变成没头没脑的「保留地址」。
        if ip.is_link_local:
            # 169.254.0.0/16 · 云厂商元数据服务(169.254.169.254)就在这里,
            # 它能吐出实例凭证 —— **ALLOW_PRIVATE 也不放行**。
            return False, f"上游地址解析到链路本地地址 {ip}(云元数据网段),不允许"
        if ip.is_multicast or ip.is_unspecified:
            return False, f"上游地址解析到特殊地址 {ip},不允许"
        if ip.is_loopback:
            if not allow_private:
                return False, (f"上游地址解析到回环地址 {ip},不允许。"
                               "确实要用本机网关请设 LLM_SHIM_ALLOW_PRIVATE=1")
        elif ip.is_private:
            if not allow_private:
                return False, (f"上游地址解析到内网地址 {ip},不允许 —— "
                               "防止 shim 被当成访问内网的跳板。"
                               "确实要用自建内网网关请设 LLM_SHIM_ALLOW_PRIVATE=1")
        elif ip.is_reserved:
            return False, f"上游地址解析到保留地址 {ip},不允许"
    return True, ""


def error_body(message: str, code: str = "hunter_unconfigured",
               etype: str = "invalid_request_error") -> dict:
    """OpenAI 兼容的错误体。前端 / SDK 认得 `error.message`。"""
    return {"error": {"message": message, "type": etype, "code": code, "param": None}}


def error_sse(message: str, model: str = PLACEHOLDER_MODEL) -> bytes:
    """流式请求的错误回复 —— 一条正文帧 + 一条收尾帧 + [DONE]。

    为什么用**正文帧**而不是只发一个 `{"error":...}` 帧:正文帧是 SSE 里最不会被
    误解的东西,任何 OpenAI 兼容客户端都会把它渲染成回答文字,用户直接看得见。
    只发 error 帧的话,不同 SDK 处理不一,最坏的结果是前端一片空白 ——
    而「一片空白」正是这条路径要消灭的症状(R0 §1.5:上游不可达时对话挂住 100 秒
    以上,用户完全看不出发生了什么)。
    """
    created = int(time.time())
    base = {"id": "hunter-shim-error", "object": "chat.completion.chunk",
            "created": created, "model": model}
    first = dict(base, choices=[{"index": 0, "delta": {"role": "assistant", "content": message},
                                 "finish_reason": None}])
    last = dict(base, choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}])
    return (b"data: " + json.dumps(first, ensure_ascii=False).encode() + b"\n\n"
            + b"data: " + json.dumps(last, ensure_ascii=False).encode() + b"\n\n"
            + b"data: [DONE]\n\n")


# 兼容老名字(仓库里别处可能还在 import)
_ensure_object_schema = ensure_object_schema


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
    clean_tools(obj)                 # 见 schema_clean.py(api 的向导检测共用同一份)
    _maybe_inject_no_think(obj)
    return json.dumps(obj).encode()


class Handler(BaseHTTPRequestHandler):
    def _target(self, upstream: str) -> str:
        path = self.path
        if path.startswith(LISTEN_PREFIX):
            path = path[len(LISTEN_PREFIX):]
        return upstream + path

    def _request_upstream(self) -> str:
        """这一次请求该转发到哪。请求头优先,其次环境变量。

        头优先是必须的:大模型配置存进数据库之后,shim 容器的 LLM_BASE_URL 是空的,
        地址只能由 opencode 的 provider(options.headers)逐次带过来。
        """
        return ((self.headers.get(UPSTREAM_HEADER) or "").strip().rstrip("/")
                or UPSTREAM)

    def _send_error(self, message: str, *, stream: bool, model: str = PLACEHOLDER_MODEL,
                    status: int = 400) -> None:
        """立刻回一个 OpenAI 兼容的错误。**不能等上游超时**。

        R0 §1.5 实测:上游不可达时 opencode 那边的对话会挂住 100 秒以上,
        用户完全看不出发生了什么。所以「没配置 / 地址不让用」这两种情况必须由
        shim 自己当场回话。
        """
        print(f"[shim] 拒绝转发 · {message}", flush=True)
        if stream:
            # 流式:回 200 + 合法 SSE。正文帧里就是这句中文,用户直接看得见。
            payload = error_sse(message, model or PLACEHOLDER_MODEL)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()
            return
        payload = json.dumps(error_body(message), ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()

    def _precheck(self, body: bytes | None):
        """→ (上游地址, 拒绝原因, 是否流式, 模型名)。拒绝原因非空就别转发了。"""
        stream, model = False, ""
        if body:
            try:
                obj = json.loads(body.decode())
                stream = bool(obj.get("stream"))
                model = str(obj.get("model") or "")
            except Exception:       # noqa: BLE001
                pass
        upstream = self._request_upstream()
        if model.strip() == PLACEHOLDER_MODEL:
            # gen-config 在「一项都没配」时写的占位模型名
            return upstream, UNCONFIGURED_MSG, stream, model
        if not upstream:
            return upstream, UNCONFIGURED_MSG, stream, model
        ok, why = check_upstream(upstream)
        if not ok:
            return upstream, why, stream, model
        return upstream, "", stream, model

    def _proxy(self, body: bytes | None):
        t0 = time.monotonic()
        upstream, reject, stream, model = self._precheck(body)
        if reject:
            _record_trace(
                method=self.command,
                path=self.path,
                upstream=upstream,
                req_model=model,
                status=400,
                duration_ms=int((time.monotonic() - t0) * 1000),
                error=reject,
            )
            self._send_error(reject, stream=stream, model=model)
            return
        if body is not None and self.path.endswith("/chat/completions"):
            body = sanitize_body(body)
        # SSL EOF 常发生在 keep-alive stream 尾部 · 加 Connection: close 强制新连接
        # 重试 1 次 · 主要覆盖偶发 SSL_UNEXPECTED_EOF · 不做无限重试防死循环
        for attempt in (1, 2):
            try:
                req = urllib.request.Request(self._target(upstream), data=body,
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
                resp_model = ""
                # 非 SSE(整段 JSON)· 一次读 + 一次剥 think · 再吐出
                # SSE 流式 · 走 line-buffered 逐行剥 think · 保持 tool_calls 增量边界
                # stream 尾部 SSL EOF 不算失败(数据已到) · 静默吞掉
                try:
                    if not is_sse:
                        resp_bytes = r.read()
                        try:
                            parsed_resp = json.loads(resp_bytes.decode())
                            if isinstance(parsed_resp, dict):
                                resp_model = str(parsed_resp.get("model") or "")
                        except Exception:
                            pass
                        if STRIP_THINK:
                            resp_bytes = strip_think_nonstream(resp_bytes)
                        self.wfile.write(resp_bytes); self.wfile.flush()
                    else:
                        stripper = ThinkStripper() if STRIP_THINK else None
                        buf = b""
                        # `data: [DONE]` 必须延后写:补发帧要排在它前面,
                        # 否则 OpenAI 兼容 SDK 读到 [DONE] 就收工,补发被无视。
                        done_line = None
                        while True:
                            # read() 会等满缓冲区或 EOF，read1() 及时返回可用数据。
                            chunk = r.read1(4096)
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
                                if not resp_model and stripper and stripper._template:
                                    resp_model = str(stripper._template.get("model") or "")
                            self.wfile.flush()
                        # 收尾:残帧 → 补发扣留的尾巴 → 最后才放行 [DONE]
                        if STRIP_THINK:
                            if buf:
                                self.wfile.write(rewrite_sse_line(buf, stripper) + b"\n")
                                if not resp_model and stripper and stripper._template:
                                    resp_model = str(stripper._template.get("model") or "")
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
                _record_trace(
                    method=self.command,
                    path=self.path,
                    upstream=upstream,
                    req_model=model,
                    resp_model=resp_model,
                    status=r.status,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )
                return
            except urllib.error.HTTPError as e:
                data = e.read()
                err_txt = data.decode(errors="replace")[:300]
                print(f"[shim] upstream {e.code}: {err_txt}",
                      flush=True)
                _record_trace(
                    method=self.command,
                    path=self.path,
                    upstream=upstream,
                    req_model=model,
                    status=e.code,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    error=err_txt,
                )
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
                _record_trace(
                    method=self.command,
                    path=self.path,
                    upstream=upstream,
                    req_model=model,
                    status=502,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    error=str(e),
                )
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
        if self.path.startswith("/trace"):
            payload = json.dumps(
                {"ok": True, "traces": list(reversed(_TRACE_LOG))},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self._proxy(None)

    def log_message(self, *args):
        return          # 默认每请求一行访问日志,太吵


if __name__ == "__main__":
    # **缺 LLM_BASE_URL 不再退出**(M1)。配置可能存在数据库里,由 opencode 的
    # provider 通过 X-Hunter-Upstream 头逐次带过来;一项都没配时 shim 也要活着 ——
    # 它正是那句「大模型尚未配置」的出口。退出的话 compose 的健康检查过不去,
    # opencode 因为 depends_on 根本起不来,用户连首页都打不开。
    print(f"[shim] listening {LISTEN_HOST}:{LISTEN_PORT}{LISTEN_PREFIX} -> "
          f"{UPSTREAM or '(环境变量未配上游 · 等请求头 ' + UPSTREAM_HEADER + ')'}",
          flush=True)
    if ALLOW_PRIVATE:
        print("[shim] ⚠ LLM_SHIM_ALLOW_PRIVATE=1 · 已放行内网/回环上游地址。"
              "内部服务名与链路本地(云元数据)地址仍然拒绝。", flush=True)
    Server = ThreadingHTTPServer
    if ":" in LISTEN_HOST:              # IPv6 字面量(如 ::):换 AF_INET6 并关掉 v6only,一个 socket 同时收 v4/v6
        class Server(ThreadingHTTPServer):      # noqa: F811
            address_family = socket.AF_INET6

            def server_bind(self):
                try:
                    self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                except OSError:
                    pass                        # 内核不让改就只收 IPv6,总比起不来强
                super().server_bind()

    Server((LISTEN_HOST, LISTEN_PORT), Handler).serve_forever()
