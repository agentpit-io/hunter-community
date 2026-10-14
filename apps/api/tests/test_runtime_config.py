"""runtime_config 的优先级与容错 · 不连数据库(monkeypatch `_read_db`)。

跑法(容器里,别跑整个 tests/ —— test_screen_fix.py 在 import 阶段就 sys.exit):
    python -m pytest tests/test_runtime_config.py -q

盯的是四件最容易改坏的事:
  1. 环境变量**非空**才算配了(compose 的 `${X:-}` 注进来的是空串)
  2. 每一项独立判断,允许「地址来自环境变量 + 模型来自数据库」这种混合
  3. 三项都来自环境变量时**压根不查库**(老用户零行为变化)
  4. 数据库不可用时不抛异常,按未配置处理
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import runtime_config as rc  # noqa: E402

ENV_NAMES = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_DEFAULT_MODEL", "LLM_SCHEMA_SANITIZE")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """每条用例都从「环境变量全无、数据库全空、缓存已清」开始。"""
    for n in ENV_NAMES:
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(rc, "_read_db", lambda: {})
    rc.invalidate()
    yield
    rc.invalidate()


def _db(monkeypatch, **kw):
    """把数据库打桩成给定的几行。kw 用 base_url/api_key/model/sanitize。"""
    rows = {rc._ITEMS[k][1]: v for k, v in kw.items()}
    monkeypatch.setattr(rc, "_read_db", lambda: dict(rows))
    rc.invalidate()


# ── 1. 环境变量优先 ────────────────────────────────────────────────
def test_env_wins_over_db(monkeypatch):
    _db(monkeypatch, base_url="https://db.example/v1", api_key="db-key", model="db-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    monkeypatch.setenv("LLM_DEFAULT_MODEL", "env-model")
    cfg = rc.llm()
    assert (cfg.base_url, cfg.api_key, cfg.model) == (
        "https://env.example/v1", "env-key", "env-model")
    assert cfg.configured is True
    assert cfg.source == "env"
    assert rc.env_locked() is True


def test_empty_string_env_is_not_configured(monkeypatch):
    """compose 的 `${LLM_BASE_URL:-}` 注进来的是空串,不能当成「已配置」。"""
    for n in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_DEFAULT_MODEL"):
        monkeypatch.setenv(n, "")
    cfg = rc.llm()
    assert cfg.configured is False
    assert cfg.source == "none"
    assert rc.env_locked() is False
    assert rc.source("base_url") == "none"


def test_whitespace_only_env_is_not_configured(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "   ")
    assert rc.source("base_url") == "none"
    assert rc.llm().base_url == ""


# ── 2. 数据库 ──────────────────────────────────────────────────────
def test_db_used_when_env_absent(monkeypatch):
    _db(monkeypatch, base_url="https://db.example/v1/", api_key="db-key",
        model="db-model", sanitize="1")
    cfg = rc.llm()
    assert cfg.base_url == "https://db.example/v1"      # 末尾 / 去掉
    assert cfg.api_key == "db-key"
    assert cfg.model == "db-model"
    assert cfg.sanitize == "1"
    assert cfg.configured is True
    assert cfg.source == "db"
    assert rc.env_locked() is False


def test_nothing_configured(monkeypatch):
    cfg = rc.llm()
    assert cfg.configured is False
    assert cfg.source == "none"
    assert cfg.base_url == "" and cfg.api_key == "" and cfg.model == ""
    # 没配时 sanitize 给默认 auto,不是空串 —— gen-config / shim 都按 auto 走
    assert cfg.sanitize == rc.SANITIZE_DEFAULT == "auto"


# ── 3. 单项混合来源 ────────────────────────────────────────────────
def test_mixed_sources(monkeypatch):
    """地址锁在环境变量里、模型和 key 来自向导 —— 必须支持。"""
    _db(monkeypatch, api_key="db-key", model="db-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://env.example/v1")
    cfg = rc.llm()
    assert cfg.base_url == "https://env.example/v1"
    assert cfg.model == "db-model"
    assert cfg.configured is True
    # 有一项来自数据库 → 整体算 db(gen-config 据此写全局配置,向导才改得动)
    assert cfg.source == "db"
    assert rc.source("base_url") == "env"
    assert rc.source("model") == "db"
    assert rc.source("sanitize") == "none"
    assert rc.env_locked() is True      # 有东西被锁住了,向导要显示


def test_partial_config_is_not_configured(monkeypatch):
    """只有地址没有模型 = 没配好。不许猜一个模型名(404 比「未配置」更难懂)。"""
    _db(monkeypatch, base_url="https://db.example/v1", api_key="db-key")
    cfg = rc.llm()
    assert cfg.configured is False
    assert cfg.source == "none"
    assert cfg.base_url == "https://db.example/v1"   # 值仍读得到,供向导回显


def test_source_rejects_unknown_key():
    with pytest.raises(ValueError):
        rc.source("llm.nope")


def test_source_accepts_full_key(monkeypatch):
    monkeypatch.setenv("LLM_DEFAULT_MODEL", "m")
    assert rc.source("llm.model") == "env"


# ── 4. 缓存 ────────────────────────────────────────────────────────
def test_db_cached_then_invalidated(monkeypatch):
    calls = {"n": 0}

    def fake():
        calls["n"] += 1
        return {rc.K_BASE_URL: "https://db.example/v1",
                rc.K_API_KEY: "k", rc.K_MODEL: "m"}

    monkeypatch.setattr(rc, "_read_db", fake)
    rc.invalidate()
    rc.llm()
    rc.llm()
    assert calls["n"] == 1, "30 秒内应命中缓存"
    rc.invalidate()
    rc.llm()
    assert calls["n"] == 2, "invalidate 之后必须重新查库"


def test_env_complete_never_touches_db(monkeypatch):
    """三项全在环境变量里时不查库 —— 老用户不依赖数据库,行为与改造前一致。"""
    def boom():
        raise AssertionError("不该查库")

    monkeypatch.setattr(rc, "_read_db", boom)
    rc.invalidate()
    monkeypatch.setenv("LLM_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_DEFAULT_MODEL", "m")
    monkeypatch.setenv("LLM_SCHEMA_SANITIZE", "0")
    cfg = rc.llm()
    assert cfg.source == "env" and cfg.sanitize == "0"


# ── 5. 数据库不可用 ────────────────────────────────────────────────
def test_db_failure_does_not_raise(monkeypatch):
    def boom():
        raise RuntimeError("relation \"hunter_config\" does not exist")

    monkeypatch.setattr(rc, "_read_db", boom)
    rc.invalidate()
    cfg = rc.llm()                       # 不抛
    assert cfg.configured is False
    assert cfg.source == "none"
    assert rc.source("model") == "none"


def test_db_failure_with_env_still_works(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(rc, "_read_db", boom)
    rc.invalidate()
    monkeypatch.setenv("LLM_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_DEFAULT_MODEL", "m")
    assert rc.llm().configured is True


# ── 6. sanitize ────────────────────────────────────────────────────
def test_sanitize_lowercased(monkeypatch):
    monkeypatch.setenv("LLM_SCHEMA_SANITIZE", "AUTO")
    assert rc.llm().sanitize == "auto"


# ── 7. save_llm 写库并失效缓存 ─────────────────────────────────────
def test_save_llm_writes_and_invalidates(monkeypatch):
    written = {}

    class _Cur:
        def execute(self, sql, args=None):
            if args and len(args) == 2:
                written[args[0]] = args[1]

    class _Conn:
        def cursor(self):
            return _Cur()

        def commit(self):
            written["__committed__"] = True

        def close(self):
            written["__closed__"] = True

    monkeypatch.setattr(rc, "_ensure_table", lambda: None)
    monkeypatch.setattr(rc, "get_conn", lambda: _Conn())
    monkeypatch.setattr(rc, "encrypt", lambda s: "ENC:" + s)
    _db(monkeypatch, base_url="old")

    rc.llm()                                  # 先把缓存填上
    cfg = rc.LLMConfig(base_url="https://new.example/v1/", api_key="sk-secret",
                       model="m2", sanitize="1", configured=True, source="db")
    rc.save_llm(cfg)

    assert written[rc.K_BASE_URL] == "https://new.example/v1"
    assert written[rc.K_API_KEY] == "ENC:sk-secret", "key 必须加密后才入库"
    assert written[rc.K_MODEL] == "m2"
    assert written[rc.K_SANITIZE] == "1"
    assert rc.K_TESTED_AT not in written, "没给 tested_token 就不写 tested_at"
    assert _cache_is_empty(), "写完必须失效缓存,否则向导保存后 30 秒内还是旧值"


def test_save_llm_records_tested_at(monkeypatch):
    written = {}

    class _Cur:
        def execute(self, sql, args=None):
            if args and len(args) == 2:
                written[args[0]] = args[1]

    class _Conn:
        def cursor(self):
            return _Cur()

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(rc, "_ensure_table", lambda: None)
    monkeypatch.setattr(rc, "get_conn", lambda: _Conn())
    monkeypatch.setattr(rc, "encrypt", lambda s: s)
    rc.save_llm(rc.LLMConfig("b", "k", "m", "auto", True, "db"), tested_token="tok")
    assert rc.K_TESTED_AT in written


def _cache_is_empty() -> bool:
    return rc._cache["rows"] is None


# ── 5. agent 侧模型名(P2 · 内置额度)─────────────────────────────
#
# 这一组盯的是 P1 在测试机上踩到的那个坑:compose 把这些变量写成 `${X:-}`,
# 没有 .env 时注进容器的是**空串**,`os.getenv(名, 默认)` 拿到的是 `""` 而不是
# 默认值 —— 请求打过去 model 是空的,表现是「工具调用成功、之后的汇总整个失败」。
def test_agent_model_env_wins(monkeypatch):
    monkeypatch.setattr(rc, "_read_db",
                        lambda: {rc.K_AGENT_MODELS: '{"ASSISTANT_MODEL_CHAT": "db-model"}'})
    rc.invalidate()
    monkeypatch.setenv("ASSISTANT_MODEL_CHAT", "env-model")
    assert rc.agent_model("ASSISTANT_MODEL_CHAT", "fallback") == "env-model"


def test_agent_model_empty_env_falls_through(monkeypatch):
    """compose 的 `${X:-}` 注进来的空串不能当「已配置」。"""
    monkeypatch.setattr(rc, "_read_db",
                        lambda: {rc.K_AGENT_MODELS: '{"ASSISTANT_MODEL_CHAT": "db-model"}'})
    rc.invalidate()
    monkeypatch.setenv("ASSISTANT_MODEL_CHAT", "   ")
    assert rc.agent_model("ASSISTANT_MODEL_CHAT", "fallback") == "db-model"


def test_agent_model_falls_back_to_default(monkeypatch):
    """库里也没有时回到代码默认值 —— 这正是这个坑被踩之前本该有的行为。"""
    monkeypatch.delenv("ASSISTANT_MODEL_CHAT", raising=False)
    assert rc.agent_model("ASSISTANT_MODEL_CHAT", "gemini-4-argon") == "gemini-4-argon"
    assert rc.agent_model("ASSISTANT_MODEL_CHAT") == ""


def test_agent_models_bad_json_is_treated_as_unset(monkeypatch):
    """库里写坏了不抛异常 —— 它在很多请求的主干上(同 llm() 的处理)。"""
    for raw in ("not json", "[1,2]", '{"A": 1}', '{"A": ""}'):
        monkeypatch.setattr(rc, "_read_db", lambda raw=raw: {rc.K_AGENT_MODELS: raw})
        rc.invalidate()
        assert rc.agent_models() == {}, raw


def test_builtin_flag_from_db(monkeypatch):
    monkeypatch.setattr(rc, "_read_db", lambda: {rc.K_BUILTIN: "1"})
    rc.invalidate()
    assert rc.builtin() is True
    monkeypatch.setattr(rc, "_read_db", lambda: {rc.K_BUILTIN: ""})
    rc.invalidate()
    assert rc.builtin() is False


def test_builtin_true_when_base_url_is_gateway(monkeypatch):
    """.env 里把地址写死成网关的锁定实例:向导从没写过标记,额度展示照样要生效。"""
    monkeypatch.setenv("LLM_BASE_URL", rc.BUILTIN_BASE_URL)
    monkeypatch.setenv("LLM_API_KEY", "hunt_tools_x")
    monkeypatch.setenv("LLM_DEFAULT_MODEL", "hunter-chat")
    assert rc.builtin() is True


def test_builtin_agent_models_split_chat_and_deep():
    """长任务走 deep、其余走 chat。deep 的输出单价是 chat 的几倍,
    别把路由分类、摘要压缩这些短任务也指过去。"""
    m = rc.BUILTIN_AGENT_MODELS
    assert m["AGENT_SUB_RESEARCH_MODEL"] == rc.BUILTIN_DEEP_MODEL
    assert m["AGENT_SUB_UZI_MODEL"] == rc.BUILTIN_DEEP_MODEL
    deep = {k for k, v in m.items() if v == rc.BUILTIN_DEEP_MODEL}
    assert deep == {"AGENT_SUB_RESEARCH_MODEL", "AGENT_SUB_UZI_MODEL"}
    assert all(v in (rc.BUILTIN_CHAT_MODEL, rc.BUILTIN_DEEP_MODEL) for v in m.values())
