"""向导第 3 步三项检测的单元测试(M2 · 设计方案 4.5)。

**用一个真的 HTTP 服务器当假上游**(标准库 http.server,不联网),不是 monkeypatch
httpx —— 要测的恰恰是"真发一个请求出去会怎样",打桩掉传输层就把要测的东西测没了。

覆盖设计方案 4.5 表里的全部失败分类:
  连通:连不上 / DNS / 401 / 404(网关没实现 /models,**算通过**)
  对话:模型名不对(并列出可用模型)/ 空回复 / 402 余额 / 429 限流 / <think> 泄漏
  工具:直接通过 / 脏 schema 被拒→清洗后通过(建议 sanitize=1)/ 不调工具 / 调错工具
"""
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "unit-test-secret-for-llm-probe-0123456789")
# 清洗函数的位置:镜像里在 /opt/hunter-shim/,仓库里在 scripts/llm-shim/。
# 容器里挂源码跑测试时两者都可能,所以逐级往上找,**不写死层数**。
if not os.environ.get("HUNTER_SCHEMA_CLEAN"):
    for _up in Path(__file__).resolve().parents:
        _c = _up / "scripts" / "llm-shim" / "schema_clean.py"
        if _c.is_file():
            os.environ["HUNTER_SCHEMA_CLEAN"] = str(_c)
            break

from app.services import llm_probe as P  # noqa: E402


# ── 假上游 ──────────────────────────────────────────────────────────
class Fake:
    """behaviour 决定这台假网关怎么回应。"""

    def __init__(self, behaviour: dict):
        self.behaviour = behaviour
        self.requests: list = []
        b = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):        # 不往 stderr 刷日志
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
                    r = b.behaviour.get("models", ("ok", None))
                    if r[0] == "401":
                        return self._send(401, {"error": {"message": "invalid api key"}})
                    if r[0] == "404":
                        return self._send(404, {"error": {"message": "not found"}})
                    return self._send(200, {"data": [{"id": m} for m in (r[1] or ["m-1", "m-2"])]})
                self._send(404, {})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                b.requests.append(body)
                if body.get("tools"):
                    return self._tool(body)
                return self._chat(body)

            def _chat(self, body):
                mode = b.behaviour.get("chat", "ok")
                if mode == "bad_model":
                    return self._send(404, {"error": {"message": "model_not_found: nope"}})
                if mode == "model_403_cn":
                    # 演示站那台 OneAPI 网关的真实措辞(2026-09-18 实测):
                    # 模型名写错回的是 403 + 中文,先按状态码判会得到「key 无效」
                    return self._send(403, {"error": {"message":
                        f"该令牌无权使用模型：{body.get('model')}"}})
                if mode == "empty":
                    return self._send(200, {"choices": [{"message": {"content": ""}}]})
                if mode == "empty_length":
                    # 正文被 max_tokens 截断（推理模型的 reasoning 先吃光预算）
                    return self._send(200, {"choices": [{"finish_reason": "length", "message": {
                        "content": "",
                        "reasoning_content": "The user is asking me to reply with only two characters..."}}]})
                if mode == "empty_reasoning":
                    # 模型正常结束，但只把内容写在 reasoning 字段里，正文为空
                    return self._send(200, {"choices": [{"finish_reason": "stop", "message": {
                        "content": "", "reasoning_content": "让我想想……"}}]})
                if mode == "no_balance":
                    return self._send(402, {"error": {"message": "Insufficient Balance"}})
                if mode == "rate":
                    return self._send(429, {"error": {"message": "too many requests"}})
                if mode == "think":
                    return self._send(200, {"choices": [{"message": {
                        "content": "<think>让我想想</think>收到"}}]})
                return self._send(200, {"choices": [{"message": {"content": "收到"}}]})

            def _tool(self, body):
                mode = b.behaviour.get("tools", "ok")
                params = body["tools"][0]["function"]["parameters"]
                dirty = "$schema" in params or "additionalProperties" in params
                if mode == "reject_dirty" and dirty:
                    return self._send(400, {"error": {"message":
                        'Invalid JSON payload received. Unknown name "$schema"'}})
                if mode == "reject_always":
                    return self._send(400, {"error": {"message":
                        'Invalid JSON payload received. Unknown name "$schema"'}})
                if mode == "no_tool":
                    return self._send(200, {"choices": [{"message": {"content": "茅台大概 1200 元"}}]})
                if mode == "wrong_tool":
                    return self._send(200, {"choices": [{"message": {"tool_calls": [
                        {"function": {"name": "some_other_tool", "arguments": "{}"}}]}}]})
                return self._send(200, {"choices": [{"message": {"tool_calls": [
                    {"function": {"name": P.PROBE_TOOL_NAME, "arguments": '{"code":"600519"}'}}]}}]})

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.t.start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}/v1"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def fake():
    made = []

    def _mk(behaviour=None):
        f = Fake(behaviour or {})
        made.append(f)
        return f
    yield _mk
    for f in made:
        f.close()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ── 前提:清洗函数确实加载得到 ──────────────────────────────────────
def test_schema_clean_loaded():
    """加载不到的话第 3 项就退化成"脏 schema 被拒就判失败",
    表现是 DeepSeek 这类模型永远过不了检测。"""
    assert P.schema_clean() is not None


# ── ① 连通 ──────────────────────────────────────────────────────────
def test_reach_ok(fake):
    f = fake({"models": ("ok", ["deepseek-v4-pro", "x"])})
    r = P.check_reach(f.base, "sk-1")
    assert r["ok"] and "deepseek-v4-pro" in r["models"] and r["elapsed_ms"] >= 0


def test_reach_bad_key(fake):
    r = P.check_reach(fake({"models": ("401", None)}).base, "sk-bad")
    assert not r["ok"] and r["code"] == "bad_key"


def test_reach_404_is_not_a_failure(fake):
    """不少网关不实现 /models —— 那不是失败,只是拿不到模型列表。"""
    r = P.check_reach(fake({"models": ("404", None)}).base, "sk-1")
    assert r["ok"] and r["code"] == "no_models_api" and r["models"] == []


def test_reach_refused():
    r = P.check_reach(f"http://127.0.0.1:{_free_port()}/v1", "sk-1")
    assert not r["ok"] and r["code"] in ("refused", "network", "timeout")


def test_reach_dns_failure():
    r = P.check_reach("http://this-host-does-not-exist.invalid/v1", "sk-1")
    assert not r["ok"] and r["code"] in ("dns", "network")


# ── ② 对话 ──────────────────────────────────────────────────────────
def test_chat_ok(fake):
    r = P.check_chat(fake().base, "sk-1", "m-1", [])
    assert r["ok"] and "收到" in r["message"] and not r["warn"]


def test_chat_bad_model_lists_available(fake):
    r = P.check_chat(fake({"chat": "bad_model"}).base, "sk-1", "m-9", ["m-1", "m-2"])
    assert not r["ok"] and r["code"] == "bad_model"
    assert "m-1" in r["message"]          # 报错里要给出可用模型


def test_chat_model_error_wins_over_403(fake):
    """网关用 403 报"模型名不对"时,必须按模型名报,不能报成 key 无效 ——
    用户会拿着一把好 key 去重新申请,而真正要改的是模型名。"""
    r = P.check_chat(fake({"chat": "model_403_cn"}).base, "sk-1", "gemini-9.9-nope", ["m-1"])
    assert not r["ok"] and r["code"] == "bad_model"
    assert "m-1" in r["message"]


def test_chat_real_bad_key_still_bad_key(fake):
    """报错里没提模型 → 仍然按 key 无效报(别把上一条修过头)。"""
    r = P.check_chat(fake({"chat": "no_balance"}).base, "sk-1", "m-1", [])
    assert r["code"] == "no_balance"


def test_chat_empty_reply(fake):
    r = P.check_chat(fake({"chat": "empty"}).base, "sk-1", "m-1", [])
    assert not r["ok"] and r["code"] == "empty"


def test_chat_truncated_by_max_tokens_passes(fake):
    """正文为空但 finish_reason=length = 被探针自己的预算截断（推理模型的
    reasoning token 也计入 max_tokens），责任在探针，不能据此判配置坏。"""
    r = P.check_chat(fake({"chat": "empty_length"}).base, "sk-1", "m-1", [])
    assert r["ok"], r
    assert r["warn"]


def test_chat_empty_reply_without_truncation_still_fails(fake):
    """模型正常结束（stop）却没有正文 = 上游没产出内容（比如只写在 reasoning
    字段里），必须判失败 —— opencode 读的是 content，这种配置就是空回复。"""
    r = P.check_chat(fake({"chat": "empty_reasoning"}).base, "sk-1", "m-1", [])
    assert not r["ok"] and r["code"] == "empty"


def test_chat_no_balance(fake):
    r = P.check_chat(fake({"chat": "no_balance"}).base, "sk-1", "m-1", [])
    assert not r["ok"] and r["code"] == "no_balance"


def test_chat_rate_limited(fake):
    r = P.check_chat(fake({"chat": "rate"}).base, "sk-1", "m-1", [])
    assert not r["ok"] and r["code"] == "rate_limited"


def test_chat_think_leak_warns_but_passes(fake):
    r = P.check_chat(fake({"chat": "think"}).base, "sk-1", "m-1", [])
    assert r["ok"] and r["warn"]


def test_chat_sends_exactly_one_user_message(fake):
    """设计方案 4.5:只发一条 user 消息(部分 Gemini 版本对 system+user 返 400)。"""
    f = fake()
    P.check_chat(f.base, "sk-1", "m-1", [])
    msgs = f.requests[-1]["messages"]
    assert len(msgs) == 1 and msgs[0]["role"] == "user"


# ── ③ 工具 ──────────────────────────────────────────────────────────
def test_tools_pass_without_cleaning(fake):
    f = fake({"tools": "ok"})
    r = P.check_tools(f.base, "sk-1", "m-1")
    assert r["ok"] and r["sanitize_suggest"] == "auto" and r["cleaned"] is False
    # 第一发必须是**脏** schema —— 拿干净 schema 去测等于没测
    assert "$schema" in f.requests[0]["tools"][0]["function"]["parameters"]


def test_tools_dirty_rejected_then_clean_passes(fake):
    f = fake({"tools": "reject_dirty"})
    r = P.check_tools(f.base, "sk-1", "m-1")
    assert r["ok"] and r["sanitize_suggest"] == "1" and r["cleaned"] is True
    cleaned = f.requests[-1]["tools"][0]["function"]["parameters"]
    assert "$schema" not in cleaned and "additionalProperties" not in cleaned


def test_tools_reject_always(fake):
    r = P.check_tools(fake({"tools": "reject_always"}).base, "sk-1", "m-1")
    assert not r["ok"] and r["code"] == "schema_reject"


def test_tools_model_does_not_call(fake):
    r = P.check_tools(fake({"tools": "no_tool"}).base, "sk-1", "m-1")
    assert not r["ok"] and r["code"] == "no_tool_call"
    assert "工具调用" in r["message"] or "换一个" in r["message"]


def test_tools_wrong_tool_name(fake):
    r = P.check_tools(fake({"tools": "wrong_tool"}).base, "sk-1", "m-1")
    assert not r["ok"] and r["code"] == "wrong_tool"


# ── 串起来 ──────────────────────────────────────────────────────────
def test_run_all_ok(fake):
    r = P.run_all(fake().base, "sk-1", "m-1")
    assert r["ok"] and len(r["checks"]) == 3 and r["sanitize_suggest"] == "auto"
    assert all(c["elapsed_ms"] >= 0 for c in r["checks"])


def test_run_all_stops_at_first_failure(fake):
    """连不上还去发对话请求,只会让用户等三份超时、拿到三条互相矛盾的报错。"""
    r = P.run_all(fake({"models": ("401", None)}).base, "sk-bad", "m-1")
    assert not r["ok"] and len(r["checks"]) == 1


def test_run_all_stops_after_chat_failure(fake):
    r = P.run_all(fake({"chat": "bad_model"}).base, "sk-1", "m-9")
    assert not r["ok"] and len(r["checks"]) == 2


def test_run_all_result_carries_no_key(fake):
    """返回体会原样进浏览器 —— 里面不能出现 key。"""
    r = P.run_all(fake().base, "sk-super-secret-value", "m-1")
    assert "sk-super-secret-value" not in json.dumps(r, ensure_ascii=False)
