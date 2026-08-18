#!/usr/bin/env python3
"""写 opencode 的 provider 配置 · 在 opencode 容器里启动前跑。

**只写 provider,别的一概不碰。** 镜像已经用 .opencode/opencode.jsonc 把 4 个 MCP
注册好了,plugins/*.ts 也由 opencode 的插件扫描器自动发现 —— 唯独 provider 是
`{}`,因为它要 API key,只能运行时给。opencode 会把两份配置合并,所以这里补齐
provider 就够了。

(踩过的坑:最初这里连 mcp 也写了一遍,结果 hunter_user_mcp.py 被起了两次 ——
镜像叫它 hunter_user、我叫它 usermcp,名字不同就没去重。)

为什么非要生成文件:opencode 只从配置文件读 provider,不认 LLM_* 环境变量。
不写的话它会回落到内置的 OpenCode Zen,你在 .env 里填的网关根本不会被调用,
现象是"能聊天但答得驴唇不对马嘴"。

Reads:  LLM_BASE_URL · LLM_API_KEY · LLM_DEFAULT_MODEL · LLM_SCHEMA_SANITIZE
Writes: $OPENCODE_CONFIG_DIR/opencode.json (默认 /opt/opencode-workspace)
"""
import json
import os
import sys

WORKSPACE = os.environ.get("OPENCODE_CONFIG_DIR", "/opt/opencode-workspace")

BASE_URL = (os.environ.get("LLM_BASE_URL") or "").strip().rstrip("/")
API_KEY = (os.environ.get("LLM_API_KEY") or "").strip()
MODEL = (os.environ.get("LLM_DEFAULT_MODEL") or "").strip()

PROVIDER_ID = "hunter-llm"

# Gemini 只认 OpenAPI 子集的 tool schema;opencode 送的是完整 JSON Schema。
# 不洗一遍的话每条消息都会被上游拒:
#   Invalid JSON payload received. Unknown name "$schema" at ... parameters
# 而 OpenAI 的 strict function calling 反而**需要** additionalProperties,
# 洗掉会削弱它。所以默认 auto:只有模型名含 gemini 才绕这一层。
SANITIZE = (os.environ.get("LLM_SCHEMA_SANITIZE", "auto") or "auto").strip().lower()
SHIM_URL = os.environ.get("LLM_SHIM_URL", "http://llm-shim:3999/v1").rstrip("/")


def _use_shim(model: str) -> bool:
    if SANITIZE in ("1", "true", "yes", "on"):
        return True
    if SANITIZE in ("0", "false", "no", "off"):
        return False
    return "gemini" in model.lower()


def main() -> int:
    missing = [
        name for name, val in (
            ("LLM_BASE_URL", BASE_URL),
            ("LLM_API_KEY", API_KEY),
            ("LLM_DEFAULT_MODEL", MODEL),
        ) if not val
    ]
    if missing:
        # Refuse to write a half-configured file: opencode would start, accept
        # messages, and either 401 on the first LLM call or silently return an
        # empty assistant bubble (upstream key missing → opencode swallows the
        # error → 前端 MessageList 只按时间戳打出「深度思考完成」一行,一片空白,
        # 完全看不出是 key 没填). Failing here puts the reason in
        # `docker compose logs opencode` where someone will actually read it.
        print(
            f"[gen-config] {' / '.join(missing)} 未设置 —— "
            "请在 .env 里填好再 `docker compose up -d`。"
            "留空会导致 opencode 收到 401 后返回空消息、前端看不到任何报错。",
            file=sys.stderr,
        )
        return 1

    via_shim = _use_shim(MODEL)
    upstream = SHIM_URL if via_shim else BASE_URL

    cfg = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            PROVIDER_ID: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Hunter LLM",
                "options": {"baseURL": upstream, "apiKey": API_KEY},
                # Only the configured model. opencode needs each model declared
                # explicitly; listing others the gateway may not serve would put
                # dead entries in the model picker.
                "models": {MODEL: {"name": MODEL}},
            }
        },
        "model": f"{PROVIDER_ID}/{MODEL}",
    }

    # ── 项目指令(2026-08-18)──────────────────────────────────
    #
    # **必须挂,否则模型读的是 opencode 自己的 AGENTS.md。**
    #
    # opencode 默认把工作区根的 `AGENTS.md` 当项目指令,而我们的工作区
    # `/opt/opencode-workspace` 就是 opencode 的源码树 —— 那份 AGENTS.md
    # 讲的是「怎么给 opencode 提 PR、分支怎么命名、SDK 怎么重新生成」。
    #
    # 于是模型的项目上下文一直是"你在给 opencode 做贡献"。表现是它用英文、
    # 用开发者口吻回答金融问题,而且**会宣称自己做了没做的事**
    # (实测:说"已暂存 12 个 SKILL",而 skill_stage 一次都没调)。
    # 用户问「为什么不是中文回答我」才暴露出来。
    #
    # instructions 是**追加**不是替换 —— opencode 仍会读 AGENTS.md,
    # 但我们这份排在后面,冲突时更靠后的更有分量。
    agent_md = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "HUNTER-AGENT.md")
    if os.path.exists(agent_md):
        cfg["instructions"] = [agent_md]
    else:
        # 不静默跳过 —— 缺了这份文件,模型会退回"给 opencode 做贡献"那套上下文,
        # 而那个症状(英文回答、假报工具调用)完全指不到这里
        print(f"[gen-config] ⚠ 缺 {agent_md} —— "
              f"模型将退回读 opencode 自身的 AGENTS.md,回答会跑偏", flush=True)

    # 只注册**镜像里没有**的那个 MCP。镜像自带的 .opencode/opencode.jsonc 已经
    # 注册了 watchlist/portfolio/uzi/hunter_user 四个,两份配置会合并 ——
    # 在这里重复写会让同一个脚本被起两次(2026-08-14 踩过,见 cd427bf)。
    extra = os.environ.get("HUNTER_EXTRA_MCP_DIR", "/opt/hunter-mcp")
    cap = os.path.join(extra, "hunter_capability_mcp.py")
    if os.path.exists(cap):
        cfg["mcp"] = {
            "hunter_cap": {
                "type": "local",
                "command": ["python3", cap],
                "enabled": True,
                # kpred 是 GPU 推理、scout 是 30-60s 主动采集,30s 默认会被掐断
                "timeout": 180000,
            }
        }

    out = os.path.join(WORKSPACE, "opencode.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    print(f"[gen-config] 已写 {out}", file=sys.stderr)
    print(f"[gen-config]   provider {PROVIDER_ID} → {upstream}"
          f"{' (经 schema shim → ' + BASE_URL + ')' if via_shim else ''}"
          f" · model {MODEL} · apiKey {'有' if API_KEY else '无'}", file=sys.stderr)
    if "mcp" in cfg:
        print("[gen-config]   额外注册 mcp: hunter_cap(镜像自带的 4 个不重复注册)",
              file=sys.stderr)
    else:
        print("[gen-config]   mcp / plugin 全部由镜像自带配置负责", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
