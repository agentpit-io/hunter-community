#!/usr/bin/env python3
"""Generate opencode.json from environment · runs inside the opencode container.

Why this exists: the hunter-opencode image ships everything it needs —
5 plugins in /opt/opencode-workspace/plugins, 4 MCP servers in
/opt/opencode-workspace/mcp — but **no opencode.json to wire them together**.
opencode reads its provider config from a file, not from LLM_* env vars, so a
container started with only env set falls back to the built-in "OpenCode Zen"
provider and never talks to your gateway. This script closes that gap.

Reference: the production instance's config (hunter.agentpit.io) has the same
shape — provider + mcp + plugin. The differences here are paths (community
image keeps everything under /opt/opencode-workspace instead of /mcp and
/root/.config/opencode) and the api hostname (docker-compose service name).

Reads:
  LLM_BASE_URL · LLM_API_KEY · LLM_DEFAULT_MODEL   provider block
  HERMES_API_URL · HUNTER_INTERNAL_KEY              MCP → api callbacks
  HUNTER_BUDGET_ENABLED                             include budget plugin
Writes: $OPENCODE_CONFIG_DIR/opencode.json (default /opt/opencode-workspace)
"""
import json
import os
import sys

WORKSPACE = os.environ.get("OPENCODE_CONFIG_DIR", "/opt/opencode-workspace")
MCP_DIR = os.path.join(WORKSPACE, "mcp")
PLUGIN_DIR = os.path.join(WORKSPACE, "plugins")

BASE_URL = (os.environ.get("LLM_BASE_URL") or "").strip().rstrip("/")
API_KEY = (os.environ.get("LLM_API_KEY") or "").strip()
MODEL = (os.environ.get("LLM_DEFAULT_MODEL") or "").strip()

HERMES_API = os.environ.get("HERMES_API_URL", "http://api:8000")
INTERNAL_KEY = os.environ.get("HUNTER_INTERNAL_KEY", "")
BUDGET_ON = (os.environ.get("HUNTER_BUDGET_ENABLED", "false").lower()
             in ("1", "true", "yes", "on"))

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

# MCP server name → script file. Only those that exist in the image are
# registered, so a slimmer image doesn't produce a config full of dead entries.
MCP_SERVERS = {
    "watchlist": "watchlist_mcp.py",   # 自选股速查 / 新闻 / 日报
    "portfolio": "portfolio_mcp.py",   # 组合调仓 / 情景模拟 / 风险画像
    "uzi": "uzi_mcp.py",               # 深度分析
    "usermcp": "hunter_user_mcp.py",   # 用户在 /mcp-config 里接的第三方数据源
}

# Loaded in this order. budget is opt-in — it caps per-request LLM spend, which
# a self-hosted user paying their own LLM bill usually doesn't want.
PLUGINS = ["hunter-auth.ts", "hunter-audit.ts", "hunter-guard.ts",
           "hunter-mcp-context.ts"]
if BUDGET_ON:
    PLUGINS.append("hunter-budget.ts")


def main() -> int:
    if not BASE_URL or not MODEL:
        # Refuse to write a half-configured file: opencode would start, accept
        # messages, and fail on the first one. Failing here puts the reason in
        # `docker compose logs opencode` where someone will actually read it.
        print("[gen-config] LLM_BASE_URL / LLM_DEFAULT_MODEL 未设置 —— "
              "请在 .env 里填好再 `docker compose up -d`", file=sys.stderr)
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

    mcp = {}
    for name, script in MCP_SERVERS.items():
        path = os.path.join(MCP_DIR, script)
        if not os.path.exists(path):
            print(f"[gen-config] 跳过 {name}:镜像里没有 {script}", file=sys.stderr)
            continue
        mcp[name] = {
            "type": "local",
            "command": ["python3", path],
            "enabled": True,
            # These MCP servers call back into the api container; without the
            # env they'd default to localhost and hit themselves.
            "environment": {
                "HERMES_API_URL": HERMES_API,
                "HUNTER_INTERNAL_KEY": INTERNAL_KEY,
            },
        }
    if mcp:
        cfg["mcp"] = mcp

    plugins = [f"file://{os.path.join(PLUGIN_DIR, p)}"
               for p in PLUGINS if os.path.exists(os.path.join(PLUGIN_DIR, p))]
    if plugins:
        cfg["plugin"] = plugins

    out = os.path.join(WORKSPACE, "opencode.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    print(f"[gen-config] 已写 {out}", file=sys.stderr)
    print(f"[gen-config]   provider {PROVIDER_ID} → {upstream}"
          f"{' (经 schema shim → ' + BASE_URL + ')' if via_shim else ''}"
          f" · model {MODEL} · apiKey {'有' if API_KEY else '无'}", file=sys.stderr)
    print(f"[gen-config]   mcp {sorted(mcp)}", file=sys.stderr)
    print(f"[gen-config]   plugin {[os.path.basename(p) for p in plugins]}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
