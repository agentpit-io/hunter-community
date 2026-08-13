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

# ③ MCP 超时兜底(两层)
#
#    层 A · uzi_mcp.py 里 httpx 客户端写死 timeout=25.0 →
#            后端 finance-data 7 路抓 + LLM 合成 22-30s 起步,25s 极易踩线,
#            超时后 tool 返 ReadTimeout,LLM 就说"深度分析服务不可用"。
#            in-place 拉到 120s。
#
#    层 B · opencode 自己 .opencode/opencode.jsonc 里所有 MCP 都 "timeout": 30000 ms →
#            即使层 A 抬到 120s,opencode 也会在 30s 时把 tool 掐掉、直接
#            "(pending / no output)" 回给 LLM(见 packages/opencode/src/mcp/index.ts
#            DEFAULT_TIMEOUT = 30_000)。开源版深度分析 62s、组合建议 45s+ 都要它。
#            统一抬到 180000(3 分钟),给一切工具留缓冲。
#
#    镜像修好后可删。
UZI_MCP=/opt/opencode-workspace/mcp/uzi_mcp.py
if [ -w "$UZI_MCP" ] && grep -q "timeout=25.0" "$UZI_MCP" 2>/dev/null; then
    sed -i 's/timeout=25\.0/timeout=120.0/g' "$UZI_MCP" \
        && echo "[boot] uzi_mcp httpx timeout 25s → 120s"
fi

MCP_CFG=/opt/opencode-workspace/.opencode/opencode.jsonc
if [ -w "$MCP_CFG" ] && grep -q '"timeout": 30000' "$MCP_CFG" 2>/dev/null; then
    sed -i 's/"timeout": 30000/"timeout": 180000/g' "$MCP_CFG" \
        && echo "[boot] opencode MCP timeout 30s → 180s(否则 opencode 会在 30s 掐 tool → 前端显示'no output')"
fi

exec bun run packages/opencode/src/index.ts serve --hostname 0.0.0.0 --port 3901
