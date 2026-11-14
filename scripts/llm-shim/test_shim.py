"""llm-shim 回归用例 · 只用回环 HTTP 服务,不联网。

跑法:docker run --rm -v "$PWD/scripts/llm-shim:/t" -w /t python:3.12-alpine \
        python3 -m unittest discover -p 'test_*.py' -v

三组:
  SSEProxyTests      —— 流式转发与 <think> 剥离(2026-09-07 吞掉末尾 7 字符那次)
  UpstreamGuardTests —— 上游地址白名单(M1 测试用例 6)
  UnconfiguredTests  —— 没配置时立刻回中文错误,不等上游超时(R0 §1.5)
"""
import http.client
import json
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import shim


DONE = b"data: [DONE]\n\n"


def sse_frame(delta):
    obj = {
        "id": "test-stream",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }
    return b"data: " + json.dumps(obj, ensure_ascii=False).encode() + b"\n\n"


def data_lines(body):
    return [line[5:].strip() for line in body.splitlines()
            if line.startswith(b"data:")]


@contextmanager
def proxy_response(chunks, *, strip_think=True, gate=None, chunked=True):
    finished = threading.Event()

    class Upstream(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            if chunked:
                self.send_header("Transfer-Encoding", "chunked")
            else:
                self.send_header("Content-Length", str(sum(map(len, chunks))))
            self.end_headers()
            try:
                for index, chunk in enumerate(chunks):
                    if chunked:
                        self.wfile.write(f"{len(chunk):x}\r\n".encode())
                    self.wfile.write(chunk)
                    if chunked:
                        self.wfile.write(b"\r\n")
                    self.wfile.flush()
                    if index == 0 and gate is not None:
                        gate.wait()
                if chunked:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                # A failing assertion can close the client during cleanup.
                pass
            finally:
                finished.set()

        def log_message(self, *args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), shim.Handler)
    threads = []
    connection = http.client.HTTPConnection("127.0.0.1", proxy.server_port, timeout=3)
    # 测试上游是 127.0.0.1 —— 新加的白名单默认会拦下回环地址,所以这里必须显式
    # 放行内网(等价于 LLM_SHIM_ALLOW_PRIVATE=1)。**这不是为了绕过检查**:
    # 下面 UpstreamGuardTests 专门测不放行时的行为。
    with patch.object(shim, "UPSTREAM", f"http://127.0.0.1:{upstream.server_port}"), \
            patch.object(shim, "ALLOW_PRIVATE", True), \
            patch.object(shim, "STRIP_THINK", strip_think):
        try:
            for server in (upstream, proxy):
                thread = threading.Thread(
                    target=server.serve_forever,
                    kwargs={"poll_interval": 0.05},
                    daemon=True,
                )
                thread.start()
                threads.append(thread)
            connection.request(
                "POST", "/v1/chat/completions",
                body=b'{"model":"test","stream":true}',
                headers={"Content-Type": "application/json"},
            )
            with connection.getresponse() as response:
                yield response, finished
        finally:
            if gate is not None:
                gate.set()
            connection.close()
            for server in (proxy, upstream):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join()


class SSEProxyTests(unittest.TestCase):
    def test_first_frame_arrives_before_upstream_finishes(self):
        first = sse_frame({"content": "first token"})
        chunks = [first, sse_frame({"content": " last token"}), DONE]
        self.assertLess(sum(map(len, chunks)), 4096)

        for strip_think in (True, False):
            for chunked in (True, False):
                with self.subTest(strip_think=strip_think, chunked=chunked):
                    gate = threading.Event()
                    with proxy_response(
                        chunks, strip_think=strip_think, gate=gate, chunked=chunked,
                    ) as (response, finished):
                        self.assertEqual(response.status, 200)
                        first_line = response.readline()
                        self.assertEqual(first_line, first.splitlines(keepends=True)[0])
                        self.assertFalse(finished.is_set())
                        # The upstream cannot send the rest or EOF until the
                        # first frame has reached this client.
                        gate.set()
                        body = first_line + response.read()
                    self.assertEqual(data_lines(body), data_lines(b"".join(chunks)))

    def test_fragmented_utf8_think_tags_and_tool_calls(self):
        tool_delta = {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}
        payload = b"".join([
            sse_frame({"content": "你<th"}),
            sse_frame({"content": "ink>hidden</thi"}),
            sse_frame(tool_delta),
            sse_frame({"content": "nk>好<thi"}),
            DONE,
        ])
        # HTTP chunk boundaries split both JSON lines and multibyte characters.
        chunks = [bytes([byte]) for byte in payload]

        for strip_think in (True, False):
            with self.subTest(strip_think=strip_think):
                with proxy_response(chunks, strip_think=strip_think) as (response, _):
                    body = response.read()
                lines = data_lines(body)
                self.assertEqual(lines[-1], b"[DONE]")
                self.assertEqual(lines.count(b"[DONE]"), 1)
                frames = [json.loads(line) for line in lines[:-1]]
                deltas = [frame["choices"][0]["delta"] for frame in frames]
                self.assertIn(tool_delta, deltas)
                content = "".join(delta.get("content", "") for delta in deltas)
                if strip_think:
                    self.assertEqual(content, "你好<thi")
                    self.assertTrue(all(frame["id"] == "test-stream" for frame in frames))
                else:
                    self.assertEqual(body, payload)
                    self.assertEqual(content, "你<think>hidden</think>好<thi")


# ─────────────────────────────────────────────────────────────────────
# M1 新增 · 上游地址白名单(测试用例 6)
# ─────────────────────────────────────────────────────────────────────
class UpstreamGuardTests(unittest.TestCase):
    """check_upstream 的放行与拒绝。

    ⚠️ 判断必须**解析 DNS 之后看 IP**,不能只看字符串 —— 一个公网域名完全可以
    解析到 127.0.0.1 或 169.254.169.254。下面 test_hostname_resolving_inward
    就是盯这条的。
    """

    def setUp(self):
        shim.clear_dns_cache()

    def ok(self, url, **kw):
        allowed, why = shim.check_upstream(url, **kw)
        self.assertTrue(allowed, f"{url} 应放行,却被拒:{why}")

    def no(self, url, expect_in="", **kw):
        allowed, why = shim.check_upstream(url, **kw)
        self.assertFalse(allowed, f"{url} 应拒绝,却放行了")
        self.assertTrue(why.strip(), "拒绝必须给出中文原因")
        if expect_in:
            self.assertIn(expect_in, why)

    def test_public_https_allowed(self):
        with patch.object(shim.socket, "getaddrinfo",
                          return_value=[(2, 1, 6, "", ("104.18.0.1", 443))]):
            self.ok("https://api.deepseek.com/v1")
            self.ok("http://api.deepseek.com/v1")

    def test_scheme_must_be_http(self):
        for url in ("file:///etc/passwd", "ftp://example.com/v1",
                    "gopher://example.com", "api.deepseek.com/v1"):
            with self.subTest(url=url):
                self.no(url, "http")

    def test_internal_service_names_rejected(self):
        for host in ("api", "web", "postgres", "redis", "opencode", "llm-shim"):
            with self.subTest(host=host):
                self.no(f"http://{host}:8000/v1", "内部服务")
                # 显式放行内网也不放行内部服务名 —— 它们没有一个是大模型网关
                self.no(f"http://{host}:8000/v1", "内部服务", allow_private=True)

    def test_loopback_rejected(self):
        with patch.object(shim.socket, "getaddrinfo",
                          return_value=[(2, 1, 6, "", ("127.0.0.1", 80))]):
            self.no("http://127.0.0.1:3000/v1", "回环")
        self.no("http://localhost:3000/v1", "回环")

    def test_link_local_and_metadata_rejected_even_when_private_allowed(self):
        with patch.object(shim.socket, "getaddrinfo",
                          return_value=[(2, 1, 6, "", ("169.254.169.254", 80))]):
            self.no("http://metadata.example/latest/meta-data/", "链路本地")
            # 云元数据能吐出实例凭证 —— 开关也不放行
            self.no("http://metadata.example/latest/meta-data/", "链路本地",
                    allow_private=True)

    def test_private_ranges_rejected_by_default(self):
        for ip in ("10.0.0.5", "172.16.3.4", "192.168.1.9"):
            with self.subTest(ip=ip):
                with patch.object(shim.socket, "getaddrinfo",
                                  return_value=[(2, 1, 6, "", (ip, 3000))]):
                    self.no(f"http://{ip}:3000/v1", "内网")
                    self.no(f"http://{ip}:3000/v1", "LLM_SHIM_ALLOW_PRIVATE")

    def test_private_allowed_with_switch(self):
        with patch.object(shim.socket, "getaddrinfo",
                          return_value=[(2, 1, 6, "", ("192.168.1.9", 3000))]):
            self.ok("http://192.168.1.9:3000/v1", allow_private=True)
        with patch.object(shim.socket, "getaddrinfo",
                          return_value=[(2, 1, 6, "", ("127.0.0.1", 3000))]):
            self.ok("http://127.0.0.1:3000/v1", allow_private=True)

    def test_hostname_resolving_inward(self):
        """看着像公网域名,解析出来是内网 —— 只看字符串的实现会在这里翻车。"""
        with patch.object(shim.socket, "getaddrinfo",
                          return_value=[(2, 1, 6, "", ("10.1.2.3", 443))]):
            self.no("https://totally-public.example.com/v1", "内网")

    def test_ipv6_loopback_and_ula(self):
        with patch.object(shim.socket, "getaddrinfo",
                          return_value=[(10, 1, 6, "", ("::1", 80, 0, 0))]):
            self.no("http://[::1]:3000/v1", "回环")
        with patch.object(shim.socket, "getaddrinfo",
                          return_value=[(10, 1, 6, "", ("fd00::1", 80, 0, 0))]):
            self.no("http://[fd00::1]:3000/v1", "内网")

    def test_unresolvable_host_rejected(self):
        with patch.object(shim.socket, "getaddrinfo",
                          side_effect=shim.socket.gaierror("no such host")):
            self.no("https://nope.invalid/v1", "解析不了")

    def test_empty_upstream(self):
        self.no("", "空")

    def test_any_address_in_the_set_is_enough_to_reject(self):
        """多条 A 记录里只要有一条落在内网就拒绝,不看"有没有一条是公网的"。"""
        with patch.object(shim.socket, "getaddrinfo", return_value=[
                (2, 1, 6, "", ("104.18.0.1", 443)),
                (2, 1, 6, "", ("127.0.0.1", 443))]):
            self.no("https://mixed.example.com/v1", "回环")

    def test_dns_transient_failure_retries_and_succeeds(self):
        """DNS 首次解析偶发超时(抛 gaierror)、第 2 次成功时自动重试并通过校验。"""
        with patch.object(shim, "DNS_RETRY_DELAY", 0.001), \
                patch.object(shim.socket, "getaddrinfo", side_effect=[
                    shim.socket.gaierror("Temporary failure in name resolution"),
                    [(2, 1, 6, "", ("104.18.0.1", 443))],
                ]) as mock_getaddr:
            self.ok("https://hunter.agentpit.io/api/saas/llm/v1")
            self.assertEqual(mock_getaddr.call_count, 2)

    def test_dns_cache_hit_avoids_repeated_getaddrinfo(self):
        """连续两次校验同一上游域名时命中 DNS 缓存,getaddrinfo 只调用 1 次。"""
        with patch.object(shim.socket, "getaddrinfo",
                          return_value=[(2, 1, 6, "", ("104.18.0.1", 443))]) as mock_getaddr:
            self.ok("https://hunter.agentpit.io/api/saas/llm/v1")
            self.ok("https://hunter.agentpit.io/api/saas/llm/v1")
            self.assertEqual(mock_getaddr.call_count, 1)

    def test_dns_stale_cache_fallback_when_all_retries_fail(self):
        """DNS 缓存过期后若上游 DNS 瞬时故障(连续抛 gaierror),自动沿用上次合法公网 IP 缓存放行。"""
        with patch.object(shim, "DNS_RETRY_DELAY", 0.001), \
                patch.object(shim, "DNS_CACHE_TTL", 0.01):
            with patch.object(shim.socket, "getaddrinfo",
                              return_value=[(2, 1, 6, "", ("104.18.0.1", 443))]):
                self.ok("https://hunter.agentpit.io/api/saas/llm/v1")
            # 强制让缓存条目过期
            for k, (_, infos) in list(shim._DNS_CACHE.items()):
                shim._DNS_CACHE[k] = (0.0, infos)
            with patch.object(shim.socket, "getaddrinfo",
                              side_effect=shim.socket.gaierror("DNS timeout")) as mock_getaddr:
                self.ok("https://hunter.agentpit.io/api/saas/llm/v1")
                self.assertEqual(mock_getaddr.call_count, shim.DNS_RETRIES)


# ─────────────────────────────────────────────────────────────────────
# M1 新增 · 没配置 / 地址不让用时立刻回错(不等上游超时)
# ─────────────────────────────────────────────────────────────────────
@contextmanager
def shim_only(*, upstream="", allow_private=False):
    """只起 shim,不起上游 —— 用来确认它**自己**就把请求挡下来了。"""
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), shim.Handler)
    thread = threading.Thread(target=proxy.serve_forever,
                              kwargs={"poll_interval": 0.05}, daemon=True)
    connection = http.client.HTTPConnection("127.0.0.1", proxy.server_port, timeout=5)
    with patch.object(shim, "UPSTREAM", upstream),             patch.object(shim, "ALLOW_PRIVATE", allow_private):
        try:
            thread.start()
            yield connection
        finally:
            connection.close()
            proxy.shutdown()
            proxy.server_close()
            thread.join()


def post_chat(connection, payload, headers=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    connection.request("POST", "/v1/chat/completions",
                       body=json.dumps(payload).encode(), headers=h)
    with connection.getresponse() as response:
        return response.status, response.read()


class UnconfiguredTests(unittest.TestCase):
    def test_no_upstream_non_stream(self):
        with shim_only() as c:
            status, body = post_chat(c, {"model": "whatever", "messages": []})
        self.assertEqual(status, 400)
        obj = json.loads(body)
        self.assertIn("大模型尚未配置", obj["error"]["message"])
        # 不许出现「即将」「正在」这类暗示它自己会好的措辞
        self.assertNotIn("正在", obj["error"]["message"])

    def test_no_upstream_stream_returns_valid_sse(self):
        """流式必须回合法 SSE —— 只回 JSON 的话前端是一片空白。"""
        with shim_only() as c:
            status, body = post_chat(
                c, {"model": "whatever", "messages": [], "stream": True})
        self.assertEqual(status, 200)
        lines = data_lines(body)
        self.assertEqual(lines[-1], b"[DONE]")
        frames = [json.loads(x) for x in lines[:-1]]
        text = "".join(f["choices"][0]["delta"].get("content", "") for f in frames)
        self.assertIn("大模型尚未配置", text)
        self.assertEqual(frames[-1]["choices"][0]["finish_reason"], "stop")

    def test_placeholder_model_rejected_even_with_upstream(self):
        """gen-config 的占位模型名 —— 上游再好也不能真发出去。

        ⚠️ 这里**不能** patch socket.getaddrinfo:`shim.socket` 就是标准库的
        socket 模块,patch 掉它连测试自己那条 HTTP 客户端连接都会被改道
        (踩过:请求真的发到了 104.18.0.1:443,拿回一段 HTML)。
        占位模型名的判断排在地址校验之前,压根不需要解析 DNS。
        """
        with shim_only(upstream="https://api.deepseek.com/v1") as c:
            status, body = post_chat(
                c, {"model": shim.PLACEHOLDER_MODEL, "messages": []})
        self.assertEqual(status, 400)
        self.assertIn("大模型尚未配置", json.loads(body)["error"]["message"])

    def test_blocked_upstream_header_returns_chinese_reason(self):
        with shim_only() as c:
            status, body = post_chat(c, {"model": "m", "messages": []},
                                     headers={"X-Hunter-Upstream": "http://api:8000/v1"})
        self.assertEqual(status, 400)
        msg = json.loads(body)["error"]["message"]
        self.assertIn("内部服务", msg)

    def test_blocked_upstream_stream(self):
        with shim_only() as c:
            status, body = post_chat(
                c, {"model": "m", "messages": [], "stream": True},
                headers={"X-Hunter-Upstream": "http://169.254.169.254/latest"})
        self.assertEqual(status, 200)
        text = "".join(json.loads(x)["choices"][0]["delta"].get("content", "")
                       for x in data_lines(body)[:-1])
        self.assertIn("链路本地", text)


class UpstreamHeaderTests(unittest.TestCase):
    """X-Hunter-Upstream 覆盖环境变量 —— 配置存数据库之后就靠它了。"""

    def test_header_overrides_env(self):
        seen = {}

        class Upstream(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                seen["path"] = self.path
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                payload = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        real = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        t = threading.Thread(target=real.serve_forever,
                             kwargs={"poll_interval": 0.05}, daemon=True)
        t.start()
        try:
            # 环境变量指向一个根本起不来的端口;请求头指向真上游。
            # 转发成功就说明用的是请求头那个。
            with shim_only(upstream="http://127.0.0.1:1/v1", allow_private=True) as c:
                status, body = post_chat(
                    c, {"model": "m", "messages": []},
                    headers={"X-Hunter-Upstream":
                             f"http://127.0.0.1:{real.server_port}/v1"})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"ok": True})
            self.assertEqual(seen["path"], "/v1/chat/completions")
        finally:
            real.shutdown()
            real.server_close()
            t.join()

    def test_header_upstream_is_checked_too(self):
        """头里的地址也要过白名单,不能因为是内部组件送来的就免检。"""
        with shim_only(upstream="https://api.deepseek.com/v1") as c:
            status, _ = post_chat(c, {"model": "m", "messages": []},
                                  headers={"X-Hunter-Upstream": "file:///etc/passwd"})
        self.assertEqual(status, 400)


class SchemaCleanTests(unittest.TestCase):
    """清洗规则抽成 schema_clean.py 之后行为不变。"""

    def test_strips_unsupported_keywords(self):
        body = json.dumps({"model": "m", "tools": [{"type": "function", "function": {
            "name": "f",
            "parameters": {"$schema": "http://json-schema.org/draft-07/schema#",
                           "type": "object",
                           "additionalProperties": False,
                           "properties": {"a": {"type": "array"}}},
        }}]}).encode()
        out = json.loads(shim.sanitize_body(body))
        params = out["tools"][0]["function"]["parameters"]
        self.assertNotIn("$schema", params)
        self.assertNotIn("additionalProperties", params)
        self.assertEqual(params["properties"]["a"]["items"], {"type": "string"})

    def test_null_schema_becomes_object(self):
        self.assertEqual(shim.ensure_object_schema({"type": "null"}),
                         {"type": "object", "properties": {}})
        self.assertEqual(shim.ensure_object_schema(None),
                         {"type": "object", "properties": {}})


if __name__ == "__main__":
    unittest.main()
