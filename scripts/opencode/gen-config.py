#!/usr/bin/env python3
"""写 opencode 的 provider / mcp / skills 配置 · 在 opencode 容器里启动前跑。

为什么非要生成文件:opencode 只从配置文件读 provider,不认 LLM_* 环境变量。
**不写的话它会回落到内置的 OpenCode Zen** —— 你在 .env 里填的网关根本不会被调用,
用户的问题被发给第三方免费模型,现象是「能聊天但答得驴唇不对马嘴」。
所以哪怕大模型一项都没配,这里也**必须**写出一个指向 llm-shim 的 provider。

## 配置从哪来(M1 · 设计方案 3.3)

1. 环境变量三项(LLM_BASE_URL / LLM_API_KEY / LLM_DEFAULT_MODEL)都非空 → 用它,
   来源标记 **env**
2. 否则问 api:`GET /api/internal/runtime/llm`(带 HUNTER_INTERNAL_KEY,10 秒超时、
   最多 3 次)。拿到 configured=true → 来源标记 **db**(初始化向导填的)
3. 都没有 → 照样生成配置并启动,provider 指向 llm-shim、模型名写占位
   `hunter-unconfigured`,来源标记 **none**。shim 会立刻返回一句中文的
   「大模型尚未配置」,用户看得见,而不是对话转圈一百多秒(R0 §1.5 实测)

## 写到哪(R0 §1.2/1.3 实测结论 · 与原设计方案不同,以实测为准)

opencode 的配置合并顺序是:
    全局 ~/.config/opencode/{config,opencode}.json(c)
      < OPENCODE_CONFIG
      < 从实例目录向上找到的 opencode.json(c)      ← gen-config 写的项目文件
      < .opencode/opencode.json(c)
而向导热生效用的 `PATCH /global/config` **只改全局文件**。所以:

| 内容 | 写到哪 | 为什么 |
|---|---|---|
| mcp · instructions | 项目 /opt/opencode-workspace/opencode.json | 与大模型无关,不需要热改 |
| provider/model · 来源 env | 项目 opencode.json | 项目文件优先级最高,天然实现「环境变量锁定」,向导改不动 |
| provider/model · 来源 db/none | 全局 ~/.config/opencode/opencode.jsonc | 只有这里能被 PATCH /global/config 热改 |
| skills.urls | 全局 opencode.jsonc | 同上(向导/SKILL 同步也走全局) |

⚠️ 来源是 env 时**必须保证全局文件里没有残留的 provider/model**,否则上一次
向导写进去的老配置会跟环境变量打架(R0 实测过「provider 合并进来了、model 没变」
这种一半生效的状态)。做法:每次启动把全局文件**整份重写**成一个干净状态 ——
顺带也清掉 `PATCH /global/config` 用 mergeDeep 累积下来的旧模型名(R0 §1.5)。

(踩过的坑:最初项目文件里连 mcp 也写了一遍,结果 hunter_user_mcp.py 被起了两次 ——
镜像叫它 hunter_user、我叫它 usermcp,名字不同就没去重。)

Reads:  LLM_BASE_URL · LLM_API_KEY · LLM_DEFAULT_MODEL · LLM_SCHEMA_SANITIZE
        HUNTER_INTERNAL_KEY · HERMES_API_URL · LLM_SHIM_URL
        HUNTER_EXTRA_MCP_DIR · HUNTER_MCP_TIMEOUT_MS(可选 · 覆盖额外 MCP 的超时)
        /opt/hunter-secrets/secrets.env(环境变量为空时的密钥来源)
Writes: $OPENCODE_CONFIG_DIR/opencode.json (默认 /opt/opencode-workspace)
        $XDG_CONFIG_HOME/opencode/opencode.jsonc(回落 ~/.config/opencode/)
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

WORKSPACE = os.environ.get("OPENCODE_CONFIG_DIR", "/opt/opencode-workspace")

PROVIDER_ID = "hunter-llm"
# 一项都没配时用的占位模型名。llm-shim 认得它,会立刻回一条中文错误。
# **不要改成一个真模型名** —— 那会让「没配置」看起来像「配错了」。
PLACEHOLDER_MODEL = "hunter-unconfigured"

SHIM_URL = os.environ.get("LLM_SHIM_URL", "http://llm-shim:3999/v1").rstrip("/")
API_URL = (os.environ.get("HERMES_API_URL") or "http://api:8000").rstrip("/")

# 密钥卷(设计方案 3.2)· 环境变量为空时从这里补。
# 正常路径是 entrypoint.sh 先 `. /opt/hunter-boot/load-secrets.sh`(那样 opencode
# 进程本身也拿得到 JWT_SECRET);这里再读一次是为了让本脚本自己一定能用上
# HUNTER_INTERNAL_KEY —— 它决定了能不能向 api 拉配置、skills.urls 写不写得出来。
SECRETS_FILE = os.environ.get(
    "HUNTER_SECRETS_FILE",
    os.path.join(os.environ.get("HUNTER_SECRETS_DIR", "/opt/hunter-secrets"), "secrets.env"),
)

_TIMEOUT_OVERRIDE_RAW = (os.environ.get("HUNTER_MCP_TIMEOUT_MS") or "").strip()


def _log(msg: str) -> None:
    print(f"[gen-config] {msg}", file=sys.stderr, flush=True)


def load_secrets_file(path: str = SECRETS_FILE) -> list:
    """把 secrets.env 里的 KEY=value 填进 os.environ —— **只填空的那些**。

    优先级是「环境变量非空 → 密钥卷」,反过来就会让云平台注入的值被本地卷盖掉。
    返回这次从文件里补上的键名(只用于日志,不打印值)。
    """
    filled = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return filled
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if not v or (os.environ.get(k) or "").strip():
            continue
        os.environ[k] = v
        filled.append(k)
    return filled


def _timeout_ms(default_ms: int) -> int:
    """下面 _EXTRA_MCP 里那两个 timeout 是按「这个工具最慢能有多慢」定的。换了更慢的
    后端(比如自己跑 Kronos 推理、或者上游网关排队)就得整体抬高 —— 做成环境变量,
    免得为了改一个数字去 fork 这个文件。给非数字 / 非正数时按原值走。"""
    if not _TIMEOUT_OVERRIDE_RAW:
        return default_ms
    try:
        val = int(_TIMEOUT_OVERRIDE_RAW)
    except ValueError:
        _log(f"HUNTER_MCP_TIMEOUT_MS={_TIMEOUT_OVERRIDE_RAW!r} 不是整数,按默认 {default_ms}ms 走")
        return default_ms
    if val <= 0:
        _log(f"HUNTER_MCP_TIMEOUT_MS={_TIMEOUT_OVERRIDE_RAW!r} 非正数,按默认 {default_ms}ms 走")
        return default_ms
    return val


def _opencode_version() -> str:
    """给启动日志用。新镜像是单文件二进制;旧镜像没有 opencode 命令,只能标注不详。"""
    exe = shutil.which("opencode")
    if not exe:
        return "未知(旧镜像 · 源码启动)"
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"未知({type(exc).__name__})"
    return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else "未知"


# ── 取配置 ──────────────────────────────────────────────────────────
def _env_cfg():
    base = (os.environ.get("LLM_BASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("LLM_API_KEY") or "").strip()
    model = (os.environ.get("LLM_DEFAULT_MODEL") or "").strip()
    if not (base and key and model):
        return None
    return {
        "base_url": base,
        "api_key": key,
        "model": model,
        # ⚠️ 判断用「非空」:compose 的 ${X:-} 会把未设置注入成空字符串
        "sanitize": (os.environ.get("LLM_SCHEMA_SANITIZE") or "auto").strip().lower(),
    }


# api 还没起来时最多等多久(秒)。0 = 不等。
#
# 为什么要等、又为什么不能无脑等:
#   · 本地 compose 有 `depends_on: service_healthy`,opencode 起来时 api 一定在,
#     **api 会立刻回话**,这个预算一秒都用不上。
#   · **云平台大多没有启动顺序编排**(Railway 官方就是六个服务同时起)。api 要跑完
#     数据库迁移才监听,实测几十秒。原来的「重试 3 次、1+2 秒」在那里必然打空,
#     opencode 于是带着占位 provider 起来 —— 用户明明在向导里配好了,
#     **每次重新部署之后发消息都回「大模型尚未配置」**,而日志里只有三行连接被拒。
#
# 关键是**只对「连不上」重试**:api 一旦回话(哪怕回的是 configured=false),
# 立刻按它说的办,不浪费一秒 —— 全新安装的启动速度一点不受影响。
CONFIG_WAIT = float(os.environ.get("HUNTER_CONFIG_WAIT") or 90)


def _api_cfg(budget: float | None = None, timeout: float = 10.0):
    """向 api 要当前生效的配置。拿不到返回 None(不抛)。

    api 连不上时按 CONFIG_WAIT 的预算退避重试;api 一回话就立刻返回。
    """
    key = (os.environ.get("HUNTER_INTERNAL_KEY") or "").strip()
    if not key:
        _log("HUNTER_INTERNAL_KEY 为空 —— 不向 api 拉配置(没有口令拉不到,也不该拉)")
        return None
    url = f"{API_URL}/api/internal/runtime/llm"
    budget = CONFIG_WAIT if budget is None else budget
    deadline = time.monotonic() + budget
    attempt = 0
    while True:
        attempt += 1
        try:
            req = urllib.request.Request(url, headers={"X-Hunter-Internal-Key": key})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001
            detail = f"{type(e).__name__}: {e}"
            if isinstance(e, urllib.error.HTTPError) and e.code == 401:
                _log(f"api 返回 401 —— opencode 与 api 的 HUNTER_INTERNAL_KEY 不一致。{detail}")
                return None
            left = deadline - time.monotonic()
            if left <= 0:
                _log(f"第 {attempt} 次拉配置失败,已等满 {budget:.0f} 秒,放弃({detail})")
                _log("  api 起得比这还慢的话,把 HUNTER_CONFIG_WAIT 调大;"
                     "或者在向导里重新点一次「应用」也能热生效。")
                return None
            wait = min(5.0, attempt, left)
            _log(f"第 {attempt} 次拉配置失败,{wait:.0f} 秒后重试(还剩 {left:.0f} 秒预算)({detail})")
            time.sleep(wait)
            continue
        if attempt > 1:
            _log(f"api 第 {attempt} 次尝试时回话了")
        if not body.get("configured"):
            _log("api 说大模型还没配置(向导还没跑)")
            return None
        return {
            "base_url": (body.get("base_url") or "").strip().rstrip("/"),
            "api_key": (body.get("api_key") or "").strip(),
            "model": (body.get("model") or "").strip(),
            "sanitize": (body.get("sanitize") or "auto").strip().lower(),
        }
    return None


def resolve_llm():
    """→ (cfg, source)。source ∈ {"env", "db", "none"}。"""
    cfg = _env_cfg()
    if cfg:
        return cfg, "env"
    cfg = _api_cfg()
    if cfg:
        return cfg, "db"
    return {
        "base_url": "",
        # 占位:openai-compatible SDK 不接受空 apiKey。这不是一把真 key,
        # 日志里照实说「apiKey 无」。
        "api_key": PLACEHOLDER_MODEL,
        "model": PLACEHOLDER_MODEL,
        "sanitize": "auto",
    }, "none"


def _use_shim(cfg: dict, source: str) -> bool:
    """Gemini 只认 OpenAPI 子集的 tool schema;opencode 送的是完整 JSON Schema。
    不洗一遍的话每条消息都会被上游拒:
        Invalid JSON payload received. Unknown name "$schema" at ... parameters
    而 OpenAI 的 strict function calling 反而**需要** additionalProperties,
    洗掉会削弱它。所以默认 auto:只有模型名含 gemini 才绕这一层。

    没配置时一律走 shim —— 它是那句中文提示的出口,而且这样请求不会离开这台机器。
    """
    if source == "none":
        return True
    s = cfg["sanitize"]
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return "gemini" in cfg["model"].lower()


def _model_label(cfg: dict) -> str:
    """问网关要这个模型的展示名(内置额度下别名背后的真实模型),拿不到就用模型名。

    与 api 侧 opencode_admin.model_label() **必须同口径** —— 不然重启一次容器,
    选择器里的名字就换了一副样子。超时 3 秒、失败即回退,标签不值得卡住启动。
    """
    model = cfg.get("model") or ""
    base = (cfg.get("base_url") or "").rstrip("/")
    if not base or not model or model == PLACEHOLDER_MODEL:
        return model
    req = urllib.request.Request(f"{base}/models", method="GET")
    if cfg.get("api_key"):
        req.add_header("Authorization", f"Bearer {cfg['api_key']}")
    try:
        with urllib.request.urlopen(req, timeout=3.0) as r:
            data = json.loads(r.read().decode("utf-8") or "{}")
        for m in data.get("data") or []:
            if m.get("id") == model:
                return (m.get("display_name") or "").strip() or model
    except Exception as e:  # noqa: BLE001
        _log(f"取模型展示名失败(不影响功能): {type(e).__name__}")
    return model


def provider_block(cfg: dict, source: str) -> dict:
    """provider + model 两个键。与 opencode_admin.apply_llm() 写的结构保持一致。"""
    via_shim = _use_shim(cfg, source)
    options = {
        "baseURL": SHIM_URL if via_shim else cfg["base_url"],
        "apiKey": cfg["api_key"],
    }
    if via_shim and cfg["base_url"]:
        # 真正的上游地址走请求头传给 shim。**不能只靠 shim 自己的 LLM_BASE_URL**:
        # 配置来自数据库时 shim 容器的环境变量是空的,不带这个头它不知道往哪转发。
        # 没配置时不写这个头 —— 空值只会让 shim 多做一次没意义的校验。
        options["headers"] = {"X-Hunter-Upstream": cfg["base_url"]}
    return {
        "provider": {
            PROVIDER_ID: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Hunter LLM",
                "options": options,
                # 只声明配置里的那一个模型。opencode 要求每个模型显式声明,
                # 多列网关未必提供的模型会在选择器里留下一堆死条目。
                "models": {cfg["model"]: {"name": _model_label(cfg)}},
            }
        },
        "model": f"{PROVIDER_ID}/{cfg['model']}",
    }


def global_config_path() -> str:
    base = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "opencode", "opencode.jsonc")


def _project_config(cfg: dict, source: str) -> dict:
    out = {"$schema": "https://opencode.ai/config.json"}
    if source == "env":
        # 环境变量锁定:写在优先级最高的项目文件里,向导即使误调也改不动
        out.update(provider_block(cfg, source))

    # ── 项目指令(2026-08-18)──────────────────────────────────
    #
    # **必须挂,否则模型读的是 opencode 自己的 AGENTS.md。**
    #
    # opencode 默认把工作区根的 `AGENTS.md` 当项目指令,而我们的工作区
    # `/opt/opencode-workspace` 就是 opencode 的源码树 —— 那份 AGENTS.md
    # 讲的是「怎么给 opencode 提 PR」。于是模型的项目上下文一直是"你在给
    # opencode 做贡献":用英文、用开发者口吻回答金融问题,而且**会宣称自己
    # 做了没做的事**(实测:说"已暂存 12 个 SKILL",而 skill_stage 一次没调)。
    #
    # instructions 是**追加**不是替换 —— opencode 仍会读 AGENTS.md,
    # 但我们这份排在后面,冲突时更靠后的更有分量。
    agent_md = os.path.join(os.path.dirname(os.path.abspath(__file__)), "HUNTER-AGENT.md")
    if os.path.exists(agent_md):
        out["instructions"] = [agent_md]
    else:
        # 不静默跳过 —— 缺了这份文件,模型会退回"给 opencode 做贡献"那套上下文,
        # 而那个症状(英文回答、假报工具调用)完全指不到这里
        _log(f"⚠ 缺 {agent_md} —— 模型将退回读 opencode 自身的 AGENTS.md,回答会跑偏")

    # 只注册**镜像里没有**的那个 MCP。镜像自带的 .opencode/opencode.jsonc 已经
    # 注册了 watchlist/portfolio/uzi/hunter_user 四个,两份配置会合并 ——
    # 在这里重复写会让同一个脚本被起两次(2026-08-14 踩过,见 cd427bf)。
    extra = os.environ.get("HUNTER_EXTRA_MCP_DIR", "/opt/hunter-mcp")
    # (注册名, 文件名, timeout_ms)
    #
    # ── 数据类 MCP ──（行情 / 选股 / 自选 / 能力库…，只读或写用户自己的数据）
    #   hunter_cap · kpred 是 GPU 推理、scout 是 30-60s 主动采集,30s 默认会被掐断
    #   screener   · 全市场扫描 · 拉全市场实测 1~3s,60s 足够(留上游抖动余量)
    _DATA_MCP = [
        ("hunter_cap", "hunter_capability_mcp.py", 180000),
        ("screener",   "screener_mcp.py",           60000),
    ]
    # ── 控制类 MCP ──（L08 · 五期方案 §6.3「控制通道与数据通道分离」）
    #   单独一个 server（`runtime_mcp.py`），四个工具 = runtime.workflow_start/get/pause/cancel。
    #   **与上面数据类分开登记**：它只操作 Temporal（启动 / 查询 / 暂停 / 取消工作流），
    #   不返回任何行情数据；注册名以 `runtime_` 起头，在生成的配置里一眼与数据类区分。
    #   它转发到 fin-worker 的 `/internal/runtime/*`（白名单模板 + 类型化参数，不是后门）。
    _CONTROL_MCP = [
        ("runtime_control", "runtime_mcp.py", 60000),
    ]
    mcp_cfg = {}
    for reg_name, fname, timeout_ms in (_DATA_MCP + _CONTROL_MCP):
        path = os.path.join(extra, fname)
        if os.path.exists(path):
            mcp_cfg[reg_name] = {
                "type": "local",
                "command": ["python3", path],
                "enabled": True,
                "timeout": _timeout_ms(timeout_ms),
            }
    if mcp_cfg:
        out["mcp"] = mcp_cfg
    return out


def _global_config(cfg: dict, source: str) -> dict:
    """全局配置 · **每次启动整份重写**(见文件头)。

    这里只放两样东西:能被向导热改的 provider/model,和 SKILL 同步的 URL。
    整份重写是刻意的:既清掉上一次运行留下的 provider/model(来源换成 env 时
    必须清),也清掉 `PATCH /global/config` 用 mergeDeep 累积的旧模型名。
    """
    out = {"$schema": "https://opencode.ai/config.json"}
    if source != "env":
        out.update(provider_block(cfg, source))
    key = (os.environ.get("HUNTER_INTERNAL_KEY") or "").strip()
    if key:
        # SKILL 同步(子任务 D 的接口)。鉴权放在 URL 路径段里是没办法的事:
        # opencode 的 Discovery.pull 用裸 GET,带不了自定义头(R0 §2.3)。
        # **末尾那个 / 不能少** —— 它用 new URL("index.json", base) 拼地址,
        # 少一个斜杠会把最后一段路径吃掉。
        out["skills"] = {"urls": [f"{API_URL}/api/internal/skills/{key}/"]}
    return out


def _write_json(path: str, obj: dict) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        return True
    except OSError as e:
        _log(f"⚠ 写不了 {path}({type(e).__name__}: {e})")
        return False


def main() -> int:
    filled = load_secrets_file()
    if filled:
        _log(f"从密钥卷补上环境变量: {' · '.join(filled)}(只补空的,不打印值)")

    cfg, source = resolve_llm()
    src_cn = {"env": "环境变量", "db": "数据库(初始化向导)", "none": "尚未配置"}[source]

    project_path = os.path.join(WORKSPACE, "opencode.json")
    global_path = global_config_path()
    written = []
    if _write_json(project_path, _project_config(cfg, source)):
        written.append(project_path)
    if _write_json(global_path, _global_config(cfg, source)):
        written.append(global_path)
    if not written:
        # 两个文件一个都没写成 —— opencode 会回落到 OpenCode Zen,把用户的问题
        # 发给第三方免费模型。这是本脚本存在的全部意义,写不出就不能装作没事。
        _log("✗ 项目与全局配置都没写成,opencode 会回落到内置 OpenCode Zen —— 拒绝启动")
        return 1

    via_shim = _use_shim(cfg, source)
    # 一行把「跑的是哪个 opencode / MCP 超时多少 / 走哪个 provider」讲清楚。
    # 这三样出问题的症状都是「对话没反应」,而日志里以前一个都看不到。
    print(f"[boot] opencode {_opencode_version()}"
          f" · mcp timeout {_timeout_ms(180000)}ms"
          f"({'HUNTER_MCP_TIMEOUT_MS' if _TIMEOUT_OVERRIDE_RAW else '默认'})"
          f" · provider {PROVIDER_ID} → {SHIM_URL if via_shim else cfg['base_url']}",
          file=sys.stderr, flush=True)
    _log(f"配置来源:{src_cn}")
    _log(f"  已写 {' 与 '.join(written)}")
    _log(f"  provider {PROVIDER_ID} → {SHIM_URL if via_shim else cfg['base_url']}"
         + (f"(经 schema shim → {cfg['base_url'] or '(无上游)'})" if via_shim else "")
         + f" · model {cfg['model']}"
         + f" · apiKey {'有' if (source != 'none' and cfg['api_key']) else '无'}")
    _log(f"  provider/model 写在{'项目文件(环境变量锁定,向导改不动)' if source == 'env' else '全局文件(向导可热改)'}")
    if source == "none":
        _log("  ⚠ 大模型尚未配置 —— opencode 照常启动,但发消息会收到 llm-shim 的"
             "「大模型尚未配置」提示。打开浏览器访问这台实例的 /setup 走一遍首启向导即可"
             "(不用改任何文件);或在 .env 里配 LLM_BASE_URL / LLM_API_KEY / LLM_DEFAULT_MODEL。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
