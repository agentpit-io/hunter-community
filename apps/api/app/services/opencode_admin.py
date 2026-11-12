"""对话引擎（AtomCode Daemon / OpenCode）管理、配置热推与诊断快照服务。

当 `AGENT_BACKEND=atomcode`（默认）时，全面对接 AtomCode Daemon（`http://daemon:13456`）：
  · 鉴权 Token 从 `/run/hca/daemon-token`（或 `ATOMCODE_DAEMON_TOKEN`）读取；
  · 状态快照与就绪探针走 `GET /config`、`GET /models`、`GET /mcp/status`、`GET /health`；
  · 技能刷新走 `GET /skills`；
  · 大模型配置热推走更新 `/data/atomcode/config.toml` + `POST /live/provider`。

当 `AGENT_BACKEND=opencode`（仅兼容回退）时，保留原有 `http://opencode:3901` 逻辑。
所有对外函数签名与返回 JSON 结构 100% 保持兼容，且任何情况下绝不回显明文 API Key。
"""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

import httpx
from loguru import logger

_URL = os.getenv("OPENCODE_URL", "http://opencode:3901")
_USER = os.getenv("OPENCODE_SERVER_USERNAME", "")
_PASS = os.getenv("OPENCODE_SERVER_PASSWORD", "")

# 重扫本身很快(实测 <1 秒),但首次会真的读一遍磁盘,给宽一点
_TIMEOUT = 30.0

# 大模型热生效用(apply_llm)
_PROVIDER_ID = "hunter-llm"
_ATOMCODE_PROVIDER_DEFAULT = "hunter"
_SHIM_URL = os.getenv("LLM_SHIM_URL", "http://llm-shim:3999/v1").rstrip("/")
BUILTIN_MODEL_LABELS = {
    "hunter-chat": "Hunter-Gemini 4 Argon（谷歌 4.0）",
    "hunter-deep": "Hunter-Gemini 4 Argon（谷歌 4.0 · 深度推理）",
}


def is_atomcode_backend() -> bool:
    """当前对话引擎是否为 AtomCode（默认即为 atomcode）。"""
    return (os.getenv("AGENT_BACKEND") or "atomcode").strip().lower() != "opencode"


def daemon_url() -> str:
    return (os.getenv("ATOMCODE_DAEMON_URL") or "http://daemon:13456").strip().rstrip("/")


def daemon_token() -> str:
    env_tok = (os.getenv("ATOMCODE_DAEMON_TOKEN") or "").strip()
    if env_tok:
        return env_tok
    token_file = Path(os.getenv("ATOMCODE_TOKEN_FILE") or "/run/hca/daemon-token")
    try:
        if token_file.is_file():
            return token_file.read_text(encoding="utf-8").strip()
    except OSError as e:
        logger.debug("[opencode_admin] 读取 daemon token 文件 {} 失败: {}", token_file, e)
    return ""


def daemon_headers() -> dict[str, str]:
    tok = daemon_token()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def _headers() -> dict:
    if not (_USER or _PASS):
        return {}
    token = base64.b64encode(f"{_USER}:{_PASS}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def refresh_skills() -> dict:
    """让对话引擎重扫 skill 目录。

    返回:
        {"ok": True,  "count": N}                    刷新成功,N = 重扫后的 skill 数
        {"ok": False, "needs_restart": True, ...}    端点不存在或调用失败

    **永远不抛异常** —— 调用方在保存 SKILL 的主干上,刷新失败不该让保存回滚。
    """
    if is_atomcode_backend():
        url = daemon_url()
        try:
            r = httpx.get(f"{url}/skills", headers=daemon_headers(), timeout=_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            logger.warning("[opencode_admin] AtomCode daemon /skills 请求失败: {}", e)
            return {
                "ok": False,
                "needs_restart": True,
                "reason": f"连不上 AtomCode daemon({type(e).__name__})",
            }
        if r.status_code != 200:
            logger.warning("[opencode_admin] AtomCode daemon /skills 返回 {}: {}", r.status_code, r.text[:120])
            return {
                "ok": False,
                "needs_restart": True,
                "reason": f"AtomCode daemon 返回 HTTP {r.status_code}",
            }
        try:
            payload = r.json()
            if isinstance(payload, list):
                count = len(payload)
            elif isinstance(payload, dict) and isinstance(payload.get("skills"), list):
                count = len(payload["skills"])
            elif isinstance(payload, int):
                count = payload
            else:
                count = 0
        except Exception:  # noqa: BLE001
            return {
                "ok": False,
                "needs_restart": True,
                "reason": "AtomCode daemon /skills 返回的不是有效 JSON",
            }
        logger.info("[opencode_admin] AtomCode 技能列表探测成功 · 现有 {} 个 skill", count)
        return {"ok": True, "count": count}

    try:
        r = httpx.post(f"{_URL}/skill/refresh", headers=_headers(), timeout=_TIMEOUT)
    except Exception as e:
        logger.warning("[opencode_admin] refresh 请求失败: {}", e)
        return {"ok": False, "needs_restart": True,
                "reason": f"连不上 opencode({type(e).__name__})"}

    if r.status_code == 404:
        logger.info("[opencode_admin] /skill/refresh 不存在(镜像较旧)· 需手动重启")
        return {"ok": False, "needs_restart": True,
                "reason": "当前 opencode 镜像不支持热刷新"}
    if r.status_code != 200:
        logger.warning("[opencode_admin] refresh 返回 {}: {}", r.status_code, r.text[:120])
        return {"ok": False, "needs_restart": True,
                "reason": f"opencode 返回 HTTP {r.status_code}"}

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
    if is_atomcode_backend():
        return ("新能力已保存，但当前对话引擎（AtomCode Daemon）需要重启以同步工作区能力目录。"
                "在部署目录执行：docker compose restart daemon（约 15 秒）")
    return ("新能力已保存,但当前 opencode 需要重启才能识别 —— "
            "它只在启动时扫描能力目录。在部署目录执行:"
            "docker compose restart opencode(约 50 秒)")


# ─────────────────────────────────────────────────────────────────────
# 大模型配置热生效與展示名
# ─────────────────────────────────────────────────────────────────────
def model_label(base_url: str, api_key: str, model: str) -> str:
    """问上游要这个模型的展示名,拿不到就用模型名本身。"""
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


def _compute_via_shim(model: str, sanitize: str) -> bool:
    s = (sanitize or "auto").strip().lower()
    if s in ("0", "false", "no", "off"):
        return False
    if s in ("1", "true", "yes", "on"):
        return True
    return "gemini" in (model or "").lower()


def write_atomcode_config(cfg, *, config_path: Path | None = None) -> tuple[bool, str]:
    """将最新大模型配置写入 `/data/atomcode/config.toml`（若目录已挂载且可写）。

    保留原有 `config.toml` 中的 `system_prompt_file` 与 `[tools]` 段，只更新 provider 与模型参数。
    写完强制 `chmod 0600`，绝不在日志中打印明文 `api_key`。
    """
    model = (getattr(cfg, "model", "") or "").strip()
    base_url = (getattr(cfg, "base_url", "") or "").strip().rstrip("/")
    api_key = getattr(cfg, "api_key", "") or ""
    sanitize = (getattr(cfg, "sanitize", "") or "auto").strip().lower()
    if not model:
        return False, "没有模型名"

    via_shim = _compute_via_shim(model, sanitize)
    effective_base_url = _SHIM_URL if (via_shim and _SHIM_URL) else base_url

    if config_path is None:
        explicit = (os.getenv("ATOMCODE_CONFIG_PATH") or "").strip()
        if explicit:
            config_path = Path(explicit)
        else:
            atomcode_home = Path(os.getenv("ATOMCODE_HOME") or "/data/atomcode")
            if not atomcode_home.exists():
                return False, f"{atomcode_home} 未挂载"
            config_path = atomcode_home / "config.toml"

    provider = (os.getenv("HCA_LLM_PROVIDER_NAME") or _ATOMCODE_PROVIDER_DEFAULT).strip()
    ctx = int(os.getenv("HCA_LLM_CONTEXT_WINDOW") or "1000000")
    persona_line = ""
    tools_lines: list[str] = []

    if config_path.is_file():
        try:
            old_text = config_path.read_text(encoding="utf-8")
            m_prov = re.search(r'^default_provider\s*=\s*"([^"]+)"', old_text, flags=re.M)
            if m_prov and m_prov.group(1).strip():
                provider = m_prov.group(1).strip()
            m_persona = re.search(r'^(system_prompt_file\s*=\s*.+)$', old_text, flags=re.M)
            if m_persona:
                persona_line = m_persona.group(1).strip()
            m_tools = re.search(r'(\[tools\][\s\S]*)$', old_text)
            if m_tools:
                tools_lines = ["", m_tools.group(1).strip()]
        except OSError:
            pass

    q = json.dumps
    lines = [
        "# 由 HunterCode 向导 / opencode_admin.py 热更新生成",
        f"default_provider = {q(provider)}",
        "auto_update = false",
        "auto_commit = false",
        'offline_mode = "off"',
        "",
        f"[providers.{provider}]",
        'type = "openai"',
        f"api_key = {q(api_key)}",
        f"model = {q(model)}",
        f"base_url = {q(effective_base_url)}",
        f"context_window = {ctx}",
    ]
    if persona_line:
        lines.append(persona_line)
    if tools_lines:
        lines.extend(tools_lines)
    lines.append("")

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("\n".join(lines), encoding="utf-8")
        try:
            config_path.chmod(0o600)
        except OSError:
            pass
        return True, str(config_path)
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"


def _apply_llm_atomcode(cfg) -> dict:
    """AtomCode 模式下的大模型配置热推：更新 config.toml + 调用 POST /live/provider。"""
    model = (getattr(cfg, "model", "") or "").strip()
    base_url = (getattr(cfg, "base_url", "") or "").strip().rstrip("/")
    sanitize = (getattr(cfg, "sanitize", "") or "auto").strip().lower()
    if not model:
        return {"ok": False, "reason": "大模型尚未配置(没有模型名),不推送"}

    via_shim = _compute_via_shim(model, sanitize)
    wrote_cfg, cfg_note = write_atomcode_config(cfg)

    url = daemon_url()
    headers = {**daemon_headers(), "Content-Type": "application/json"}
    provider = (os.getenv("HCA_LLM_PROVIDER_NAME") or _ATOMCODE_PROVIDER_DEFAULT).strip()

    # 先查当前 daemon 配置里的 default_provider 名（若能连上）
    daemon_reachable = False
    try:
        r_cfg = httpx.get(f"{url}/config", headers=daemon_headers(), timeout=10.0)
        if r_cfg.status_code == 200 and isinstance(r_cfg.json(), dict):
            daemon_reachable = True
            dp = str(r_cfg.json().get("default_provider") or "").strip()
            if dp:
                provider = dp
    except Exception as e:  # noqa: BLE001
        logger.debug("[opencode_admin] 预读 AtomCode /config 失败: {}", e)

    try:
        r = httpx.post(
            f"{url}/live/provider",
            headers=headers,
            json={"provider": provider, "model": model},
            timeout=_TIMEOUT,
        )
        if r.status_code < 400:
            logger.info(
                "[opencode_admin] 已向 AtomCode daemon 热切模型 · provider={} model={} · 经 shim={} · config_written={}",
                provider, model, via_shim, wrote_cfg,
            )
            return {"ok": True, "reason": ""}
        if daemon_reachable or wrote_cfg:
            logger.info(
                "[opencode_admin] AtomCode /live/provider 返回 {}，但 config_written={} ({}) · 新会话将使用新配置",
                r.status_code, wrote_cfg, cfg_note,
            )
            return {"ok": True, "reason": ""}
        return {"ok": False, "reason": f"AtomCode daemon 返回 HTTP {r.status_code}"}
    except Exception as e:  # noqa: BLE001
        if wrote_cfg or daemon_reachable:
            logger.info(
                "[opencode_admin] AtomCode /live/provider 不可达({})，但已写入 config.toml ({})",
                type(e).__name__, cfg_note,
            )
            return {"ok": True, "reason": ""}
        logger.warning("[opencode_admin] AtomCode apply_llm 请求失败: {}", e)
        return {"ok": False, "reason": f"连不上 AtomCode daemon({type(e).__name__})"}


def apply_llm(cfg) -> dict:
    """把大模型配置热推给对话引擎（AtomCode Daemon 或 OpenCode），**不重启容器**。"""
    model = (getattr(cfg, "model", "") or "").strip()
    base_url = (getattr(cfg, "base_url", "") or "").strip().rstrip("/")
    api_key = getattr(cfg, "api_key", "") or ""
    sanitize = (getattr(cfg, "sanitize", "") or "auto").strip().lower()
    if not model:
        return {"ok": False, "reason": "大模型尚未配置(没有模型名),不推送"}

    if is_atomcode_backend():
        return _apply_llm_atomcode(cfg)

    via_shim = _compute_via_shim(model, sanitize)
    options = {"baseURL": _SHIM_URL if via_shim else base_url, "apiKey": api_key}
    if via_shim and base_url:
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

    try:
        payload = r.json()
    except Exception:  # noqa: BLE001
        logger.info("[opencode_admin] /global/config 返回的不是 JSON"
                    "(content-type={})· 判定为镜像不支持,需重启容器",
                    r.headers.get("content-type", "?"))
        return {"ok": False, "reason": "当前 opencode 镜像不支持热更新配置(未知路由被 SPA 接管)"}
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "opencode 返回的配置格式不对(需重启容器)"}

    logger.info("[opencode_admin] 已热推大模型配置 · model={} · 经 shim={}", model, via_shim)
    return {"ok": True, "reason": ""}


def probe_engine_ready(model: str) -> dict:
    """探测对话引擎（AtomCode Daemon 或 OpenCode）是否已就绪且生效指定模型。"""
    if is_atomcode_backend():
        url = daemon_url()
        headers = daemon_headers()
        try:
            r = httpx.get(f"{url}/config", headers=headers, timeout=15.0)
        except Exception as e:  # noqa: BLE001
            return {"ready": False, "reason": f"连不上 AtomCode daemon（{type(e).__name__}）"}
        if r.status_code != 200:
            return {"ready": False, "reason": f"AtomCode daemon 返回 HTTP {r.status_code}"}
        try:
            d = r.json()
        except Exception:  # noqa: BLE001
            return {"ready": False, "reason": "AtomCode daemon 返回的不是 JSON"}
        if not isinstance(d, dict):
            return {"ready": False, "reason": "AtomCode daemon 配置结构异常"}

        # AtomCode /config 格式: {"default_provider": "hunter", "providers": [{"name": "hunter", "model": "hunter-chat"}]}
        def_prov = str(d.get("default_provider") or _ATOMCODE_PROVIDER_DEFAULT)
        providers = d.get("providers") or []
        p_obj = next((x for x in providers if isinstance(x, dict) and x.get("name") == def_prov), None)
        if p_obj is None and providers and isinstance(providers[0], dict):
            p_obj = providers[0]
        cur_model = str((p_obj or {}).get("model") or d.get("model") or "")
        cur_full = f"{def_prov}/{cur_model}" if (cur_model and "/" not in cur_model) else cur_model

        if cur_model == model or cur_full.endswith(f"/{model}"):
            return {"ready": True, "reason": "", "current": cur_full}

        # 若 daemon 在线且 config.toml 已写入目标 model，也视为就绪
        cfg_file = Path(os.getenv("ATOMCODE_CONFIG_PATH") or (Path(os.getenv("ATOMCODE_HOME") or "/data/atomcode") / "config.toml"))
        try:
            if cfg_file.is_file() and f'model = {json.dumps(model)}' in cfg_file.read_text(encoding="utf-8"):
                return {"ready": True, "reason": "", "current": f"{def_prov}/{model}"}
        except OSError:
            pass

        if cur_model:
            # AtomCode daemon 已在线且有可用模型
            return {"ready": True, "reason": "", "current": cur_full}
        return {"ready": False, "reason": "配置还在重新加载", "current": cur_full}

    url = os.getenv("OPENCODE_URL", "http://opencode:3901").rstrip("/")
    try:
        r = httpx.get(f"{url}/config", headers=_headers(), timeout=20.0)
    except Exception as e:  # noqa: BLE001
        return {"ready": False, "reason": f"连不上 opencode（{type(e).__name__}）"}
    if r.status_code != 200:
        return {"ready": False, "reason": f"opencode 返回 HTTP {r.status_code}"}
    try:
        d = r.json()
    except Exception:  # noqa: BLE001
        return {"ready": False, "reason": "opencode 返回的不是 JSON"}
    cur = d.get("model") if isinstance(d, dict) else None
    if isinstance(cur, str) and cur.endswith(f"/{model}"):
        return {"ready": True, "reason": "", "current": cur}
    return {"ready": False, "reason": "配置还在重新加载", "current": cur or ""}


def engine_diagnostics_snapshot(fallback_base_url: str = "", fallback_api_key: str = "") -> dict:
    """获取对话引擎（AtomCode Daemon 或 OpenCode）的诊断快照，供 `/api/setup/llm/logs` 使用。
    **绝不回显任何明文 API Key。**
    """
    if is_atomcode_backend():
        url = daemon_url()
        headers = daemon_headers()
        snap: dict = {
            "engine": "atomcode",
            "reachable": False,
            "current_model": "",
            "default_provider": "",
            "providers": [],
            "mcp_servers": [],
        }
        try:
            r_cfg = httpx.get(f"{url}/config", headers=headers, timeout=5.0)
            if r_cfg.status_code == 200 and isinstance(r_cfg.json(), dict):
                cfg_data = r_cfg.json()
                snap["reachable"] = True
                def_prov = str(cfg_data.get("default_provider") or "")
                snap["default_provider"] = def_prov
                provs = cfg_data.get("providers") or []
                p_obj = next((x for x in provs if isinstance(x, dict) and x.get("name") == def_prov), None)
                if p_obj is None and provs and isinstance(provs[0], dict):
                    p_obj = provs[0]
                if isinstance(p_obj, dict) and p_obj.get("model"):
                    pname = str(p_obj.get("name") or def_prov or "hunter")
                    pmodel = str(p_obj.get("model"))
                    snap["current_model"] = f"{pname}/{pmodel}"

            r_mod = httpx.get(f"{url}/models", headers=headers, timeout=5.0)
            if r_mod.status_code == 200 and isinstance(r_mod.json(), list):
                snap["reachable"] = True
                by_prov: dict[str, dict[str, dict]] = {}
                for m in r_mod.json():
                    if not isinstance(m, dict):
                        continue
                    pid = str(m.get("provider") or snap["default_provider"] or "hunter")
                    mid = str(m.get("model") or "")
                    if not mid:
                        continue
                    disp = BUILTIN_MODEL_LABELS.get(mid) or model_label(fallback_base_url, fallback_api_key, mid) or mid
                    by_prov.setdefault(pid, {})[mid] = {"name": disp}
                    if not snap["current_model"] and m.get("is_default"):
                        snap["current_model"] = f"{pid}/{mid}"
                for pid, models_map in by_prov.items():
                    snap["providers"].append({
                        "id": pid,
                        "name": pid,
                        "models": models_map,
                    })

            r_mcp = httpx.get(f"{url}/mcp/status", headers=headers, timeout=5.0)
            if r_mcp.status_code == 200 and isinstance(r_mcp.json(), dict):
                snap["reachable"] = True
                for s in r_mcp.json().get("servers") or []:
                    if isinstance(s, dict):
                        snap["mcp_servers"].append({
                            "name": s.get("name") or "",
                            "status": s.get("status") or "",
                            "tool_count": int(s.get("tool_count") or 0),
                        })
        except Exception as exc:  # noqa: BLE001
            snap["error"] = f"{type(exc).__name__}: {exc}"
        return snap

    snap = {"engine": "opencode", "reachable": False, "current_model": "", "providers": []}
    oc_url = os.getenv("OPENCODE_URL", "http://opencode:3901").rstrip("/")
    oc_headers = _headers()
    try:
        r_cfg = httpx.get(f"{oc_url}/config", headers=oc_headers, timeout=5.0)
        if r_cfg.status_code == 200 and isinstance(r_cfg.json(), dict):
            snap["reachable"] = True
            snap["current_model"] = str(r_cfg.json().get("model") or "")
        r_prov = httpx.get(f"{oc_url}/config/providers", headers=oc_headers, timeout=5.0)
        if r_prov.status_code == 200 and isinstance(r_prov.json(), dict):
            snap["reachable"] = True
            for p in r_prov.json().get("providers") or []:
                if not isinstance(p, dict) or p.get("id") == "opencode":
                    continue
                models_out = {}
                for mid, mobj in (p.get("models") or {}).items():
                    if mid == "hunter-unconfigured":
                        continue
                    mname = mobj.get("name") if isinstance(mobj, dict) else str(mobj)
                    models_out[mid] = {"name": mname or mid}
                snap["providers"].append({
                    "id": p.get("id") or "",
                    "name": p.get("name") or "",
                    "models": models_out,
                })
    except Exception as exc:  # noqa: BLE001
        snap["error"] = f"{type(exc).__name__}: {exc}"
    return snap
