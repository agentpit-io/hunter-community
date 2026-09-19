"""内置额度路径的单元测试(P2 · 方案 4.8)。

不连库、不联网:`runtime_config` 的读写打桩成内存字典,`hunter_key` 的上游校验
打桩成一个假的 manifest。

盯的是五件最容易改坏的事:

  1. 内置额度路径下 `LLM_SCHEMA_SANITIZE` 必须是 **0**(清洗在网关做,方案 4.5)
  2. agent 侧那批模型名要**跟着一起写**(不写的话对话能用、深度分析是坏的 ——
     P1 在测试机上实测过这个组合),而且切回自带 key 时要**跟着一起清掉**
  3. `builtin=true` 只在地址真的是网关时才算数(别让别的地址顶着内置额度的名义写)
  4. 同一把 key 顺带解锁数据供给,但**失败不能影响大模型配置的保存**
  5. `/setup/llm/quota` 的门禁、形状,以及网关不可达时不许假装有额度
"""
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "unit-test-secret-for-builtin-quota-0123456789")

from app.routers import setup as S              # noqa: E402
from app.services import runtime_config as RC   # noqa: E402
from app.services import setup_guard as G       # noqa: E402

GW = RC.BUILTIN_BASE_URL
LOCAL = {"X-Hunter-Forwarded-For": "127.0.0.1"}
PUBLIC = {"X-Hunter-Forwarded-For": "8.8.8.8"}


class MemCfg:
    def __init__(self, base_url="", api_key="", model="", sanitize="auto", source="none"):
        self.base_url, self.api_key, self.model = base_url, api_key, model
        self.sanitize, self.source = sanitize, source
        self.configured = bool(base_url and api_key and model)


class FakeHunterKey:
    """够用的 hunter_key 替身 —— 只实现 setup.py 真正调的那几个。"""

    APPLY_URL = "https://example.invalid/dev/api-keys"

    def __init__(self):
        self.stored = ""
        self.env = False
        self.manifest_result = {"unlocked": True}
        self.save_raises = False

    def env_locked(self):
        return self.env

    def resolve(self):
        return self.stored

    def masked(self, k=None):
        k = self.stored if k is None else k
        return f"{k[:15]}****{k[-4:]}" if len(k) > 22 else "****"

    async def manifest(self, key=""):
        if isinstance(self.manifest_result, Exception):
            raise self.manifest_result
        return self.manifest_result

    def save(self, plain):
        if self.save_raises:
            raise RuntimeError("库挂了")
        self.stored = plain


@pytest.fixture
def app(monkeypatch):
    G.reset_state()
    monkeypatch.delenv("HUNTER_SETUP_TOKEN", raising=False)
    state = {"cfg": MemCfg(), "kv": {}, "env_locked": False, "saved": [],
             "builtin": False, "agent_models": {}, "quota_calls": [],
             "quota_result": {"builtin": True, "ok": True, "quota": {}}}
    hk = FakeHunterKey()

    monkeypatch.setattr(RC, "llm", lambda: state["cfg"])
    monkeypatch.setattr(RC, "env_locked", lambda: state["env_locked"])
    monkeypatch.setattr(RC, "source", lambda k: "none")
    monkeypatch.setattr(RC, "get_str", lambda k: state["kv"].get(k, ""))
    monkeypatch.setattr(RC, "set_str", lambda k, v: state["kv"].__setitem__(k, v))
    monkeypatch.setattr(RC, "builtin", lambda: state["builtin"])

    def _save(cfg, tested_token=None):
        state["saved"].append({"base_url": cfg.base_url, "model": cfg.model,
                               "sanitize": cfg.sanitize, "api_key": cfg.api_key})
        state["cfg"] = MemCfg(cfg.base_url, cfg.api_key, cfg.model, cfg.sanitize, "db")
    monkeypatch.setattr(RC, "save_llm", _save)

    def _save_builtin(on, models=None):
        state["builtin"] = bool(on)
        state["agent_models"] = dict(models or {}) if on else {}
    monkeypatch.setattr(RC, "save_builtin", _save_builtin)

    # setup.py 是在函数体里 `from app.services import hunter_key` 的,
    # 所以要把模块本身换掉,不能只 setattr 到 S 上。
    monkeypatch.setitem(sys.modules, "app.services.hunter_key", hk)
    # 光换 sys.modules 不够:同一进程里别的用例先 import 过真的 hunter_key 时(全量 pytest 里
    # tests/agent/test_mcp_tools.py 等),包 app.services 上已挂着这个属性,
    # `from app.services import hunter_key` 先取包属性、不查 sys.modules —— 拿到的是真模块。
    import app.services as _services
    monkeypatch.setattr(_services, "hunter_key", hk, raising=False)

    def _fetch(url, api_key):
        state["quota_calls"].append((url, api_key))
        r = state["quota_result"]
        return r() if callable(r) else r
    monkeypatch.setattr(S, "_fetch_quota", _fetch)

    a = FastAPI()

    @a.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = None
        request.state.user_role = None
        return await call_next(request)

    a.include_router(S.router, prefix="/api")
    a.state.mem = state
    a.state.hk = hk
    return a


@pytest.fixture
def client(app):
    return TestClient(app, client=("172.18.0.7", 50000))


def _token(base_url, model, api_key, sanitize_suggest=""):
    return G.issue_test_token(base_url, model, api_key, sanitize_suggest)


def _save(client, *, base_url=GW, model="hunter-chat", api_key="hunt_tools_abc",
          builtin=True, **extra):
    body = {"base_url": base_url, "api_key": api_key, "model": model,
            "builtin": builtin, "test_token": _token(base_url, model, api_key)}
    body.update(extra)
    return client.put("/api/setup/llm", json=body, headers=LOCAL)


# ── 1. sanitize 必须是 0 ────────────────────────────────────────────
def test_builtin_forces_sanitize_zero(client):
    """清洗在网关做。**别改成 auto** —— 现在 auto 恰好也不走 shim(模型名里没有
    gemini),但那是巧合,别名一改就悄悄变了。"""
    r = _save(client)
    assert r.status_code == 200
    assert r.json()["saved"]["sanitize"] == "0"
    assert client.app.state.mem["saved"][-1]["sanitize"] == "0"


def test_builtin_ignores_caller_sanitize(client):
    """调用方显式传 auto / 1 也压不过 —— 内置额度这条路上它不是用户的选择。"""
    for v in ("auto", "1"):
        assert _save(client, sanitize=v).json()["saved"]["sanitize"] == "0"


def test_non_builtin_keeps_old_sanitize_behaviour(client):
    """自带 key 的路径行为一字不变:没传就落默认。"""
    r = _save(client, base_url="https://api.deepseek.com/v1", model="deepseek-v4-pro",
              api_key="sk-x", builtin=False)
    assert r.json()["saved"]["sanitize"] == RC.SANITIZE_DEFAULT
    assert r.json()["saved"]["builtin"] is False


# ── 2. agent 侧模型名 ───────────────────────────────────────────────
def test_builtin_writes_agent_models(client):
    _save(client)
    m = client.app.state.mem["agent_models"]
    assert m["ASSISTANT_MODEL_CHAT"] == "hunter-chat"
    assert m["AGENT_MODEL_ROUTER"] == "hunter-chat"
    assert m["SIGNAL_ANALYSIS_MODEL"] == "hunter-chat"
    # 长任务走 deep —— 不写这两个,深度分析会拿空模型名打上游(P1 实测)
    assert m["AGENT_SUB_RESEARCH_MODEL"] == "hunter-deep"
    assert m["AGENT_SUB_UZI_MODEL"] == "hunter-deep"
    assert client.app.state.mem["builtin"] is True


def test_switching_back_to_own_key_clears_agent_models(client):
    """切回自带 key 必须清掉那批模型名,否则会拿着只有我们网关认识的
    `hunter-deep` 去打用户自己的上游,400 到底。"""
    _save(client)
    assert client.app.state.mem["agent_models"]
    _save(client, base_url="https://api.deepseek.com/v1", model="deepseek-v4-pro",
          api_key="sk-x", builtin=False)
    assert client.app.state.mem["agent_models"] == {}
    assert client.app.state.mem["builtin"] is False


def test_agent_models_cover_every_env_name_in_code():
    """`BUILTIN_AGENT_MODELS` 要覆盖代码里真正读的那些变量名。

    漏一个的表现是「对话正常、某一条分析链路悄悄用空模型名」—— 不报错、很难查。
    所以这里对着源码里 `agent_model("X", ...)` 的第一个参数比对。
    """
    import re
    root = Path(__file__).resolve().parents[1] / "app"
    used = set()
    for f in root.rglob("*.py"):
        for m in re.finditer(r'agent_model\(\s*"([A-Z0-9_]+)"', f.read_text(encoding="utf-8")):
            used.add(m.group(1))
    # ONE_API_* 是内部部署的一组独立变量,内置额度不接管它(没配 ONE_API_KEY 时
    # 那些调用点自己就跳过了)。
    used -= {"ONE_API_MODEL"}
    missing = used - set(RC.BUILTIN_AGENT_MODELS)
    assert not missing, f"这些变量代码里读了、内置额度却没给值:{sorted(missing)}"


# ── 3. builtin 只在地址真的是网关时才算数 ──────────────────────────
def test_builtin_flag_ignored_for_other_base_url(client):
    r = _save(client, base_url="https://evil.example/v1", model="hunter-chat", builtin=True)
    assert r.json()["saved"]["builtin"] is False
    assert client.app.state.mem["builtin"] is False
    assert client.app.state.mem["agent_models"] == {}
    # 地址不是网关 → 不该强制 0
    assert r.json()["saved"]["sanitize"] == RC.SANITIZE_DEFAULT


@pytest.mark.parametrize("url,expect", [
    (GW, True),
    (GW + "/", True),
    ("HTTPS://HUNTER.AGENTPIT.IO/API/SAAS/LLM/V1", True),
    ("https://my-hermes.example.com/api/saas/llm/v1", True),   # 自建同一套路由
    ("https://hunter.agentpit.io/api/saas/llm", False),
    ("https://api.deepseek.com/v1", False),
    ("", False),
])
def test_is_builtin_base(url, expect):
    assert RC.is_builtin_base(url) is expect


def test_builtin_quota_url():
    assert RC.builtin_quota_url(GW) == "https://hunter.agentpit.io/api/saas/llm/quota"
    assert RC.builtin_quota_url("https://api.deepseek.com/v1") == ""


# ── 4. 顺带解锁数据供给 ─────────────────────────────────────────────
def test_builtin_adopts_key_for_data_supply(client):
    r = _save(client)
    assert r.json()["data_supply"]["adopted"] is True
    assert client.app.state.hk.stored == "hunt_tools_abc"


def test_adopt_skipped_when_already_configured(client):
    client.app.state.hk.stored = "hunt_tools_old"
    r = _save(client)
    assert r.json()["data_supply"] == {"adopted": False, "reason": "already_configured"}
    assert client.app.state.hk.stored == "hunt_tools_old"   # 不覆盖用户已经配好的


def test_adopt_skipped_when_env_locked(client):
    client.app.state.hk.env = True
    assert _save(client).json()["data_supply"]["reason"] == "env_locked"


def test_adopt_skipped_for_non_platform_key(client):
    """网关只认 `hunt_tools_`,但万一以后放宽了,这里也不该把别的 key 存成平台 key。"""
    r = _save(client, api_key="sk-not-a-platform-key")
    assert r.json()["data_supply"]["reason"] == "not_platform_key"
    assert client.app.state.hk.stored == ""


@pytest.mark.parametrize("setup_hk,reason", [
    (lambda hk: setattr(hk, "manifest_result", {"unlocked": False, "upstream_error": True}),
     "upstream_error"),
    (lambda hk: setattr(hk, "manifest_result", {"unlocked": False}), "not_unlocked"),
    (lambda hk: setattr(hk, "manifest_result", RuntimeError("boom")), "upstream_error"),
    (lambda hk: setattr(hk, "save_raises", True), "save_failed"),
])
def test_adopt_failure_never_breaks_the_save(client, setup_hk, reason):
    """数据供给这一步失败是**可以接受的**:大模型配置已经写好了,第 4 步还能补。
    它绝不该把 200 变成 500。"""
    setup_hk(client.app.state.hk)
    r = _save(client)
    assert r.status_code == 200
    assert r.json()["saved"]["builtin"] is True
    assert r.json()["data_supply"]["adopted"] is False
    assert r.json()["data_supply"]["reason"] == reason
    # 大模型配置与 agent 模型名照样写进去了
    assert client.app.state.mem["saved"][-1]["sanitize"] == "0"
    assert client.app.state.mem["agent_models"]["AGENT_SUB_UZI_MODEL"] == "hunter-deep"


def test_non_builtin_never_touches_data_supply(client):
    """自带 key 的路径不许顺手把用户的 LLM key 当平台 key 存了。"""
    r = _save(client, base_url="https://api.deepseek.com/v1", model="deepseek-v4-pro",
              api_key="hunt_tools_abc", builtin=False)
    assert r.json()["data_supply"] == {"adopted": False}
    assert client.app.state.hk.stored == ""


# ── 5. 额度接口 ─────────────────────────────────────────────────────
def test_quota_is_guarded(client, monkeypatch):
    """公开前缀下的新接口同样要自己鉴权(仓内铁律)。"""
    r = client.get("/api/setup/llm/quota", headers=PUBLIC)
    assert r.status_code == 401 and r.json()["detail"]["need_unlock"] is True


def test_quota_not_builtin(client):
    client.app.state.mem["cfg"] = MemCfg("https://api.deepseek.com/v1", "sk-x", "m", "auto", "db")
    r = client.get("/api/setup/llm/quota", headers=LOCAL)
    assert r.status_code == 200 and r.json() == {"builtin": False}
    assert client.app.state.mem["quota_calls"] == []      # 一个请求都不该发出去


def test_quota_without_key_does_not_call_gateway(client):
    """地址是网关但 key 还没配 —— 不带 key 去打网关只会拿到 401,没有意义。"""
    client.app.state.mem["cfg"] = MemCfg(GW, "", "hunter-chat", "0", "db")
    assert client.get("/api/setup/llm/quota", headers=LOCAL).json() == {"builtin": False}
    assert client.app.state.mem["quota_calls"] == []


def test_quota_happy_path(client):
    client.app.state.mem["cfg"] = MemCfg(GW, "hunt_tools_abc", "hunter-chat", "0", "db")
    client.app.state.mem["quota_result"] = {
        "builtin": True, "ok": True,
        "quota": {"used_today": 1000, "limit_daily": 300000, "remaining": 299000,
                  "exhausted": False, "reset_at": "2026-09-20T00:00:00+08:00"},
    }
    d = client.get("/api/setup/llm/quota", headers=LOCAL).json()
    assert d["ok"] is True and d["quota"]["remaining"] == 299000
    url, key = client.app.state.mem["quota_calls"][0]
    assert url == "https://hunter.agentpit.io/api/saas/llm/quota"
    assert key == "hunt_tools_abc"


def test_quota_upstream_failure_is_honest(client):
    """网关不可达时**不许**编一个额度出来(空的比假的好)。"""
    client.app.state.mem["cfg"] = MemCfg(GW, "hunt_tools_abc", "hunter-chat", "0", "db")
    client.app.state.mem["quota_result"] = {
        "builtin": True, "ok": False, "code": "timeout", "message": "连不上"}
    d = client.get("/api/setup/llm/quota", headers=LOCAL).json()
    assert d["builtin"] is True and d["ok"] is False and "quota" not in d


def test_fetch_quota_never_raises(monkeypatch):
    """`_fetch_quota` 自己也要永远不抛 —— 设置页上一个额度数字取不到,
    不该把整张「大模型」卡片打成红色报错。"""
    import httpx

    class Boom:
        def get(self, *a, **k):
            raise httpx.ConnectTimeout("nope")
    monkeypatch.setitem(sys.modules, "httpx", httpx)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectTimeout("nope")))
    d = S._fetch_quota("https://x/quota", "k")
    assert d["builtin"] is True and d["ok"] is False and d["code"] == "timeout"


@pytest.mark.parametrize("status,code", [(401, "bad_key"), (403, "bad_key"), (500, "http_error")])
def test_fetch_quota_http_errors(monkeypatch, status, code):
    import httpx

    class R:
        status_code = status
        text = "nope"

        def json(self):
            return {}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())
    assert S._fetch_quota("https://x/quota", "k")["code"] == code


def test_fetch_quota_non_json(monkeypatch):
    import httpx

    class R:
        status_code = 200
        text = "<html>"

        def json(self):
            raise ValueError("not json")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())
    assert S._fetch_quota("https://x/quota", "k")["code"] == "bad_payload"


# ── 6. status 带出 builtin ──────────────────────────────────────────
def test_status_exposes_builtin(client):
    assert client.get("/api/setup/status", headers=LOCAL).json()["llm"]["builtin"] is False
    _save(client)
    assert client.get("/api/setup/status", headers=LOCAL).json()["llm"]["builtin"] is True
