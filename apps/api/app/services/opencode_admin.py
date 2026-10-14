"""让 opencode 重新扫描 skill 目录。

**为什么需要**:opencode 只在实例启动时扫一次 skill 目录,之后缓存住
(`skill/index.ts` 里两个 `InstanceState`)。所以往 `user-skills/` 写完文件,
opencode 还看不到 —— 表现是「侧栏已经显示了,模型却说没有这个能力」。

实测过三条路(`_19` §1):
  · 文件出现在挂载目录      → 容器内 `ls` 立刻可见,opencode **认不到**
  · `POST /instance/dispose` → 返回 200 true,skill 列表**纹丝不动**
  · 重启容器                → 有效,**约 52 秒**

所以给 fork 加了 `POST /skill/refresh`(huntercode PR #1)。
**但用户的镜像版本我们控制不了** —— 旧镜像上这个端点是 404,
那时必须如实告诉用户"需要手动重启",不能假装成功。

> 一个静默 no-op 的 refresh 比没有 refresh 更糟:
> UI 报告保存成功,而模型手上还是旧列表,用户完全无从判断。
"""
from __future__ import annotations

import base64
import os

import httpx
from loguru import logger

_URL = os.getenv("OPENCODE_URL", "http://opencode:3901")
_USER = os.getenv("OPENCODE_SERVER_USERNAME", "")
_PASS = os.getenv("OPENCODE_SERVER_PASSWORD", "")

# 重扫本身很快(实测 <1 秒),但首次会真的读一遍磁盘,给宽一点
_TIMEOUT = 30.0

# 大模型热生效用(apply_llm)· 与 scripts/opencode/gen-config.py 里的同名常量对齐
_PROVIDER_ID = "hunter-llm"
_SHIM_URL = os.getenv("LLM_SHIM_URL", "http://llm-shim:3999/v1").rstrip("/")
BUILTIN_MODEL_LABELS = {
    "hunter-chat": "Hunter-Gemini 4 Argon（谷歌 4.0）",
    "hunter-deep": "Hunter-Gemini 4 Argon（谷歌 4.0 · 深度推理）",
}


def _headers() -> dict:
    if not (_USER or _PASS):
        return {}
    token = base64.b64encode(f"{_USER}:{_PASS}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def refresh_skills() -> dict:
    """让 opencode 重扫 skill 目录。

    返回:
        {"ok": True,  "count": N}                    刷新成功,N = 重扫后的 skill 数
        {"ok": False, "needs_restart": True, ...}    端点不存在(镜像旧)或调用失败

    **永远不抛异常** —— 调用方在保存 SKILL 的主干上,刷新失败不该让保存回滚。
    文件已经写好了,重启一次就生效;把这件事**告诉用户**即可。
    """
    try:
        r = httpx.post(f"{_URL}/skill/refresh", headers=_headers(), timeout=_TIMEOUT)
    except Exception as e:
        logger.warning("[opencode_admin] refresh 请求失败: {}", e)
        return {"ok": False, "needs_restart": True,
                "reason": f"连不上 opencode({type(e).__name__})"}

    if r.status_code == 404:
        # 旧镜像没有这个端点 —— 这不是错误,是版本差异,措辞要区分开
        logger.info("[opencode_admin] /skill/refresh 不存在(镜像较旧)· 需手动重启")
        return {"ok": False, "needs_restart": True,
                "reason": "当前 opencode 镜像不支持热刷新"}
    if r.status_code != 200:
        logger.warning("[opencode_admin] refresh 返回 {}: {}", r.status_code, r.text[:120])
        return {"ok": False, "needs_restart": True,
                "reason": f"opencode 返回 HTTP {r.status_code}"}

    # ⚠️ **状态码 200 不等于端点存在**。
    # opencode 对未知路由返回 200 + SPA 的 HTML(实测:Content-Type text/html,
    # 正文是 <!doctype html>)。只看状态码的话,旧镜像上会被判成"刷新成功",
    # UI 报告已同步而模型手上还是旧列表 —— 正是这个模块开头说要避免的那件事。
    #
    # 所以**以能不能解析出一个数为准**:端点契约是返回重扫后的 skill 数。
    try:
        count = int(r.json())
    except Exception:
        logger.info("[opencode_admin] /skill/refresh 返回的不是数字"
                    "(content-type={})· 判定为镜像不支持,需手动重启",
                    r.headers.get("content-type", "?"))
        return {"ok": False, "needs_restart": True,
                "reason": "当前 opencode 镜像不支持热刷新(未知路由被 SPA 接管)"}
    logger.info("[opencode_admin] 刷新成功 · 现有 {} 个 skill", count)
    return {"ok": True, "count": count}


def restart_hint() -> str:
    """刷新不成功时给用户看的一句话。写清楚**为什么**,不只是让他敲命令。"""
    return ("新能力已保存,但当前 opencode 需要重启才能识别 —— "
            "它只在启动时扫描能力目录。在部署目录执行:"
            "docker compose restart opencode(约 50 秒)")


# ─────────────────────────────────────────────────────────────────────
# 大模型配置热生效(M1 · R0 §1.2 / 1.5)
# ─────────────────────────────────────────────────────────────────────
def model_label(base_url: str, api_key: str, model: str) -> str:
    """问上游要这个模型的展示名,拿不到就用模型名本身。

    为什么要问:内置额度下用户配的是别名(hunter-chat),而他在对话框里想知道的是
    「我现在用的是哪个模型」。别名换上游时用户无感,展示名跟着上游走 —— 所以展示名
    只能问网关要,不能在客户端写死一张表(写死的那天起就开始过期)。

    自带 key 的地址一般没有这个字段,返回模型名本身,行为与改动前一致。
    超时 8 秒、最多 2 次尝试、内置网关带谷歌 4.0 兜底。
    """
    if not base_url or not model:
        return model
    clean_base = base_url.rstrip("/")
    is_builtin_base = clean_base.endswith("/api/saas/llm/v1")
    for attempt in (1, 2):
        try:
            r = httpx.get(
                f"{clean_base}/models",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                timeout=8.0,
            )
            if r.status_code != 200:
                break
            for m in (r.json() or {}).get("data") or []:
                if m.get("id") == model:
                    disp = (m.get("display_name") or "").strip()
                    if model in BUILTIN_MODEL_LABELS and (
                        not disp or ("Gemini 4 Argon" in disp and "谷歌 4.0" not in disp)
                    ):
                        disp = BUILTIN_MODEL_LABELS[model]
                    label = disp or model
                    logger.info("[opencode_admin] model_label={} (model={})", label, model)
                    return label
            break
        except Exception as e:  # noqa: BLE001
            logger.debug("[opencode_admin] 第 {} 次取模型展示名失败(不影响功能): {}", attempt, e)
    fallback = BUILTIN_MODEL_LABELS[model] if (is_builtin_base and model in BUILTIN_MODEL_LABELS) else model
    logger.info("[opencode_admin] model_label={}(回退, model={})", fallback, model)
    return fallback


def apply_llm(cfg) -> dict:
    """把大模型配置热推给 opencode,**不重启容器**。cfg 是 runtime_config.LLMConfig。

    返回 {"ok": bool, "reason": str}。**永远不抛异常** —— 与 refresh_skills() 同样
    的理由:它挂在保存配置的主干上,推送失败不该让保存回滚。

    ## 为什么是 `PATCH /global/config` 而不是 `PATCH /config`

    R0 实测(2026-09-18):`PATCH /config` 返回 200(9 ms)、也确实写出了
    `<实例目录>/config.json`,但**那个文件根本不在配置加载路径上**,内容永不生效:
    PATCH 之后 `GET /config` 与新会话仍用旧模型。改用 `PATCH /global/config` 走的是
    `Config.updateGlobal`:写 `~/.config/opencode/opencode.jsonc`、清掉全局配置缓存
    (`invalidate()`)、销毁全部实例,所以配置能真正重新加载。

    R0 实测耗时(同一网关来回换两个模型名):
        PATCH /global/config 返回          34 ms / 48 ms
        GET /config 反映新模型             0.52 s / 0.53 s
        GET /mcp(实例重建 + MCP 重连)     3.44 s / 3.60 s · 均 6/6 connected
        新会话首个请求                     3.42 s / 3.69 s
        **PATCH → 新对话可用 总计          7.42 s / 7.87 s**
    旧会话可读、可续聊,会话不丢。

    ## ⚠️ 两个调用方必须知道的坑

    1. **这是同步函数,async 里必须 `await run_in_threadpool(apply_llm, cfg)`。**
       api 的入口是单 worker 的 uvicorn,直接在 `async def` 里调同步 httpx 会把
       事件循环整个堵住。子任务 D 已经踩过一次(`/skill/refresh` 卡满 30 秒超时,
       而文件其实早写好了、opencode 也活着)。写成同步是为了与 refresh_skills()
       风格一致,代价就是调用方要包一层。
    2. **PATCH 本身不会撞上那个闭环,但紧接着的「就绪探测」会。**
       `PATCH /global/config` 的实例销毁发生在**响应之后**,所以 PATCH 这一下是安全的;
       可是**下一个**打到 opencode 的请求会触发实例重建,重建时要走完整的配置加载 +
       SKILL 发现流程,而 SKILL 发现里有 `skills.urls` → **回头来拉 api 的
       /api/internal/skills/...**。如果 api 这一侧正被一个同步调用堵着事件循环,
       就是死锁:opencode 拉不到清单,api 等到超时。
       所以 M2 的「保存后轮询 `GET /config/providers` 判断就绪」同样必须
       `run_in_threadpool`,不能在事件循环里同步等。

    M1 只提供这个服务函数,**不暴露 HTTP 接口** —— 接口是 M2 向导的事。
    """
    model = (getattr(cfg, "model", "") or "").strip()
    base_url = (getattr(cfg, "base_url", "") or "").strip().rstrip("/")
    api_key = getattr(cfg, "api_key", "") or ""
    sanitize = (getattr(cfg, "sanitize", "") or "auto").strip().lower()
    if not model:
        return {"ok": False, "reason": "大模型尚未配置(没有模型名),不推送"}

    # 与 scripts/opencode/gen-config.py 的 _use_shim() 保持一致:
    # sanitize=0 直连用户地址、不经 shim;1 强制经 shim;auto 只有 gemini 才经。
    # **两处逻辑必须同口径** —— 不然重启一次容器,provider 就换了一副样子。
    if sanitize in ("0", "false", "no", "off"):
        via_shim = False
    elif sanitize in ("1", "true", "yes", "on"):
        via_shim = True
    else:
        via_shim = "gemini" in model.lower()

    options = {"baseURL": _SHIM_URL if via_shim else base_url, "apiKey": api_key}
    if via_shim and base_url:
        # 真正的上游走请求头给 shim —— 配置存数据库时 shim 容器的 LLM_BASE_URL 是空的
        options["headers"] = {"X-Hunter-Upstream": base_url}

    body = {
        "provider": {
            _PROVIDER_ID: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Hunter LLM",
                "options": options,
                "models": {model: {"name": model_label(base_url, api_key, model)}},
            }
        },
        "model": f"{_PROVIDER_ID}/{model}",
    }

    try:
        r = httpx.patch(f"{_URL}/global/config", headers=_headers(), json=body,
                        timeout=_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        logger.warning("[opencode_admin] apply_llm 请求失败: {}", e)
        return {"ok": False, "reason": f"连不上 opencode({type(e).__name__})"}

    if r.status_code == 404:
        logger.info("[opencode_admin] /global/config 不存在(镜像较旧)· 需重启 opencode")
        return {"ok": False, "reason": "当前 opencode 镜像不支持热更新配置(需重启容器)"}
    if r.status_code != 200:
        logger.warning("[opencode_admin] apply_llm 返回 {}: {}", r.status_code, r.text[:120])
        return {"ok": False, "reason": f"opencode 返回 HTTP {r.status_code}"}

    # ⚠️ **状态码 200 不等于端点存在**(与 refresh_skills 同一个坑):
    # opencode 对未知路由返回 200 + SPA 的 HTML。只看状态码的话,旧镜像上会被判成
    # 「已生效」,而模型其实一点没变 —— 用户下一条对话还是老模型,却看不到任何提示。
    # 所以以**能不能解析出一个 JSON 对象**为准(HTML 解析必然失败)。
    try:
        payload = r.json()
    except Exception:  # noqa: BLE001
        logger.info("[opencode_admin] /global/config 返回的不是 JSON"
                    "(content-type={})· 判定为镜像不支持,需重启容器",
                    r.headers.get("content-type", "?"))
        return {"ok": False, "reason": "当前 opencode 镜像不支持热更新配置(未知路由被 SPA 接管)"}
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "opencode 返回的配置格式不对(需重启容器)"}

    # 日志只说模型名和走不走 shim,**不打印 apiKey**
    logger.info("[opencode_admin] 已热推大模型配置 · model={} · 经 shim={} · "
                "按 R0 实测约 7.4~7.9 秒后新对话生效(MCP 重连 3.4~3.6 秒)",
                model, via_shim)
    return {"ok": True, "reason": ""}
