"""Regression tests for SSE forwarding, using only loopback HTTP servers."""
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
    with patch.object(shim, "UPSTREAM", f"http://127.0.0.1:{upstream.server_port}"), \
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


if __name__ == "__main__":
    unittest.main()
