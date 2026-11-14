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
import threading
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

# 允许关闭「并行工具调用补 index」(默认开)· 出 bug 时可紧急关掉。
# 为什么需要:hunter 网关(Gemini 经 OneAPI)在流式响应里发并行 tool_calls 时
# **不带 `index` 字段**(HCA M0 实测,hunter-chat / hunter-deep 都一样)。
# OpenAI 流式协议靠 `index` 区分同一轮里的多个工具调用,AtomCode 的
# openai_compat.rs 是 `tc.index.unwrap_or(0)` —— 缺 index 时三个调用全落进
# 槽位 0,name 相互覆盖、arguments 首尾相接,于是出现
# `write_file(file_path=..., command="python3 -c ...", content=...)`
# 这种「把 bash 的参数混进 write_file」的脏调用,进而触发工具死循环。
# 这一层按 tool_call 的 `id` 首次出现顺序补回 index,不改其余任何字段。
FIX_TOOL_INDEX = os.environ.get("LLM_FIX_TOOL_INDEX", "1") == "1"

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


# DNS 解析缓存与瞬时超时重试（应对 macOS Colima 休眠唤醒 / 代理切换瞬间 DNS 偶发 2s 超时）
DNS_CACHE_TTL = float(os.environ.get("LLM_SHIM_DNS_TTL", "300"))
DNS_RETRIES = int(os.environ.get("LLM_SHIM_DNS_RETRIES", "3"))
DNS_RETRY_DELAY = float(os.environ.get("LLM_SHIM_DNS_RETRY_DELAY", "0.3"))
_DNS_CACHE: dict[tuple[str, int], tuple[float, list]] = {}
_DNS_LOCK = threading.Lock()


def clear_dns_cache() -> None:
    """清空 DNS 解析缓存（主要供单元测试隔离使用）。"""
    with _DNS_LOCK:
        _DNS_CACHE.clear()


def _resolve_host(host: str, port: int) -> list:
    """带缓存、重试与历史缓存兜底（stale cache fallback）的 DNS 解析。"""
    key = (host, port)
    now = time.monotonic()
    with _DNS_LOCK:
        cached = _DNS_CACHE.get(key)
        if cached is not None and (now - cached[0]) < DNS_CACHE_TTL:
            return cached[1]

    last_err: Exception | None = None
    attempts = max(1, DNS_RETRIES)
    for attempt in range(1, attempts + 1):
        try:
            infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
            with _DNS_LOCK:
                _DNS_CACHE[key] = (time.monotonic(), infos)
            return infos
        except socket.gaierror as e:
            last_err = e
            if attempt < attempts:
                time.sleep(DNS_RETRY_DELAY)

    with _DNS_LOCK:
        stale = _DNS_CACHE.get(key)
        if stale is not None:
            print(
                f"[shim] DNS 瞬时解析超时，沿用上次缓存的解析结果: {host}",
                flush=True,
            )
            return stale[1]

    assert last_err is not None
    raise last_err


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
        infos = _resolve_host(host, port)
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



class ToolCallIndexer:
    """给流式 `delta.tool_calls[]` 补回缺失的 `index`(每个响应一个实例)。

    规则:
      · 分片自带合法 `index` → 原样不动(网关正常时这层是纯透传);
      · 只有 `id` → 按 **id 首次出现的顺序** 分配 0,1,2…,同一个 id 始终同一个 index
        (工具调用可能分多帧续传 arguments,必须稳定);
      · 连 `id` 都没有 → 归到当前最后一个已知 index(续传帧的常见形状);
        一个 id 都还没见过就给 0。
    """

    def __init__(self):
        self._by_id = {}        # tool_call id -> index
        self._next = 0          # 下一个可分配的 index

    def assign(self, tc: dict) -> bool:
        """就地补 index · 返回是否改动过。"""
        if not isinstance(tc, dict):
            return False
        if isinstance(tc.get("index"), int):
            # 网关给了 index:以它为准,并让后续无 id 的续传帧接在它后面
            self._next = max(self._next, tc["index"] + 1)
            return False
        tid = tc.get("id")
        if tid:
            if tid not in self._by_id:
                self._by_id[tid] = self._next
                self._next += 1
            tc["index"] = self._by_id[tid]
        else:
            # 无 id 的续传帧 → 挂到最近一个调用上
            tc["index"] = max(self._next - 1, 0)
        return True


def rewrite_sse_line(line: bytes, stripper, indexer=None) -> bytes:
    """处理一行 SSE(不含末尾 `\\n`)。

    碰两处,其余原样转发:
      · `choices[].delta.content` —— 剥 <think>(stripper 为 None 时跳过);
      · `choices[].delta.tool_calls[]` —— 补回缺失的 `index`(indexer 为 None 时跳过)。
    """
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
    if stripper is not None:
        stripper.remember_template(obj)
    changed = False
    for ch in choices:
        delta = ch.get("delta") or {}
        c = delta.get("content")
        if stripper is not None and isinstance(c, str) and c:
            cleaned = stripper.process(c)
            if cleaned != c:
                delta["content"] = cleaned
                changed = True
        if indexer is not None:
            tcs = delta.get("tool_calls")
            if isinstance(tcs, list):
                for tc in tcs:
                    if indexer.assign(tc):
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


# ── 请求瀑布追踪（I2）· 默认关 ─────────────────────────────────────────────
#
# 为什么要在 shim 里做：一轮对话真正发给网关的是什么，只有这一跳看得全 ——
# 系统提示分了几条、每条多少字符、挂了多少个工具、工具 schema 占多少字节、
# 上游的首字延迟与总耗时、网关回的 usage。daemon 的 SSE 里这些一个都没有。
#
# `SHIM_TRACE_DIR` 有值才开。写的是 jsonl，一行一次 /chat/completions；
# **不记任何消息正文、不记 Authorization**，只记长度与工具名 ——
# 追踪文件会进评测产物，正文里有持仓与密钥性质的东西。
TRACE_DIR = (os.environ.get("SHIM_TRACE_DIR") or "").strip()


def _trace_request(body_bytes):
    """把请求体拆成可比较的尺寸构成。解析不了就返回 None（追踪从不影响转发）。"""
    try:
        obj = json.loads(body_bytes.decode())
    except Exception:  # noqa: BLE001
        return None
    msgs = obj.get("messages") or []
    parts = []
    for m in msgs:
        c = m.get("content")
        if isinstance(c, list):          # 多模态形态：只数文本片段
            chars = sum(len(x.get("text") or "") for x in c if isinstance(x, dict))
        else:
            chars = len(c or "")
        tc = m.get("tool_calls") or []
        parts.append({"role": m.get("role"), "chars": chars,
                      "tool_calls": len(tc),
                      "tool_call_chars": sum(len(json.dumps(x, ensure_ascii=False)) for x in tc)})
    tools = obj.get("tools") or []
    tinfo = []
    for t in tools:
        fn = (t.get("function") or {}) if isinstance(t, dict) else {}
        tinfo.append({
            "name": fn.get("name"),
            "bytes": len(json.dumps(t, ensure_ascii=False).encode()),
            "desc_chars": len(fn.get("description") or ""),
            "params_bytes": len(json.dumps(fn.get("parameters") or {}, ensure_ascii=False).encode()),
        })
    return {
        "model": obj.get("model"), "stream": bool(obj.get("stream")),
        "req_bytes": len(body_bytes),
        "n_messages": len(msgs), "messages": parts,
        "system_chars": sum(p["chars"] for p in parts if p["role"] == "system"),
        "n_tools": len(tools),
        "tools_bytes": sum(t["bytes"] for t in tinfo),
        "tools": tinfo,
    }


def _trace_write(rec):
    if not TRACE_DIR:
        return
    try:
        os.makedirs(TRACE_DIR, exist_ok=True)
        path = os.path.join(TRACE_DIR, "requests.jsonl")
        new_file = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if new_file:
            # 容器里这个进程是 root，而收追踪的评测脚本跑在宿主上、是普通用户 ——
            # 默认的 0644 会让它连清空都做不了（实测：run_ab 每次运行前清空追踪
            # 报 Permission denied，结果一整批只有一个混在一起的大文件）。
            # 追踪里不含正文与密钥（见 _trace_request），放宽到 0666 没有额外风险。
            os.chmod(path, 0o666)
    except Exception:  # noqa: BLE001
        pass                              # 追踪写不进去也绝不影响这一次转发


def _trace_usage(line: bytes, sink: dict):
    """从 SSE 行里捞 usage（网关在最后一帧给）。捞不到就算了。"""
    if b'"usage"' not in line:
        return
    try:
        payload = line.split(b"data:", 1)[1].strip()
        if payload == b"[DONE]":
            return
        u = json.loads(payload).get("usage")
        if isinstance(u, dict):
            sink.update(u)
    except Exception:  # noqa: BLE001
        pass


# ── 工具白名单（I2）· 默认关 ───────────────────────────────────────────────
#
# 为什么做在这一跳：AtomCode 把编码工具、代码智能工具、AtomGit REST 工具**无条件**
# mount 给模型（`atomcode-coding/src/assemble.rs:225` 的 `mount_coding_tools`，
# 没有任何 config 或环境变量能关）。投研场景里 `atomgit_pr` / `code_review` /
# `trace_callers` 这一族一次都用不上，但**每一轮请求都要重发一遍它们的 schema**。
# I2 实测：一轮 59 个工具、schema 合计 59 885 字节，占 26 132 个输入 token 的大头。
#
# 这里只做**减法**：按名字/前缀把用不上的工具从请求里摘掉，其余字段一个不碰。
# 内核那边这些工具仍然注册着 —— 万一模型凭记忆硬凑一个调用，照样能执行，
# 不会出现「说有却调不动」的死角。
#
# `LLM_TOOL_DENY` 逗号分隔，支持结尾 `*` 通配（`atomgit_*`）。空 = 不过滤。
TOOL_DENY = [x.strip() for x in (os.environ.get("LLM_TOOL_DENY") or "").split(",") if x.strip()]
_TOOL_DENY_LOGGED = set()


def tool_denied(name: str) -> bool:
    for pat in TOOL_DENY:
        if pat.endswith("*"):
            if name.startswith(pat[:-1]):
                return True
        elif name == pat:
            return True
    return False


def drop_tools(obj: dict) -> int:
    """按 LLM_TOOL_DENY 摘掉工具。返回摘掉的个数（没配置就是 0）。"""
    if not TOOL_DENY:
        return 0
    tools = obj.get("tools")
    if not isinstance(tools, list):
        return 0

    def name_of(t):
        return ((t.get("function") or {}).get("name") or "") if isinstance(t, dict) else ""

    kept = [t for t in tools if not tool_denied(name_of(t))]
    dropped = sorted({name_of(t) for t in tools if tool_denied(name_of(t))})
    obj["tools"] = kept
    n = len(tools) - len(kept)
    # **摘掉工具是会改变模型行为的**，所以每种组合打一次日志，出问题时看得见。
    sig = tuple(dropped)
    if n and sig not in _TOOL_DENY_LOGGED:
        _TOOL_DENY_LOGGED.add(sig)
        print(f"[shim] 工具白名单：本次请求摘掉 {n} 个 —— {'、'.join(sig)}", flush=True)
    return n


def sanitize_body(body_bytes: bytes) -> bytes:
    try:
        obj = json.loads(body_bytes.decode())
    except Exception:
        return body_bytes            # 不是 JSON 就别碰
    clean_tools(obj)                 # 见 schema_clean.py(api 的向导检测共用同一份)
    drop_tools(obj)                  # I2 工具白名单（LLM_TOOL_DENY，默认不配=不过滤）
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
        trace = None
        if body is not None and self.path.endswith("/chat/completions"):
            body = sanitize_body(body)
            if TRACE_DIR:
                trace = _trace_request(body)
                if trace is not None:
                    trace["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    trace["t_start"] = time.time()
                    trace["usage"] = {}
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
                if trace is not None:
                    trace["upstream_headers_ms"] = round((time.time() - trace["t_start"]) * 1000, 1)
                    trace["status"] = r.status
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
                        if trace is not None:
                            trace["ttfb_ms"] = round((time.time() - trace["t_start"]) * 1000, 1)
                            _trace_usage(b"data:" + resp_bytes, trace["usage"])
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
                        indexer = ToolCallIndexer() if FIX_TOOL_INDEX else None
                        # 两件事任一要做,就得逐行解析重写;都不做才原样透传
                        rewriting = STRIP_THINK or FIX_TOOL_INDEX
                        buf = b""
                        # `data: [DONE]` 必须延后写:补发帧要排在它前面,
                        # 否则 OpenAI 兼容 SDK 读到 [DONE] 就收工,补发被无视。
                        done_line = None
                        while True:
                            # read() 会等满缓冲区或 EOF，read1() 及时返回可用数据。
                            chunk = r.read1(4096)
                            if not chunk:
                                break
                            if trace is not None and "ttfb_ms" not in trace:
                                trace["ttfb_ms"] = round((time.time() - trace["t_start"]) * 1000, 1)
                            if not rewriting:
                                self.wfile.write(chunk); self.wfile.flush()
                                continue
                            buf += chunk
                            # 按 \n 切 · 保留最后一段(可能不完整)· SSE 每行末尾都是 \n
                            while b"\n" in buf:
                                line, buf = buf.split(b"\n", 1)
                                if line.strip().replace(b" ", b"") == b"data:[DONE]":
                                    done_line = line
                                    continue
                                # [DONE] 之后的空行是它自己的帧终止符。done_line 收尾时
                                # 会带 b"\n\n" 重新写出来,这里再放行就多一个 \n,
                                # 下游按帧解析的 SDK 会看到一个空帧(2026-09-22 由回归用例
                                # test_fragmented_utf8_think_tags_and_tool_calls 抓到)。
                                if done_line is not None and not line.strip():
                                    continue
                                if trace is not None:
                                    _trace_usage(line, trace["usage"])
                                self.wfile.write(rewrite_sse_line(line, stripper, indexer) + b"\n")
                                if not resp_model and stripper and stripper._template:
                                    resp_model = str(stripper._template.get("model") or "")
                            self.wfile.flush()
                        # 收尾:残帧 → 补发扣留的尾巴 → 最后才放行 [DONE]
                        if rewriting:
                            if buf:
                                self.wfile.write(rewrite_sse_line(buf, stripper, indexer) + b"\n")
                                if not resp_model and stripper and stripper._template:
                                    resp_model = str(stripper._template.get("model") or "")
                            # 正常情况下 stripper 按需扣留 · 这里多半是空;
                            # 只有流恰好断在半个 <think> 上才非空。**不能写 pass** ——
                            # 2026-09-07 就是这行 pass 把每条回答的末尾吞了 7 个字符。
                            # STRIP_THINK=0 而只开补 index 时 stripper 是 None,没有尾巴要补
                            frame = stripper.tail_frame() if stripper is not None else None
                            if frame:
                                self.wfile.write(frame)
                                print(f"[shim] 补发被扣留的尾巴 {len(frame)}B", flush=True)
                            if done_line is not None:
                                self.wfile.write(done_line + b"\n\n")
                            self.wfile.flush()
                except Exception as se:
                    print(f"[shim] stream tail eof (ignored · data delivered): {type(se).__name__}",
                          flush=True)
                if trace is not None:
                    trace["total_ms"] = round((time.time() - trace.pop("t_start")) * 1000, 1)
                    _trace_write(trace)
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
