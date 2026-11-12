#!/usr/bin/env python3
"""daemon 容器的启动前初始化：铺工作区 + 渲染 config.toml。

由 `deploy/daemon/entrypoint.sh` 在起 daemon **之前**调用。做两件事：

## 1. 把工作区模板铺进 $HCA_WORKSPACE

`distro/workspace-template/` 在镜像里是 `/opt/hca/workspace-template/`（只读原件），
工作区 `/workspace` 是数据卷。两类文件区别对待：

* **发行版托管**（`.atomcode.md` `.mcp.json` `.hooks.json` `.hooks/`
  `.atomcode/skills/` `README.md` `requirements.txt`）—— 每次启动重铺，
  这样升级镜像就等于升级技能和 MCP 清单，不用用户手动合并。
  `HCA_WORKSPACE_REFRESH=0` 可以关掉（自己改过这些文件的人用）。
* **用户资产**（`holdings/` `theses/` `factors/` `reports/` `scripts/`）——
  **只在不存在时铺一次**，之后再也不碰。这些是用户的研究资料，重铺等于删数据。

`.hooks.json` 里的 `${VAR}` / `${VAR:-默认值}` 在这一步替换成容器环境变量。

**`.mcp.json` 不在这一步替换，原样铺过去** —— AtomCode 自己会展开
（上游 `mcp/config.rs:315-336`，`command` / `args` / `env` 三处都走 `expand_env_vars`，
支持的正是 `${VAR}` 与 `${VAR:-默认值}` 这两种写法，`config.rs:797+` 有对应单测）。
交给它展开比我们先渲染好：**kronos / truesource / hunter 系那几把 key 就不会以明文
落进工作区数据卷**，只在 daemon 进程的环境里存在。

`.hooks.json` 则必须我们自己渲染：hook 的 `command` **没有**这一层展开
（`cc_hooks.rs` 直接拿字符串当命令），而且那个文件是严格 JSON、连 `//` 注释都不许有。

⚠️ 一个已知取舍：AtomCode 的 `/live/permission` 有个 `allow_persist`，会把工具写进
**工作区** `.mcp.json` 的 `autoApprove`。默认重铺会把这种运行时追加冲掉 ——
模板里已经把所有工具都预置进 `autoApprove` 了，正常用不到 `allow_persist`；
真要留住就设 `HCA_WORKSPACE_REFRESH=0`。

## 2. 渲染 ~/.atomcode/config.toml

provider 只有一个（OpenAI 兼容），地址/模型/key 全来自环境变量
（已拍板决策 3：默认 OneAPI · Gemini 3.8，也允许自建地址）。key 支持走
`HCA_LLM_API_KEY_FILE` 从文件读 —— compose 里用 secrets / 挂载文件时不用把
密钥塞进环境变量，`docker inspect` 就看不到。

写完 chmod 0600。**任何情况下都不打印 key**，只打印长度和前缀。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

TEMPLATE_DIR = Path(os.environ.get("HCA_TEMPLATE_DIR", "/opt/hca/workspace-template"))
WORKSPACE = Path(os.environ.get("HCA_WORKSPACE", "/workspace"))
ATOMCODE_HOME = Path(os.environ.get("ATOMCODE_HOME", "/data/atomcode"))

# 每次启动重铺的「发行版托管」条目（相对模板根）
MANAGED = [
    ".atomcode.md",
    ".mcp.json",
    ".hooks.json",
    ".hooks",
    ".atomcode/skills",
    "README.md",
    "requirements.txt",
]
# 只在缺失时铺一次的用户资产目录
SEED_ONCE = ["holdings", "theses", "factors", "reports", "scripts"]

# ${VAR} 与 ${VAR:-默认值}
VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def render_vars(text: str) -> tuple[str, list[str]]:
    """替换 ${VAR} / ${VAR:-默认}。返回 (结果, 用了默认值的变量名)。"""
    fell_back: list[str] = []

    def sub(m: re.Match[str]) -> str:
        name, default = m.group(1), m.group(2)
        val = os.environ.get(name)
        if val is None or val == "":
            if default is None:
                fell_back.append(f"{name}（无默认值，渲染成空串）")
                return ""
            fell_back.append(name)
            return default
        return val

    return VAR_RE.sub(sub, text), fell_back


def copy_tree(src: Path, dst: Path) -> int:
    """整目录覆盖复制，返回文件数。目标里多出来的文件删掉 ——
    否则上一版留下的技能目录会一直赖着，`GET /skills` 里多出一个幽灵技能。"""
    n = 0
    wanted: set[Path] = set()
    for f in sorted(src.rglob("*")):
        if f.is_dir():
            continue
        target = dst / f.relative_to(src)
        wanted.add(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        n += 1
    if dst.is_dir():
        for f in sorted(dst.rglob("*")):
            if f.is_file() and f not in wanted:
                f.unlink()
    return n


# ── MCP 子集开关（I2）──────────────────────────────────────────────────────
#
# `HCA_MCP_DISABLE="akshare,kronos"` —— 铺 `.mcp.json` 时把这几个 server 去掉。
# 两个用途：
#   1. I2 的 A 线对照：把 HCA 的工具清单压成与社区版相同的 4 个，
#      这样两边比的才是**引擎**，不是「谁挂的数据源多」。
#   2. 产品侧：用不上的数据源关掉能省下一整块工具 schema
#      （实测 9 个 MCP 的 28 个工具在每轮请求里占 2 万多字节）。
# 不设这个变量时**一个字都不改**，原样铺过去（保留注释、由 AtomCode 展开 ${VAR}）。


def _strip_jsonc(text: str) -> str:
    """剥掉 .mcp.json 里的 // 注释。只在需要重写这个文件时才用。

    逐行处理并跳过字符串内部的 `//`（URL 里的 `https://` 是最常见的一个）。
    """
    out = []
    for line in text.splitlines():
        res, in_str, esc, i = [], False, False, 0
        while i < len(line):
            c = line[i]
            if in_str:
                res.append(c)
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
                res.append(c)
            elif c == "/" and i + 1 < len(line) and line[i + 1] == "/":
                break
            else:
                res.append(c)
            i += 1
        out.append("".join(res))
    return "\n".join(out)


def filter_mcp(text: str) -> tuple[str, list[str]]:
    """按 HCA_MCP_DISABLE 去掉 server。没设开关就原样返回。"""
    names = [x.strip() for x in (os.environ.get("HCA_MCP_DISABLE") or "").split(",") if x.strip()]
    if not names:
        return text, []
    try:
        obj = json.loads(_strip_jsonc(text))
    except json.JSONDecodeError as e:
        print(f"[hca-init]   ⚠ .mcp.json 解析不了（{e}），HCA_MCP_DISABLE 本次不生效")
        return text, []
    servers = obj.get("mcpServers")
    if not isinstance(servers, dict):
        print("[hca-init]   ⚠ .mcp.json 里没有 mcpServers，HCA_MCP_DISABLE 本次不生效")
        return text, []
    removed = [n for n in names if n in servers]
    missing = [n for n in names if n not in servers]
    for n in removed:
        del servers[n]
    if missing:
        print(f"[hca-init]   ⚠ HCA_MCP_DISABLE 里有不存在的 server：{'、'.join(missing)}")
    obj.pop("_说明", None)
    obj["_hca_note"] = (f"由 HCA_MCP_DISABLE 去掉了：{'、'.join(removed)}"
                        if removed else "HCA_MCP_DISABLE 没有命中任何 server")
    return json.dumps(obj, ensure_ascii=False, indent=2), removed


# ── 人设里按 MCP 开关裁剪的块（I2）────────────────────────────────────────
#
#     <!-- hca:if-mcp hcapack -->  …只有挂了 hcapack 才该让模型看见的内容…  <!-- /hca:if-mcp -->
#
# 为什么需要：人设第五节有一张「优先用组合工具」的表，而 `HCA_MCP_DISABLE=hcapack`
# 时那几个工具根本不在模型的工具清单里 —— 留着就是**告诉模型去调一个它看不见的
# 工具**，白费一轮。裁掉之后人设与工具清单永远是一致的。
IF_MCP_RE = re.compile(
    r"[ \t]*<!--\s*hca:if-mcp\s+([A-Za-z0-9_,\s]+?)\s*-->\n(.*?)[ \t]*<!--\s*/hca:if-mcp\s*-->\n",
    re.S)


def filter_persona(text: str, disabled: set) -> tuple[str, list[str]]:
    """去掉那些「所需 MCP 已被关掉」的块。没有块或没关任何 server 时原样返回。"""
    dropped: list[str] = []

    def sub(m: "re.Match[str]") -> str:
        need = [x.strip() for x in re.split(r"[,\s]+", m.group(1)) if x.strip()]
        if any(n in disabled for n in need):
            dropped.extend(need)
            return ""
        return m.group(2)

    return IF_MCP_RE.sub(sub, text), dropped


def disabled_mcp() -> set:
    return {x.strip() for x in (os.environ.get("HCA_MCP_DISABLE") or "").split(",") if x.strip()}


def ensure_internal_key() -> str:
    """确保 HUNTER_INTERNAL_KEY 在 os.environ 中可用：优先环境变量，其次密钥文件或 secrets.env。"""
    cur = (os.environ.get("HUNTER_INTERNAL_KEY") or "").strip()
    if cur:
        return cur
    key_file = Path((os.environ.get("HUNTER_INTERNAL_KEY_FILE") or "/opt/hunter-secrets/internal_key").strip())
    try:
        if key_file.is_file():
            val = key_file.read_text(encoding="utf-8").strip()
            if val:
                os.environ["HUNTER_INTERNAL_KEY"] = val
                print(f"[hca-init] HUNTER_INTERNAL_KEY 从 {key_file} 读入")
                return val
    except OSError:
        pass
    sec_env = Path((os.environ.get("HUNTER_SECRETS_FILE") or "/opt/hunter-secrets/secrets.env").strip())
    try:
        if sec_env.is_file():
            for line in sec_env.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("HUNTER_INTERNAL_KEY="):
                    val = line.split("=", 1)[1].strip().strip("'\"")
                    if val:
                        os.environ["HUNTER_INTERNAL_KEY"] = val
                        print(f"[hca-init] HUNTER_INTERNAL_KEY 从 {sec_env} 读入")
                        return val
    except OSError:
        pass
    return ""


def fetch_wizard_llm_config() -> dict:
    """当环境变量未指定大模型 Key 时，向 api 的 `/api/internal/runtime/llm` 拉取向导保存的数据库配置。"""
    api_url = (os.environ.get("HERMES_API_URL") or "").strip().rstrip("/")
    internal_key = ensure_internal_key()
    if not api_url or not internal_key:
        return {}
    import urllib.request
    req = urllib.request.Request(
        f"{api_url}/api/internal/runtime/llm",
        headers={"X-Hunter-Internal-Key": internal_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, dict) and data.get("configured"):
                return data
    except Exception as e:  # noqa: BLE001
        print(f"[hca-init] ℹ 向导模型配置拉取跳过（{type(e).__name__}）")
    return {}


def seed_workspace() -> None:
    ensure_internal_key()
    refresh = env_bool("HCA_WORKSPACE_REFRESH", True)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    print(f"[hca-init] 工作区 {WORKSPACE}（模板 {TEMPLATE_DIR}，重铺托管文件={'是' if refresh else '否'}）")

    for rel in MANAGED:
        src = TEMPLATE_DIR / rel
        dst = WORKSPACE / rel
        if not src.exists():
            print(f"[hca-init]   ⚠ 模板里没有 {rel}，跳过")
            continue
        if dst.exists() and not refresh:
            print(f"[hca-init]   保留 {rel}（HCA_WORKSPACE_REFRESH=0）")
            continue
        if src.is_dir():
            n = copy_tree(src, dst)
            print(f"[hca-init]   铺开 {rel}/  {n} 个文件")
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            text = src.read_text(encoding="utf-8")
            if rel == ".atomcode.md":
                text, dropped = filter_persona(text, disabled_mcp())
                if dropped:
                    print(f"[hca-init]   .atomcode.md 去掉了依赖 {'、'.join(sorted(set(dropped)))} "
                          f"的段落（这些 MCP 已被 HCA_MCP_DISABLE 关掉）")
            if rel == ".mcp.json":
                text, removed = filter_mcp(text)
                if removed:
                    print(f"[hca-init]   .mcp.json 去掉 {len(removed)} 个 server："
                          f"{'、'.join(removed)}（HCA_MCP_DISABLE）")
            if rel == ".hooks.json":
                # 只渲染这一个：hook 的 command 不走 AtomCode 的环境变量展开。
                # .mcp.json 交给 AtomCode 自己展开，密钥不落盘。
                text, fell_back = render_vars(text)
                if fell_back:
                    print(f"[hca-init]   {rel} 用默认值的变量：{'、'.join(sorted(set(fell_back)))}")
                try:
                    json.loads(text)  # 严格 JSON —— 解析不过 AtomCode 会静默当没有 hook
                except json.JSONDecodeError as e:
                    print(f"[hca-init]   ✗ {rel} 不是合法 JSON（{e}）——"
                          f"AtomCode 会静默忽略整个文件，hook 一条都不会生效")
            dst.write_text(text, encoding="utf-8")
            print(f"[hca-init]   铺开 {rel}")

    # hook 脚本要可执行 —— copy2 保留权限，但从 git 检出/rsync 过的模板可能丢 x 位
    hooks_dir = WORKSPACE / ".hooks"
    if hooks_dir.is_dir():
        for f in hooks_dir.iterdir():
            if f.suffix in (".sh", ".py"):
                f.chmod(0o755)

    for rel in SEED_ONCE:
        src = TEMPLATE_DIR / rel
        dst = WORKSPACE / rel
        if dst.exists():
            print(f"[hca-init]   已存在 {rel}/，不动（用户资产）")
            continue
        if src.is_dir():
            n = copy_tree(src, dst)
            print(f"[hca-init]   初始化 {rel}/  {n} 个文件")
        else:
            dst.mkdir(parents=True, exist_ok=True)
            print(f"[hca-init]   初始化 {rel}/（空）")


def render_config() -> None:
    if not env_bool("HCA_CONFIG_REFRESH", True):
        print("[hca-init] HCA_CONFIG_REFRESH=0，不动 config.toml")
        return

    provider = os.environ.get("HCA_LLM_PROVIDER_NAME") or "hunter"
    base_url = (os.environ.get("HCA_LLM_BASE_URL") or os.environ.get("LLM_BASE_URL") or "").strip()
    model = (os.environ.get("HCA_LLM_MODEL") or os.environ.get("LLM_DEFAULT_MODEL") or "hunter-chat").strip()
    ctx = os.environ.get("HCA_LLM_CONTEXT_WINDOW") or "1000000"

    key = (os.environ.get("HCA_LLM_API_KEY") or os.environ.get("LLM_API_KEY") or "").strip()
    key_file = (os.environ.get("HCA_LLM_API_KEY_FILE") or os.environ.get("LLM_API_KEY_FILE") or "").strip()
    if not key and key_file:
        # 这里以前是 `p.is_file()` 裸调 —— 文件存在但**没有读权限**时
        # `os.stat` 直接抛 PermissionError，容器就死在启动脚本里（只留一段 traceback）。
        # 容器里跑的是 uid 10001(hca)，而宿主上的 key 文件默认是 0600 owner=运维自己，
        # 两边对不上就是这个下场（M4 从零安装实测撞到）。改成给一句能照着做的话。
        p = Path(key_file)
        try:
            key = p.read_text(encoding="utf-8").strip()
            print(f"[hca-init] key 从 {key_file} 读入")
        except PermissionError:
            import os as _os
            st = None
            try:
                st = _os.stat(_os.path.dirname(key_file))
            except OSError:
                pass
            print(f"[hca-init] ✗ {key_file} 存在但**读不到**（容器里是 uid "
                  f"{_os.getuid()}:{_os.getgid()}"
                  + (f"，宿主目录是 {oct(st.st_mode & 0o777)} owner={st.st_uid}:{st.st_gid}" if st else "")
                  + "）。", file=sys.stderr)
            print("[hca-init]   修法一：在宿主上跑 `bash deploy/up.sh`，它会把密钥目录"
                  "改成 group=10001 / 0750、key 文件 0640（只改组，不改属主）。", file=sys.stderr)
            print("[hca-init]   修法二：改用环境变量 HCA_LLM_API_KEY=…（不落盘，但 "
                  "docker inspect 看得到）。", file=sys.stderr)
        except FileNotFoundError:
            print(f"[hca-init] ⚠ HCA_LLM_API_KEY_FILE={key_file} 不存在")
        except OSError as e:
            print(f"[hca-init] ⚠ 读 {key_file} 失败：{type(e).__name__}: {e}")

    # 当环境变量与密钥文件均未提供 key 时，联动拉取向导在数据库中保存的模型配置
    if not key:
        wiz = fetch_wizard_llm_config()
        if wiz:
            key = str(wiz.get("api_key") or "").strip()
            w_model = str(wiz.get("model") or "").strip()
            w_base = str(wiz.get("base_url") or "").strip().rstrip("/")
            w_san = str(wiz.get("sanitize") or "auto").strip().lower()
            if w_model:
                model = w_model
            via_shim = w_san in ("1", "true", "yes", "on") or (w_san not in ("0", "false", "no", "off") and "gemini" in model.lower())
            shim_url = (os.environ.get("LLM_SHIM_URL") or "http://llm-shim:3999/v1").strip().rstrip("/")
            if via_shim and shim_url:
                base_url = shim_url
            elif w_base:
                base_url = w_base
            print(f"[hca-init] 已从向导数据库配置拉取模型：model={model} via_shim={via_shim}")

    # 若走内置额度网关且仍未显式设置 LLM key，自动回退使用 HUNTER_API_KEY
    if not key and base_url.rstrip("/").endswith("/api/saas/llm/v1"):
        platform_key = (os.environ.get("HUNTER_API_KEY") or "").strip()
        if platform_key.startswith("hunt_tools_"):
            key = platform_key
            print("[hca-init] 内置额度网关自动复用 HUNTER_API_KEY")

    # 人设替换（**只有 fork 二进制认这一项**，见 docs/fork-patches.md）。
    # 官方 5.1.0 会把 `system_prompt_file` 当成未知键读进来又原样忽略 ——
    # 不报错，但也不生效。所以这里只在显式给了环境变量时才写这一行，
    # 免得官方二进制的 config.toml 里躺着一行看起来生效、实际没生效的配置。
    persona_file = (os.environ.get("HCA_LLM_SYSTEM_PROMPT_FILE") or "").strip()
    if persona_file and not Path(persona_file).is_file():
        print(f"[hca-init] ⚠ HCA_LLM_SYSTEM_PROMPT_FILE={persona_file} 不存在，不写这一行")
        persona_file = ""
    if persona_file:
        # 人设也要过一遍 `<!-- hca:if-mcp … -->` 裁剪，理由与 `.atomcode.md` 那份
        # 一模一样：关掉 hcapack 时人设里那段「优先用组合工具」留着就是叫模型去调
        # 一个它看不见的工具。原件多半是只读挂载，所以裁完另写一份到 ATOMCODE_HOME，
        # config.toml 指向裁过的那份。
        raw = Path(persona_file).read_text(encoding="utf-8")
        body, dropped = filter_persona(raw, disabled_mcp())
        ATOMCODE_HOME.mkdir(parents=True, exist_ok=True)
        rendered = ATOMCODE_HOME / "persona.md"
        rendered.write_text(body, encoding="utf-8")
        rendered.chmod(0o600)
        if dropped:
            print(f"[hca-init] 人设按关掉的 MCP 裁掉了 {sorted(set(dropped))} 相关的块")
        print(f"[hca-init] 人设 {persona_file} → {rendered}"
              f"（{len(raw)} → {len(body)} 字符）")
        persona_file = str(rendered)

    # 工具挂载过滤（同样**只有 fork 二进制认**，见 docs/fork-patches.md §2）。
    # 逗号分隔；`[tools] allow` 是白名单、`deny` 在它之后判（deny 赢）。
    # 写法：精确名 `read_file`、前缀 `mcp__screener__*`、族 `group:codeintel`。
    # 与 llm-shim 的 LLM_TOOL_DENY 的分工：shim 那层是在**请求出容器之后**把
    # schema 摘掉，daemon 侧仍然把工具挂着（模型看不见，但 daemon 还认得它）；
    # 这一层是**根本不挂**，所以连 daemon 内部的工具目录里都没有。两层都留着：
    # 官方二进制只有 shim 那层可用。
    def _tool_list(name: str) -> list[str]:
        raw = (os.environ.get(name) or "").strip()
        return [x.strip() for x in raw.split(",") if x.strip()]

    tools_allow = _tool_list("HCA_TOOLS_ALLOW")
    tools_deny = _tool_list("HCA_TOOLS_DENY")

    if not base_url:
        print("[hca-init] ⚠ 没有 HCA_LLM_BASE_URL，daemon 起得来但没有可用模型")
    # 只说长度和前缀，绝不打印 key 本身
    shown = f"{key[:11]}****（{len(key)} 字符）" if key else "（空）"
    print(f"[hca-init] provider={provider} model={model} base_url={base_url or '（空）'} key={shown}")
    print(f"[hca-init] system_prompt_file={persona_file or '（未设，用内置人设）'}")
    print(f"[hca-init] tools.allow={tools_allow or '（未设，全挂）'} tools.deny={tools_deny or '（未设）'}")

    ATOMCODE_HOME.mkdir(parents=True, exist_ok=True)
    cfg = ATOMCODE_HOME / "config.toml"
    # TOML 基本字符串的转义规则与 JSON 一致，用 json.dumps 生成，避免手搓引号出错
    q = json.dumps
    body = "\n".join([
        "# 由 deploy/daemon/hca-init.py 在容器启动时生成，改这里没用 —— 改环境变量。",
        "# 想手工维护就设 HCA_CONFIG_REFRESH=0。",
        f"default_provider = {q(provider)}",
        "auto_update = false",
        "auto_commit = false",
        'offline_mode = "off"',
        "",
        f"[providers.{provider}]",
        'type = "openai"',
        f"api_key = {q(key)}",
        f"model = {q(model)}",
        f"base_url = {q(base_url)}",
        f"context_window = {int(ctx)}",
    ] + ([f"system_prompt_file = {q(persona_file)}"] if persona_file else []) + (
        ["", "[tools]"]
        + ([f"allow = [{', '.join(q(x) for x in tools_allow)}]"] if tools_allow else [])
        + ([f"deny = [{', '.join(q(x) for x in tools_deny)}]"] if tools_deny else [])
        if (tools_allow or tools_deny) else []
    ) + [
        "",
    ])
    cfg.write_text(body, encoding="utf-8")
    cfg.chmod(0o600)
    print(f"[hca-init] 已写 {cfg}（0600）")


def main() -> int:
    if not TEMPLATE_DIR.is_dir():
        print(f"[hca-init] ✗ 模板目录不存在：{TEMPLATE_DIR}", file=sys.stderr)
        return 2
    seed_workspace()
    render_config()
    print("[hca-init] 完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
