"""guard.py 的身份反查（M3）：`session_id` → hermes user_id。

为什么单独一个文件：这组用例要起一个**真的 HTTP 服务**冒充 hermes-api 的
`GET /api/internal/session/{sid}/user`，和 test_guard_hook.py 那种纯子进程的
跑法不是一回事。

测的是网页形态下最要紧的一条：**不同用户不能共用一份持仓账本**。
M2 用的是容器级 `HUNTER_USER_ID`，那是单用户评测环境的简化；
网页上线后继续用它就是数据串户。

    python3 -m pytest tools/tests/test_guard_session_identity.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GUARD = REPO / "distro" / "workspace-template" / ".hooks" / "guard.py"

INTERNAL_KEY = "test-internal-key"
# session_id → user_id，冒充 chat_session_owner 表
OWNERS = {"sess-alice": "u-alice", "sess-bob": "u-bob"}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802  BaseHTTPRequestHandler 的约定
        self.server.hits.append(self.path)  # type: ignore[attr-defined]
        if self.headers.get("X-Hunter-Internal-Key") != INTERNAL_KEY:
            return self._send(401, {"detail": "internal auth failed"})
        prefix, suffix = "/api/internal/session/", "/user"
        if not (self.path.startswith(prefix) and self.path.endswith(suffix)):
            return self._send(404, {"detail": "no route"})
        sid = self.path[len(prefix):-len(suffix)]
        uid = OWNERS.get(sid)
        if not uid:
            return self._send(404, {"detail": "无归属记录"})
        self._send(200, {"session_id": sid, "user_id": uid})

    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):  # 别把请求日志刷进 pytest 输出
        pass


@pytest.fixture
def api():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    srv.hits = []  # type: ignore[attr-defined]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


def run_guard(session_id: str, tool: str, tool_input: dict, workspace: Path, env_extra: dict):
    env = dict(os.environ)
    env["HCA_WORKSPACE"] = str(workspace)
    # 显式清掉，免得开发机上恰好设了这几个变量让用例失真
    env.pop("HERMES_API_URL", None)
    env.pop("HUNTER_INTERNAL_KEY", None)
    env.pop("HUNTER_USER_ID", None)
    env.update(env_extra)
    payload = json.dumps({
        "session_id": session_id, "hook_event_name": "PreToolUse",
        "tool_name": tool, "tool_input": tool_input, "cwd": str(workspace),
    }, ensure_ascii=False)
    p = subprocess.run([sys.executable, str(GUARD)], input=payload, env=env,
                       capture_output=True, text=True, timeout=30)
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    return (json.loads(lines[-1]) if lines else None), p.returncode


def injected(res):
    return ((res or {}).get("hookSpecificOutput", {}) or {}).get("updatedInput")


def base_env(api):
    return {
        "HERMES_API_URL": f"http://127.0.0.1:{api.server_port}",
        "HUNTER_INTERNAL_KEY": INTERNAL_KEY,
    }


# ── 主路径 ──────────────────────────────────────────────────────────────────

def test_identity_comes_from_the_session_not_the_container(api, tmp_path):
    """同一台 daemon、两个会话，注进去的必须是各自的用户。"""
    env = base_env(api)
    a, _ = run_guard("sess-alice", "mcp__uzi__stock_deep_analysis", {"code": "600519"}, tmp_path, env)
    b, _ = run_guard("sess-bob", "mcp__portfolio__portfolio_rebalance", {}, tmp_path / "b", env)
    assert injected(a) == {"code": "600519", "_hermes_user_id": "u-alice"}
    assert injected(b) == {"_hermes_user_id": "u-bob"}


def test_session_lookup_beats_the_env_fallback(api, tmp_path):
    """两个来源都有时以会话为准 —— 否则多用户下所有人都会拿到那个常量。"""
    env = {**base_env(api), "HUNTER_USER_ID": "u-容器常量"}
    res, _ = run_guard("sess-alice", "mcp__watchlist__stock_quickview", {"code": "600519"}, tmp_path, env)
    assert injected(res)["_hermes_user_id"] == "u-alice"


def test_rewrite_only_never_a_permission_decision(api, tmp_path):
    res, _ = run_guard("sess-alice", "mcp__uzi__stock_deep_analysis", {"code": "600519"}, tmp_path, base_env(api))
    assert "permissionDecision" not in (res or {}).get("hookSpecificOutput", {}), \
        "只改参数、不表态，否则每次调 MCP 都会多弹一次权限"


def test_second_call_hits_the_cache_not_the_api(api, tmp_path):
    env = base_env(api)
    for _ in range(3):
        run_guard("sess-alice", "mcp__uzi__stock_deep_analysis", {"code": "600519"}, tmp_path, env)
    lookups = [h for h in api.hits if h.startswith("/api/internal/session/")]
    assert len(lookups) == 1, f"三次调用只该查一次，实际 {len(lookups)} 次：{lookups}"
    cache = tmp_path / ".atomcode" / "session-user.json"
    assert json.loads(cache.read_text(encoding="utf-8"))["sess-alice"]["uid"] == "u-alice"


# ── 回落与失败 ──────────────────────────────────────────────────────────────

def test_falls_back_to_env_when_session_has_no_owner(api, tmp_path):
    """运维在容器里手工发起的会话没有归属记录 —— 单机形态照常工作。"""
    env = {**base_env(api), "HUNTER_USER_ID": "u-单机"}
    res, _ = run_guard("sess-没登记", "mcp__uzi__stock_deep_analysis", {"code": "600519"}, tmp_path, env)
    assert injected(res)["_hermes_user_id"] == "u-单机"


def test_no_owner_and_no_env_means_no_injection(api, tmp_path):
    """两条都没有就**不注入** —— 让下游 MCP 自己报缺身份，别默默用别人的账本。"""
    res, _ = run_guard("sess-没登记", "mcp__uzi__stock_deep_analysis", {"code": "600519"}, tmp_path, base_env(api))
    assert res is None


def test_api_unreachable_falls_back_and_does_not_block(tmp_path):
    """api 挂了不能把对话卡住：2 秒超时、回落环境变量、hook 照样 exit 0。"""
    env = {
        "HERMES_API_URL": "http://127.0.0.1:1",   # 必然连不上
        "HUNTER_INTERNAL_KEY": INTERNAL_KEY,
        "HUNTER_USER_ID": "u-兜底",
    }
    res, rc = run_guard("sess-alice", "mcp__uzi__stock_deep_analysis", {"code": "600519"}, tmp_path, env)
    assert rc == 0
    assert injected(res)["_hermes_user_id"] == "u-兜底"


def test_wrong_internal_key_does_not_inject_a_guess(api, tmp_path):
    """密钥不对时是 401，不该把它当成「查到了空」以外的任何东西。"""
    env = {**base_env(api), "HUNTER_INTERNAL_KEY": "wrong"}
    res, _ = run_guard("sess-alice", "mcp__uzi__stock_deep_analysis", {"code": "600519"}, tmp_path, env)
    assert res is None


def test_no_lookup_for_non_hunter_mcp(api, tmp_path):
    """akshare / kronos / truesource 不认 _hermes_user_id，连查都不该查。"""
    res, _ = run_guard("sess-alice", "mcp__akshare__akshare_call", {"func": "x"}, tmp_path, base_env(api))
    assert res is None
    assert [h for h in api.hits if h.startswith("/api/internal/session/")] == []


def test_模型自填的身份会被覆盖掉(api, tmp_path):
    """I2 加严：**无条件覆盖**模型自己填的 `_hermes_user_id`。

    原来的行为是「参数里已经有就不动」，而工具参数是**模型生成的** ——
    那等于模型填上别人的 UUID 就能读到别人的持仓。身份只能由这个 hook 决定。
    （这条用例原先断言的正是那个行为，是一个被测试钉住的越权口子。）
    """
    res, _ = run_guard("sess-alice", "mcp__uzi__stock_deep_analysis",
                       {"code": "600519", "_hermes_user_id": "别人的 UUID"},
                       tmp_path, base_env(api))
    assert res is not None, "模型自填的身份被原样放过去了"
    assert res["hookSpecificOutput"]["updatedInput"]["_hermes_user_id"] == "u-alice"


def test_查不到身份时摘掉模型自填的那一份(api, tmp_path):
    """查不到真实身份 + 模型自己填了一个 = 最危险的组合，必须摘掉而不是放行。"""
    env = base_env(api)
    env["HUNTER_USER_ID"] = ""
    res, _ = run_guard("sess-不存在的", "mcp__uzi__stock_deep_analysis",
                       {"code": "600519", "_hermes_user_id": "别人的 UUID"}, tmp_path, env)
    assert res is not None
    assert "_hermes_user_id" not in res["hookSpecificOutput"]["updatedInput"]


def test_guard_logs_the_identity_source(api, tmp_path):
    run_guard("sess-alice", "mcp__uzi__stock_deep_analysis", {"code": "600519"}, tmp_path, base_env(api))
    rec = json.loads((tmp_path / ".atomcode" / "guard.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert rec["decision"] == "rewrite"
    assert "session" in rec["reason"], f"审计里要看得出身份是哪来的：{rec}"


def test_fin_memory_and_doc_tools_receive_session_identity(api, tmp_path):
    """M2 新增的 fin_memory 与 watchlist 文档翻阅工具都必须由 guard 自动注入且覆盖 _hermes_user_id。"""
    env = base_env(api)
    m_res, _ = run_guard(
        "sess-alice",
        "mcp__fin_memory__fin_memory_query",
        {"project_id": "prj_1", "_hermes_user_id": "伪造身份"},
        tmp_path,
        env,
    )
    assert injected(m_res) == {"project_id": "prj_1", "_hermes_user_id": "u-alice"}

    f_res, _ = run_guard(
        "sess-bob",
        "mcp__watchlist__file_search_in_doc",
        {"file_id": "f_1", "keyword": "营收"},
        tmp_path / "b",
        env,
    )
    assert injected(f_res) == {"file_id": "f_1", "keyword": "营收", "_hermes_user_id": "u-bob"}


