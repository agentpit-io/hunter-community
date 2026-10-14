"""`/api/setup/*` 路由的单元测试(M2 · 设计方案 4.8)。

只挂 setup 这一个 router 到一个干净的 FastAPI app 上,**不起整个 main.py**
(那会连库、起调度器)。`runtime_config` 的读写全部打桩成内存字典。

重点覆盖门禁 —— 这个前缀在 middleware/auth.py 里是免 JWT 的公开前缀,
每个 handler 自己的 `_guard` 就是唯一那道门。漏一个就是「公网上谁都能改配置」。
"""
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "unit-test-secret-for-setup-router-0123456789")

from app.routers import setup as S              # noqa: E402
from app.services import llm_probe as P         # noqa: E402
from app.services import llm_trace as LT        # noqa: E402
from app.services import opencode_admin as OA   # noqa: E402
from app.services import runtime_config as RC   # noqa: E402
from app.services import setup_guard as G       # noqa: E402


# ── 内存版 runtime_config ───────────────────────────────────────────
class MemCfg:
    def __init__(self, base_url="", api_key="", model="", sanitize="auto", source="none"):
        self.base_url, self.api_key, self.model = base_url, api_key, model
        self.sanitize, self.source = sanitize, source
        self.configured = bool(base_url and api_key and model)


@pytest.fixture
def app(monkeypatch):
    G.reset_state()
    state = {"cfg": MemCfg(), "kv": {}, "env_locked": False,
             "item_source": {"base_url": "none", "api_key": "none", "model": "none"},
             "saved": [], "applied": [], "ready": False,
             "builtin": False, "agent_models": {}}

    monkeypatch.setattr(RC, "llm", lambda: state["cfg"])
    monkeypatch.setattr(RC, "env_locked", lambda: state["env_locked"])
    monkeypatch.setattr(RC, "source", lambda k: state["item_source"][k.replace("llm.", "")])
    monkeypatch.setattr(RC, "get_str", lambda k: state["kv"].get(k, ""))
    monkeypatch.setattr(RC, "set_str", lambda k, v: state["kv"].__setitem__(k, v))

    def _save(cfg, tested_token=None):
        state["saved"].append({"base_url": cfg.base_url, "model": cfg.model,
                               "sanitize": cfg.sanitize, "api_key": cfg.api_key})
        state["cfg"] = MemCfg(cfg.base_url, cfg.api_key, cfg.model, cfg.sanitize, "db")
    monkeypatch.setattr(RC, "save_llm", _save)

    # 内置额度标记与 agent 侧模型名(P2)。真实实现会写库,这里记在内存里。
    def _save_builtin(on, models=None):
        state["builtin"] = bool(on)
        state["agent_models"] = dict(models or {}) if on else {}
    monkeypatch.setattr(RC, "save_builtin", _save_builtin)
    monkeypatch.setattr(RC, "builtin", lambda: state["builtin"])

    from app.services import opencode_admin as OA
    monkeypatch.setattr(OA, "apply_llm", lambda cfg: (state["applied"].append(cfg.model)
                                                      or {"ok": True, "reason": ""}))
    monkeypatch.setattr(S, "_probe_ready",
                        lambda model: {"ready": state["ready"], "reason": "", "current": model})
    monkeypatch.setattr(S, "_hunter_key_state",
                        lambda: {"configured": False, "masked": "", "env_locked": False,
                                 "apply_url": "https://example.invalid"})

    a = FastAPI()

    @a.middleware("http")
    async def _identity(request: Request, call_next):
        """模拟 middleware/auth.py 的可选身份识别。"""
        request.state.user_id = request.headers.get("X-Test-Uid") or None
        request.state.user_role = request.headers.get("X-Test-Role") or None
        return await call_next(request)

    a.include_router(S.router, prefix="/api")
    a.state.mem = state
    return a


@pytest.fixture
def client(app):
    # ⚠️ 对端地址必须是内网的:`source_of` 只有在直接对端是内网时才采信转发头
    # (对端是公网 = 有人绕开 web 直接打 api,他给的头一个字都不能信)。
    # TestClient 默认对端是 "testclient",会被判成 unknown → 转发头全部失效,
    # 于是每个用例都得到 401,而原因和被测逻辑毫无关系。
    return TestClient(app, client=("172.18.0.7", 50000))


LOCAL = {"X-Hunter-Forwarded-For": "127.0.0.1"}
PUBLIC = {"X-Hunter-Forwarded-For": "8.8.8.8"}


# ── 门禁 ────────────────────────────────────────────────────────────
def test_local_without_token_is_allowed(client, monkeypatch):
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    r = client.get("/api/setup/status", headers=LOCAL)
    assert r.status_code == 200 and r.json()["authed"] is True


def test_public_without_token_is_refused(client, monkeypatch):
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    r = client.get("/api/setup/status", headers=PUBLIC)
    d = r.json()
    assert r.status_code == 200 and d["authed"] is False
    assert d["need_unlock"] and d["unlock_reason"] == "no_token_public"


def test_token_configured_requires_unlock_even_from_localhost(client, monkeypatch):
    """**比设计方案 4.2 严**:设了口令就一律要,不管来源看起来是不是本机。
    来源判断依赖可伪造的转发头,而"有没有口令"伪造不了。"""
    monkeypatch.setenv("HUNTER_SETUP_TOKEN", "pw")
    d = client.get("/api/setup/status", headers=LOCAL).json()
    assert d["authed"] is False and d["unlock_reason"] == "token_required"


def test_unlock_then_allowed(client, monkeypatch):
    monkeypatch.setenv("HUNTER_SETUP_TOKEN", "pw")
    r = client.post("/api/setup/unlock", json={"token": "pw"}, headers=LOCAL)
    assert r.status_code == 200
    sess = r.json()["session"]
    d = client.get("/api/setup/status", headers={**LOCAL, "X-Hunter-Setup-Session": sess}).json()
    assert d["authed"] is True and d["via"] == "setup_session"


def test_unlock_wrong_then_locked(client, monkeypatch):
    monkeypatch.setenv("HUNTER_SETUP_TOKEN", "pw")
    for _ in range(G.MAX_UNLOCK_FAILS - 1):
        assert client.post("/api/setup/unlock", json={"token": "x"}, headers=PUBLIC).status_code == 400
    r = client.post("/api/setup/unlock", json={"token": "x"}, headers=PUBLIC)
    assert r.status_code == 423                      # Locked
    assert r.json()["detail"]["locked_for"] == G.LOCK_SECONDS
    # 锁定期间正确口令也不放行
    assert client.post("/api/setup/unlock", json={"token": "pw"}, headers=PUBLIC).status_code == 423


def test_unlock_without_token_configured(client, monkeypatch):
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    r = client.post("/api/setup/unlock", json={"token": "anything"}, headers=PUBLIC)
    assert r.status_code == 409


def test_admin_jwt_bypasses_unlock(client, monkeypatch):
    monkeypatch.setenv("HUNTER_SETUP_TOKEN", "pw")
    d = client.get("/api/setup/status", headers={**PUBLIC, "X-Test-Role": "admin",
                                                 "X-Test-Uid": "u1"}).json()
    assert d["authed"] and d["via"] == "admin"


def test_every_write_endpoint_is_guarded(client, monkeypatch):
    """公开前缀下**每一个** handler 都必须自己鉴权。新增接口要加进这张表。"""
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    calls = [
        ("get", "/api/setup/env-check", None),
        ("get", "/api/setup/llm-presets", None),
        ("post", "/api/setup/llm/test", {"base_url": "https://x/v1", "api_key": "k", "model": "m"}),
        ("put", "/api/setup/llm", {"base_url": "https://x/v1", "api_key": "k",
                                   "model": "m", "test_token": "t"}),
        ("post", "/api/setup/apply", {}),
        ("get", "/api/setup/engine-ready", None),
        ("post", "/api/setup/complete", {"skipped": False}),
        ("post", "/api/setup/reopen", {}),
    ]
    for method, path, body in calls:
        fn = getattr(client, method)
        r = fn(path, json=body, headers=PUBLIC) if body is not None else fn(path, headers=PUBLIC)
        assert r.status_code == 401, f"{method.upper()} {path} 没有鉴权!"
        assert r.json()["detail"]["need_unlock"] is True


# ── status ──────────────────────────────────────────────────────────
def test_status_never_returns_the_key(client, monkeypatch):
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    client.app.state.mem["cfg"] = MemCfg("https://x/v1", "sk-super-secret", "m", "auto", "db")
    body = client.get("/api/setup/status", headers=LOCAL).text
    assert "sk-super-secret" not in body
    assert "****cret" in body          # 只给打码值


def test_status_should_run(client, monkeypatch):
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    mem = client.app.state.mem
    assert client.get("/api/setup/status", headers=LOCAL).json()["setup"]["should_run"] is True
    mem["cfg"] = MemCfg("https://x/v1", "k", "m", "auto", "db")
    assert client.get("/api/setup/status", headers=LOCAL).json()["setup"]["should_run"] is False
    # 「稍后配置」之后也不该再被弹回向导
    mem["cfg"] = MemCfg()
    mem["kv"][RC.K_SETUP_DONE] = "123"
    assert client.get("/api/setup/status", headers=LOCAL).json()["setup"]["should_run"] is False


# ── 保存 ────────────────────────────────────────────────────────────
def _ok_token(base="https://x/v1", model="m", key="k", sanitize_suggest=""):
    return G.issue_test_token(base, model, key, sanitize_suggest)


def test_save_uses_sanitize_suggest_from_token(client, monkeypatch):
    """检测说「这个模型必须开清洗」时,调用方不传 sanitize 也要按建议存。

    网页端会把建议回传,直接调接口的人不会 —— 原来这种情况下会落到默认 auto,
    而 auto 只对名字里含 gemini 的模型开清洗,于是检测报文承诺的「保存时会自动
    设为开」对其他模型就是假的,症状是回复空白。
    """
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    body = {"base_url": "https://x/v1", "api_key": "k", "model": "m",
            "test_token": _ok_token(sanitize_suggest="1")}
    r = client.put("/api/setup/llm", json=body, headers=LOCAL)
    assert r.status_code == 200 and r.json()["saved"]["sanitize"] == "1"
    assert client.app.state.mem["saved"][-1]["sanitize"] == "1"

    # 调用方明确指定时以它为准
    body2 = dict(body, sanitize="0", test_token=_ok_token(sanitize_suggest="1"))
    assert client.put("/api/setup/llm", json=body2,
                      headers=LOCAL).json()["saved"]["sanitize"] == "0"

    # 没有建议就退回默认
    body3 = dict(body, test_token=_ok_token())
    assert client.put("/api/setup/llm", json=body3,
                      headers=LOCAL).json()["saved"]["sanitize"] == RC.SANITIZE_DEFAULT


def test_save_requires_valid_test_token(client, monkeypatch):
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    body = {"base_url": "https://x/v1", "api_key": "k", "model": "m", "test_token": ""}
    assert client.put("/api/setup/llm", json=body, headers=LOCAL).status_code == 400
    body["test_token"] = "forged.999999999999.deadbeef"
    assert client.put("/api/setup/llm", json=body, headers=LOCAL).status_code == 400
    body["test_token"] = _ok_token()
    r = client.put("/api/setup/llm", json=body, headers=LOCAL)
    assert r.status_code == 200 and client.app.state.mem["saved"][-1]["model"] == "m"


def test_save_rejects_token_for_other_config(client, monkeypatch):
    """测的是 A、存的是 B —— 必须拒。"""
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    body = {"base_url": "https://x/v1", "api_key": "k", "model": "OTHER",
            "test_token": _ok_token()}
    r = client.put("/api/setup/llm", json=body, headers=LOCAL)
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "mismatch"


def test_save_409_when_env_locked(client, monkeypatch):
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    mem = client.app.state.mem
    mem["env_locked"] = True
    mem["item_source"] = {"base_url": "env", "api_key": "env", "model": "env"}
    r = client.put("/api/setup/llm", json={"base_url": "https://x/v1", "api_key": "k",
                                           "model": "m", "test_token": _ok_token()},
                   headers=LOCAL)
    assert r.status_code == 409
    assert "LLM_BASE_URL" in r.json()["detail"]["message"]
    assert mem["saved"] == []          # 一个字都不许写进去


def test_save_rejects_bad_sanitize(client, monkeypatch):
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    r = client.put("/api/setup/llm", json={"base_url": "https://x/v1", "api_key": "k",
                                           "model": "m", "sanitize": "yes-please",
                                           "test_token": _ok_token()}, headers=LOCAL)
    assert r.status_code == 400


# ── 检测接口 ────────────────────────────────────────────────────────
def test_test_endpoint_validates_input(client, monkeypatch):
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    r = client.post("/api/setup/llm/test",
                    json={"base_url": "ftp://x", "api_key": "k", "model": "m"}, headers=LOCAL)
    assert r.status_code == 400
    r = client.post("/api/setup/llm/test",
                    json={"base_url": "https://x/v1", "api_key": "", "model": "m"}, headers=LOCAL)
    assert r.status_code == 400          # 库里也没有 key


def test_test_endpoint_rate_limited(client, monkeypatch):
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    from app.services import llm_probe as P
    monkeypatch.setattr(P, "run_all", lambda *a: {"ok": False, "checks": [], "models": [],
                                                  "sanitize_suggest": "", "elapsed_ms": 1})
    body = {"base_url": "https://x/v1", "api_key": "k", "model": "m"}
    for _ in range(G.TEST_RATE_PER_MIN):
        assert client.post("/api/setup/llm/test", json=body, headers=LOCAL).status_code == 200
    assert client.post("/api/setup/llm/test", json=body, headers=LOCAL).status_code == 429


def test_test_endpoint_reuses_stored_key(client, monkeypatch):
    """只改模型名、不重填 key —— 已保存的 key 不回显给前端,得能接着用。"""
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    client.app.state.mem["cfg"] = MemCfg("https://x/v1", "stored-key", "m", "auto", "db")
    seen = {}
    from app.services import llm_probe as P
    monkeypatch.setattr(P, "run_all", lambda b, k, m: (seen.update(key=k) or
                        {"ok": True, "checks": [], "models": [], "sanitize_suggest": "auto",
                         "elapsed_ms": 1}))
    r = client.post("/api/setup/llm/test",
                    json={"base_url": "https://x/v1", "api_key": "", "model": "m2"}, headers=LOCAL)
    assert r.status_code == 200 and seen["key"] == "stored-key"
    # 返回的 test_token 必须绑在真正用到的那把 key 上,否则保存时对不上
    assert G.verify_test_token(r.json()["test_token"], "https://x/v1", "m2", "stored-key")["ok"]


def test_no_test_token_when_failed(client, monkeypatch):
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    from app.services import llm_probe as P
    monkeypatch.setattr(P, "run_all", lambda *a: {"ok": False, "checks": [], "models": [],
                                                  "sanitize_suggest": "", "elapsed_ms": 1})
    r = client.post("/api/setup/llm/test",
                    json={"base_url": "https://x/v1", "api_key": "k", "model": "m"}, headers=LOCAL)
    assert r.json()["test_token"] == ""


# ── apply / ready / complete / reopen ──────────────────────────────
def test_apply_requires_configured(client, monkeypatch):
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    assert client.post("/api/setup/apply", json={}, headers=LOCAL).status_code == 400
    client.app.state.mem["cfg"] = MemCfg("https://x/v1", "k", "m", "auto", "db")
    r = client.post("/api/setup/apply", json={}, headers=LOCAL)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.app.state.mem["applied"] == ["m"]


def test_engine_ready(client, monkeypatch):
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    mem = client.app.state.mem
    assert client.get("/api/setup/engine-ready", headers=LOCAL).json()["ready"] is False
    mem["cfg"] = MemCfg("https://x/v1", "k", "m", "auto", "db")
    mem["ready"] = True
    assert client.get("/api/setup/engine-ready", headers=LOCAL).json()["ready"] is True


def test_complete_and_reopen(client, monkeypatch):
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    mem = client.app.state.mem
    client.post("/api/setup/complete", json={"skipped": True}, headers=LOCAL)
    assert mem["kv"][RC.K_SETUP_DONE] and mem["kv"]["setup.skipped"] == "1"
    client.post("/api/setup/reopen", json={}, headers=LOCAL)
    assert mem["kv"][RC.K_SETUP_DONE] == "" and mem["kv"]["setup.skipped"] == ""


def test_reopen_does_not_wipe_config(client, monkeypatch):
    """换模型的人还要在向导里看到当前值 —— 清掉配置只会让他重新找一遍 key。"""
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    mem = client.app.state.mem
    mem["cfg"] = MemCfg("https://x/v1", "k", "m", "auto", "db")
    client.post("/api/setup/reopen", json={}, headers=LOCAL)
    assert mem["cfg"].configured and mem["saved"] == []


# ── v2.2.1 谷歌 4.0 展示名兜底与大模型调用日志跟踪 ──────────────────────
def test_opencode_admin_model_label_google4_fallback(monkeypatch):
    """内置网关下即使网络抖动或返回不含谷歌 4.0 字样，也应返回 Hunter-Gemini 4 Argon（谷歌 4.0）。"""
    import httpx

    def _fail_get(*args, **kwargs):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(httpx, "get", _fail_get)
    label = OA.model_label("https://hunter.agentpit.io/api/saas/llm/v1", "hunt_tools_xxx", "hunter-chat")
    assert label == "Hunter-Gemini 4 Argon（谷歌 4.0）"


def test_llm_logs_and_download_never_leak_api_key(client, monkeypatch, tmp_path):
    """诊断日志与下载接口必须返回调用跟踪记录且绝不泄露明文 API Key。"""
    import httpx

    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    trace_file = tmp_path / "trace.jsonl"
    monkeypatch.setenv("HUNTER_LLM_TRACE_PATH", str(trace_file))
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: (_ for _ in ()).throw(httpx.ConnectTimeout("offline")))

    secret_key = "sk-super-secret-never-leak-9999"
    client.app.state.mem["cfg"] = MemCfg(
        "https://hunter.agentpit.io/api/saas/llm/v1",
        secret_key,
        "hunter-chat",
        "0",
        "db",
    )

    LT.record_call(
        caller="unit.test",
        base_url="https://user:pass@hunter.agentpit.io/api/saas/llm/v1?key=abc",
        req_model="hunter-chat",
        resp_model="gemini-4-argon",
        status=200,
        latency_ms=123,
        tokens_in=10,
        tokens_out=20,
    )

    r = client.get("/api/setup/llm/logs", headers=LOCAL)
    assert r.status_code == 200
    body = r.json()
    assert secret_key not in r.text
    assert body["llm_config"]["display_name"] == "Hunter-Gemini 4 Argon（谷歌 4.0）"
    assert len(body["api_traces"]) >= 1
    assert body["api_traces"][0]["resp_model"] == "gemini-4-argon"

    r_dl = client.get("/api/setup/llm/logs/download", headers=LOCAL)
    assert r_dl.status_code == 200
    assert "attachment; filename=\"hunter-llm-diagnostics-" in r_dl.headers.get("content-disposition", "")
    assert secret_key not in r_dl.text
    assert "gemini-4-argon" in r_dl.text

