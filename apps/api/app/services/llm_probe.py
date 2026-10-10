"""向导第 3 步 · 大模型三项检测（设计方案 4.5）。

依次做三件事，每一项都记**真实耗时**（不做任何估算展示）：

  ① 连通   `GET  {base}/models`            10 秒
  ② 对话   `POST {base}/chat/completions`  30 秒 · **只发一条 user 消息**
  ③ 工具   同上 + 一个故意带 `$schema` / `additionalProperties` 的工具

## 为什么由 api 发这些请求

与 opencode 实际调用走**同一条网络路径**（同一个 docker 网络、同一组代理变量）。
放到浏览器里测，测的是用户电脑能不能连上，那和容器能不能连上是两件事 ——
aihubmix 那个「浏览器通、Alpine 容器被 TLS 指纹拦」的坑就是这么来的。

## 为什么 ② 只发一条 user 消息

部分 Gemini 版本对「system + user 两条消息」的组合返回 400。检测的目的是判断
这套配置能不能用，不该被一个与配置无关的兼容性问题带偏。

## 为什么 ③ 的 schema 要故意写脏

opencode 送给模型的就是完整 JSON Schema（带 `$schema`、`additionalProperties`）。
拿一个干净 schema 去测，会得到「检测通过、实际对话一发就 400」。
脏 schema 失败时用 **llm-shim 同一份清洗函数**（`schema_clean.py`）重试：
重试通过就说明这套配置需要 `LLM_SCHEMA_SANITIZE=1`，向导据此给出建议值。

## 失败要分类，不要只回一句「失败」

用户拿到「连不上」和拿到「key 无效」的下一步动作完全不同。分类不出来的
如实写「未知错误」+ 上游原话，**不要猜**。
"""
from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path

import httpx
from loguru import logger

CONNECT_TIMEOUT = 10.0
CHAT_TIMEOUT = 30.0
TOOL_TIMEOUT = 45.0     # 带工具的请求普遍更慢（实测 gemini/qwen 20~40 秒）

# 检测用的工具 · 名字取得足够特别，模型不可能"恰好"调到别的
PROBE_TOOL_NAME = "hunter_setup_probe_quote"

# ⚠️ 故意保留 `$schema` / `additionalProperties` —— 见模块文档
PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": PROBE_TOOL_NAME,
        "description": "查询某只股票的最新价格。用户问到股价时必须调用它。",
        "parameters": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "code": {"type": "string", "description": "股票代码，如 600519"},
            },
            "required": ["code"],
        },
    },
}

PROBE_TOOL_PROMPT = "查一下 600519 现在的股价。你必须调用工具，不要凭记忆回答。"


# ── 清洗函数 · 与 llm-shim 共用同一份文件 ────────────────────────────
def _load_schema_clean():
    """加载 `scripts/llm-shim/schema_clean.py`。

    镜像里由 Dockerfile 放到 `/opt/hunter-shim/`；仓库里直接跑测试时回落到
    源码路径。**不在 api 里另存一份副本** —— 两份实现迟早会漂，而漂了的表现是
    「向导说能用、实际对话 400」，最难查的那一类。
    """
    candidates = [
        os.getenv("HUNTER_SCHEMA_CLEAN") or "",
        "/opt/hunter-shim/schema_clean.py",
        # 仓库里直接跑时的回落。⚠️ 不能写死 parents[4]:容器里
        # `/src/app/services/llm_probe.py` 只有 3 级父目录,会 IndexError,
        # 而这段是**模块加载期**跑的 —— 抛出来就是整个 api 起不来。
        *[str(x / "scripts" / "llm-shim" / "schema_clean.py")
          for x in Path(__file__).resolve().parents],
    ]
    for c in candidates:
        if c and Path(c).is_file():
            spec = importlib.util.spec_from_file_location("hunter_schema_clean", c)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)   # type: ignore[union-attr]
            return mod
    return None


_schema_clean = None


def schema_clean():
    global _schema_clean
    if _schema_clean is None:
        _schema_clean = _load_schema_clean()
        if _schema_clean is None:
            logger.warning("[setup] 找不到 schema_clean.py · 第 3 项检测将不做清洗重试")
    return _schema_clean


# ── 错误分类 ────────────────────────────────────────────────────────
def classify_transport(exc: Exception) -> tuple[str, str]:
    """把 httpx 的异常翻成 (code, 中文说明)。"""
    name = type(exc).__name__
    text = str(exc)
    low = text.lower()
    if isinstance(exc, httpx.ConnectTimeout) or isinstance(exc, httpx.ReadTimeout) \
            or isinstance(exc, httpx.PoolTimeout) or "timeout" in low:
        return ("timeout",
                "服务器连不上该地址：请求超时。"
                "如果你的宿主机开了代理（clash / verge 之类），容器默认是不走代理的，"
                "需要在 .env 里设 HTTP_PROXY_UPSTREAM / HTTPS_PROXY_UPSTREAM。")
    if "name or service not known" in low or "nodename nor servname" in low \
            or "temporary failure in name resolution" in low or "getaddrinfo" in low:
        return ("dns", "域名解析失败：这个地址的域名在容器里查不到，请检查地址是否写错。")
    if "unexpected_eof" in low or "record layer failure" in low or "sslerror" in low \
            or "ssl" in low and "eof" in low:
        return ("tls",
                "网关拦截了容器发起的连接（TLS 握手被断开）。"
                "实测 aihubmix 等网关会按 TLS 指纹拦 Alpine 容器的直连，"
                "需要在 .env 里配 HTTP_PROXY_UPSTREAM / HTTPS_PROXY_UPSTREAM 走宿主机代理。")
    if "connection refused" in low or "connect call failed" in low:
        return ("refused", "对方拒绝连接：地址或端口不对，或者那个服务没在跑。")
    return ("network", f"网络请求失败（{name}）：{text[:200]}")


def _err_text(resp: httpx.Response) -> str:
    try:
        d = resp.json()
    except Exception:  # noqa: BLE001
        return (resp.text or "")[:400]
    if isinstance(d, dict):
        e = d.get("error")
        if isinstance(e, dict):
            return str(e.get("message") or e)[:400]
        if isinstance(e, str):
            return e[:400]
        if d.get("message"):
            return str(d["message"])[:400]
    return str(d)[:400]


_MODEL_ERR_WORDS = (
    "model_not_found", "model not found", "does not exist", "no such model",
    "invalid model", "unknown model", "unsupported model", "model_not_supported",
    "模型不存在", "无权使用模型", "模型无权", "不支持该模型", "该模型不存在",
)


def _mentions_model(msg: str, model: str) -> bool:
    """这条报错是在说「模型不对」吗。

    两条判据，任一命中即可：
      · 报错里**逐字出现了模型名** —— 网关不会无缘无故提它；
      · 命中模型类关键词（中英文都收，不同网关措辞差很多）。
    """
    low = (msg or "").lower()
    if model and model.lower() in low:
        return True
    return any(w in low for w in _MODEL_ERR_WORDS)


def _looks_like_schema_error(msg: str) -> bool:
    low = (msg or "").lower()
    keys = ("$schema", "additionalproperties", "unknown name", "cannot find field",
            "invalid json payload", "invalid schema", "function_declarations",
            "must be a json schema", "unknown field")
    return any(k in low for k in keys)


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


# ── ① 连通 ──────────────────────────────────────────────────────────
def check_reach(base_url: str, api_key: str) -> dict:
    url = f"{base_url.rstrip('/')}/models"
    t0 = time.monotonic()
    try:
        r = httpx.get(url, headers=_headers(api_key), timeout=CONNECT_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        code, msg = classify_transport(e)
        return {"name": "连通", "ok": False, "elapsed_ms": int((time.monotonic() - t0) * 1000),
                "code": code, "message": msg, "models": []}
    ms = int((time.monotonic() - t0) * 1000)

    if r.status_code in (401, 403):
        return {"name": "连通", "ok": False, "elapsed_ms": ms, "code": "bad_key",
                "message": f"key 无效或没有权限（HTTP {r.status_code}）：{_err_text(r)}",
                "models": []}
    if r.status_code == 404:
        # 不少网关不实现 /models —— 这不算失败，只是拿不到可用模型列表
        return {"name": "连通", "ok": True, "elapsed_ms": ms, "code": "no_models_api",
                "message": "地址可达（该网关没有实现 /models 接口，拿不到可用模型列表）",
                "models": []}
    if r.status_code >= 400:
        return {"name": "连通", "ok": False, "elapsed_ms": ms, "code": "http_error",
                "message": f"上游返回 HTTP {r.status_code}：{_err_text(r)}", "models": []}

    models: list[str] = []
    try:
        d = r.json()
        for it in (d.get("data") or d.get("models") or []):
            mid = it.get("id") if isinstance(it, dict) else it
            if isinstance(mid, str):
                models.append(mid)
    except Exception:  # noqa: BLE001
        pass
    return {"name": "连通", "ok": True, "elapsed_ms": ms, "code": "",
            "message": f"地址可达，拿到 {len(models)} 个可用模型" if models else "地址可达",
            "models": models[:500]}


# ── ② 对话 ──────────────────────────────────────────────────────────
def check_chat(base_url: str, api_key: str, model: str, known_models: list) -> dict:
    url = f"{base_url.rstrip('/')}/chat/completions"
    body = {
        "model": model,
        # 只发一条 user 消息 —— 见模块文档
        "messages": [{"role": "user", "content": "只回复两个字：收到"}],
        "max_tokens": 16,
    }
    t0 = time.monotonic()
    try:
        r = httpx.post(url, headers=_headers(api_key), json=body, timeout=CHAT_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        code, msg = classify_transport(e)
        return {"name": "对话", "ok": False, "elapsed_ms": int((time.monotonic() - t0) * 1000),
                "code": code, "message": msg, "reply": "", "warn": ""}
    ms = int((time.monotonic() - t0) * 1000)
    err = _err_text(r)

    if r.status_code >= 400:
        # ⚠️ **模型名的判断要排在 401/403 之前**。
        #
        # 实测（2026-09-18，演示站那台 OneAPI 网关）：模型名写错时它回的是
        #   HTTP 403 ·「该令牌无权使用模型：gemini-9.9-nonexistent」
        # 先按状态码判的话会得到「key 无效」—— 用户拿着一把好 key 去重新申请，
        # 而真正要改的是模型名。判据改成**看报错里提没提模型**：
        # 提了模型名、或命中模型类关键词，就按模型名不对处理。
        if _mentions_model(err, model) or r.status_code == 404:
            hint = ""
            if known_models:
                near = [m for m in known_models if model.split("-")[0].lower() in m.lower()][:8]
                pool = near or known_models[:8]
                hint = "；这个地址可用的模型里有：" + "、".join(pool)
            return {"name": "对话", "ok": False, "elapsed_ms": ms, "code": "bad_model",
                    "message": f"模型名不对，或这把 key 没有这个模型的权限"
                               f"（HTTP {r.status_code}）：{err}{hint}",
                    "reply": "", "warn": ""}
        if r.status_code in (401, 403):
            return {"name": "对话", "ok": False, "elapsed_ms": ms, "code": "bad_key",
                    "message": f"key 无效或没有权限（HTTP {r.status_code}）：{err}",
                    "reply": "", "warn": ""}
        if r.status_code == 429:
            return {"name": "对话", "ok": False, "elapsed_ms": ms, "code": "rate_limited",
                    "message": f"上游限流（HTTP 429）：{err}", "reply": "", "warn": ""}
        if r.status_code == 402:
            return {"name": "对话", "ok": False, "elapsed_ms": ms, "code": "no_balance",
                    "message": f"账户余额不足（HTTP 402）：{err}", "reply": "", "warn": ""}
        return {"name": "对话", "ok": False, "elapsed_ms": ms, "code": "http_error",
                "message": f"上游返回 HTTP {r.status_code}：{err}", "reply": "", "warn": ""}

    try:
        d = r.json()
        choice0 = (d.get("choices") or [{}])[0] or {}
        msg0 = choice0.get("message") or {}
        text = (msg0.get("content") or "").strip()
        finish_reason = str(choice0.get("finish_reason") or "").strip()
    except Exception:  # noqa: BLE001
        return {"name": "对话", "ok": False, "elapsed_ms": ms, "code": "bad_payload",
                "message": f"上游返回的不是 OpenAI 兼容格式：{(r.text or '')[:200]}",
                "reply": "", "warn": ""}

    if not text:
        if finish_reason == "length":
            # 正文为空是因为生成被 max_tokens 截断：推理模型的 reasoning token
            # 也计入这个预算，先把它花光就写不到正文了。责任在探针自己的预算，
            # 不是配置坏 —— 正式对话不设 max_tokens，不受影响，按通过处理。
            return {"name": "对话", "ok": True, "elapsed_ms": ms, "code": "",
                    "message": "上游被 max_tokens 截断（finish_reason=length），"
                               "本次没拿到正文 —— 按通过处理。",
                    "reply": "",
                    "warn": "生成预算被思考过程用光了（推理型模型常见）。"
                            "正式对话不设 max_tokens，通常不受影响。"}
        return {"name": "对话", "ok": False, "elapsed_ms": ms, "code": "empty",
                "message": "上游返回了 200，但回复内容是空的。"
                           "常见原因是这个模型把内容放在了 reasoning 字段里，"
                           "或者 max_tokens 被推理过程吃光了。",
                "reply": "", "warn": ""}

    warn = ""
    if "<think>" in text.lower():
        warn = ("这个模型会把思考过程（<think>…</think>）混进正文，"
                "对话里会看到多余内容。llm-shim 会尽量剥掉，但建议优先换一个不泄漏的模型。")
    return {"name": "对话", "ok": True, "elapsed_ms": ms, "code": "",
            "message": f"收到回复：{text[:60]}", "reply": text[:200], "warn": warn}


# ── ③ 工具调用 ──────────────────────────────────────────────────────
def _tool_names(payload: dict) -> list:
    try:
        msg = (payload.get("choices") or [{}])[0].get("message") or {}
    except Exception:  # noqa: BLE001
        return []
    out = []
    for c in (msg.get("tool_calls") or []):
        fn = c.get("function") if isinstance(c, dict) else None
        if isinstance(fn, dict) and fn.get("name"):
            out.append(fn["name"])
    # 少数网关仍回老的 function_call 字段
    fc = msg.get("function_call")
    if isinstance(fc, dict) and fc.get("name"):
        out.append(fc["name"])
    return out


def _tool_request(base_url: str, api_key: str, model: str, tools: list) -> tuple:
    url = f"{base_url.rstrip('/')}/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": PROBE_TOOL_PROMPT}],
        "tools": tools,
        "tool_choice": "auto",
        "max_tokens": 256,
    }
    t0 = time.monotonic()
    try:
        r = httpx.post(url, headers=_headers(api_key), json=body, timeout=TOOL_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return None, e, int((time.monotonic() - t0) * 1000)
    return r, None, int((time.monotonic() - t0) * 1000)


def check_tools(base_url: str, api_key: str, model: str) -> dict:
    """返回里多一个 `sanitize_suggest`：脏 schema 直接过 → "auto"；
    洗过才过 → "1"。**这是向导保存时写进配置的那个值的来源。**"""
    raw_tools = [PROBE_TOOL]
    r, exc, ms = _tool_request(base_url, api_key, model, raw_tools)
    if exc is not None:
        code, msg = classify_transport(exc)
        return {"name": "工具调用", "ok": False, "elapsed_ms": ms, "code": code,
                "message": msg, "sanitize_suggest": "", "cleaned": False}

    if r.status_code < 400:
        names = _tool_names(r.json() if r.content else {})
        if PROBE_TOOL_NAME in names:
            return {"name": "工具调用", "ok": True, "elapsed_ms": ms, "code": "",
                    "message": f"模型正确调用了工具 {PROBE_TOOL_NAME}（未清洗 schema 即通过）",
                    "sanitize_suggest": "auto", "cleaned": False}
        if names:
            return {"name": "工具调用", "ok": False, "elapsed_ms": ms, "code": "wrong_tool",
                    "message": f"模型调用了工具，但名字不对（{', '.join(names)}）。"
                               f"这通常说明网关改写了工具定义。",
                    "sanitize_suggest": "", "cleaned": False}
        return {"name": "工具调用", "ok": False, "elapsed_ms": ms, "code": "no_tool_call",
                "message": "模型没有调用工具，直接用文字回答了。"
                           "行情、K 线、深度分析这些功能全部依赖工具调用，"
                           "这个模型用不了，请换一个支持 function calling 的模型。",
                "sanitize_suggest": "", "cleaned": False}

    err = _err_text(r)
    sc = schema_clean()
    if not (_looks_like_schema_error(err) and sc is not None):
        return {"name": "工具调用", "ok": False, "elapsed_ms": ms, "code": "http_error",
                "message": f"上游返回 HTTP {r.status_code}：{err}",
                "sanitize_suggest": "", "cleaned": False}

    # 脏 schema 被拒 → 用 shim 同一份清洗函数洗一遍重试
    import copy
    body_probe = {"tools": copy.deepcopy(raw_tools)}
    sc.clean_tools(body_probe)
    r2, exc2, ms2 = _tool_request(base_url, api_key, model, body_probe["tools"])
    total = ms + ms2
    if exc2 is not None:
        code, msg = classify_transport(exc2)
        return {"name": "工具调用", "ok": False, "elapsed_ms": total, "code": code,
                "message": msg, "sanitize_suggest": "", "cleaned": True}
    if r2.status_code >= 400:
        return {"name": "工具调用", "ok": False, "elapsed_ms": total, "code": "schema_reject",
                "message": f"这个模型拒绝我们发的工具定义，清洗后仍然失败："
                           f"HTTP {r2.status_code} {_err_text(r2)}",
                "sanitize_suggest": "", "cleaned": True}
    names = _tool_names(r2.json() if r2.content else {})
    if PROBE_TOOL_NAME in names:
        return {"name": "工具调用", "ok": True, "elapsed_ms": total, "code": "needs_clean",
                "message": "原始工具定义被拒，清洗掉 JSON Schema 扩展字段后通过 —— "
                           "这个模型需要打开 schema 清洗（保存时会自动设为开）。",
                "sanitize_suggest": "1", "cleaned": True}
    return {"name": "工具调用", "ok": False, "elapsed_ms": total, "code": "no_tool_call",
            "message": "清洗 schema 之后模型仍然没有调用工具。"
                       "行情、K 线、深度分析这些功能全部依赖工具调用，请换一个模型。",
            "sanitize_suggest": "", "cleaned": True}


def run_all(base_url: str, api_key: str, model: str) -> dict:
    """三项依次跑。**前一项失败就停** —— 连不上还去发对话请求，只会让用户
    等三份超时、拿到三条互相矛盾的报错。"""
    t0 = time.monotonic()
    checks = []

    c1 = check_reach(base_url, api_key)
    checks.append(c1)
    if not c1["ok"]:
        return _result(checks, t0, "")

    c2 = check_chat(base_url, api_key, model, c1.get("models") or [])
    checks.append(c2)
    if not c2["ok"]:
        return _result(checks, t0, "")

    c3 = check_tools(base_url, api_key, model)
    checks.append(c3)
    return _result(checks, t0, c3.get("sanitize_suggest") or "")


def _result(checks: list, t0: float, sanitize: str) -> dict:
    ok = all(c["ok"] for c in checks) and len(checks) == 3
    return {
        "ok": ok,
        "checks": [{k: v for k, v in c.items() if k != "models"} for c in checks],
        "models": next((c.get("models") for c in checks if c.get("models")), []) or [],
        "sanitize_suggest": sanitize,
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
    }
