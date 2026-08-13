#!/bin/sh
# opencode 容器入口 · 补两件镜像没做的事,再交给 opencode 本体。
#
# ① MCP 依赖版本
#    镜像里 4 个 MCP 脚本用的是 mcp<2 的 API(@server.list_tools() 装饰器),
#    但镜像构建时没锁版本,装进去的是 mcp 2.0.0 —— 脚本一启动就
#    AttributeError: 'Server' object has no attribute 'list_tools',
#    于是所有工具都注册不上,左侧 SKILL 点了没反应。
#    这里兜一下。**真正该修的地方是 huntercode 的 Dockerfile 锁 "mcp<2"**,
#    镜像修好后下面这段会自动变成空转(检测到能用就不装)。
#
# ② opencode.json
#    镜像自带 plugins/ 和 mcp/ 却没有把它们串起来的配置文件,而 opencode 只认
#    配置文件里的 provider、不认 LLM_* 环境变量 —— 不生成的话它会回落到内置的
#    OpenCode Zen,根本不去调你配的网关。
#
# ⚠️ 末尾 exec 与镜像 ENTRYPOINT 保持一致,镜像若改了启动参数这里要跟着改。
#    查当前值:docker inspect ghcr.io/agentpit-io/hunter-opencode:latest \
#              --format '{{json .Config.Entrypoint}}'
set -e

if python3 -c 'import mcp.server,sys; sys.exit(0 if hasattr(mcp.server.Server("probe"),"list_tools") else 1)' 2>/dev/null; then
    echo "[boot] mcp SDK 版本可用,跳过安装"
else
    echo "[boot] mcp SDK 不兼容(镜像装的是 2.x,脚本要 1.x),补装中…"
    # --user 装到 /home/hunter/.local · --break-system-packages 是 PEP 668 的要求,
    # 这是容器不是系统 Python,没有"弄坏发行版"的风险
    pip install --user --break-system-packages --quiet --no-warn-script-location "mcp<2" \
        || echo "[boot] ⚠️ 补装失败 —— 工具类 SKILL 会不可用,聊天不受影响"
fi

python3 /opt/hunter-boot/gen-config.py

exec bun run packages/opencode/src/index.ts serve --hostname 0.0.0.0 --port 3901
