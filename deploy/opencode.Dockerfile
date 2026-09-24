# hunter-community · opencode 包装镜像
#
# 基础镜像由私仓 huntercode 构建(单文件二进制 · 618 MB),这一层只做一件事:
# 把原来靠 bind mount 送进容器的 10 处仓库文件 COPY 进来,让镜像自包含。
#
# 为什么不发新的基础镜像标签:R0 预研实测(docs/setup-wizard/R0-预研结论.md 第七节)
# 结论是 SKILL 同步用 opencode 原生 `skills.urls` 即可、huntercode 无需改动,
# 所以继续钉 1.18.12-slim.1。**别改成 latest / dev** —— 浮动标签意味着某次
# docker pull 会无声换掉运行方式,出了问题连「什么时候变的」都查不出来。
#
# ⚠️ 基础镜像 USER=hunter(uid 1001)、WORKDIR=/opt/opencode-workspace。
# 所有 COPY 都必须带 --chown=1001:1001:COPY 默认把文件给 root,
# 容器以 1001 跑,gen-config.py 读 /opt/hunter-mcp 会 Permission denied,
# 而那个失败发生在启动阶段,表现是容器反复 Restarting。
#
# 构建上下文是**仓库根**(要 COPY skills/ 和 scripts/,它们不在同一子目录下):
#   docker build -f deploy/opencode.Dockerfile -t hunter-community-opencode .

# ── 插件依赖 · 构建时装好,启动时不再联网 ─────────────────────────
#
# opencode 启动时会给三个配置目录各装一次 `@opencode-ai/plugin`
# (~/.config/opencode、/opt/opencode-workspace、/opt/opencode-workspace/.opencode),
# **只要目录里没有 node_modules 就现场 npm install**;而我们的两个插件要等这一步装完
# 才初始化(opencode 源码 plugin/index.ts 里的 waitForDependencies)。
#
# 这三个目录都不在数据卷里,所以每次新装 / 升级重建容器都要重装一遍。npm 下载没有
# 总超时,国内直连 registry.npmjs.org 一旦碰上卡死的连接,opencode 所有接口(连 /path
# 都算)就一直挂着 —— 2026-09-25 本机重装实测:装完 10 分钟模型选择器还是空的,
# 浏览器里看不到任何报错,重启容器(依赖已在)8 秒就好。
#
# 在这里预先装好,并带上 package.json 与 package-lock.json:opencode 看到 node_modules
# 存在、锁文件里也有 @opencode-ai/plugin,就整个跳过安装(core/src/npm.ts 的 install)。
# ⚠️ 版本必须与基础镜像的 opencode 版本一致(它装的是 @opencode-ai/plugin@<自身版本>),
#    升级基础镜像时这里一起改;对不上的话锁文件检查照样通过,但插件接口可能不匹配。
FROM node:22-slim AS plugin-deps
ARG OPENCODE_PLUGIN_VERSION=1.18.12
WORKDIR /deps
RUN printf '{\n  "dependencies": {\n    "@opencode-ai/plugin": "%s"\n  }\n}\n' "$OPENCODE_PLUGIN_VERSION" > package.json \
 && npm install --ignore-scripts --no-audit --no-fund --omit=dev \
 && test -d node_modules/@opencode-ai/plugin

FROM ghcr.io/agentpit-io/hunter-opencode:1.18.12-slim.1

COPY --from=plugin-deps --chown=1001:1001 /deps/ /home/hunter/.config/opencode/
COPY --from=plugin-deps --chown=1001:1001 /deps/ /opt/opencode-workspace/
COPY --from=plugin-deps --chown=1001:1001 /deps/ /opt/opencode-workspace/.opencode/

# ── 启动脚本 · gen-config.py · HUNTER-AGENT.md ────────────────
# 原挂载:./scripts/opencode:/opt/hunter-boot:ro
COPY --chown=1001:1001 scripts/opencode/ /opt/hunter-boot/

# ── 平台自有能力的 MCP(整目录)──────────────────────────────
# 原挂载:./scripts/opencode-mcp:/opt/hunter-mcp:ro
# 放 /opt/hunter-mcp 而不是覆盖 mcp/ 目录 —— 覆盖会把镜像自带的脚本一起遮掉。
# gen-config.py 从这里注册 hunter_cap / screener。
COPY --chown=1001:1001 scripts/opencode-mcp/ /opt/hunter-mcp/

# ── 覆盖镜像自带的三个 MCP ────────────────────────────────────
# watchlist_mcp.py  加了 watchlist_add tool,让「加XX到自选」能直接落库,
#                   而不是回落到"点击左侧菜单"的文本兜底。
# uzi_mcp.py        httpx 120s→170s 对齐后端三段预算,失败对象带 type/code/instruction
#                   (2026-09-07 茅台事故)。
# hunter_user_mcp.py 镜像那份描述让模型「预测走势调 hunter_cap_kpred」,而 kpred 已删,
#                   模型会去找一个不存在的工具。
# 三份都是 huntercode 的部署副本(见 CLAUDE.md「部署备注」),改动先改 huntercode。
COPY --chown=1001:1001 scripts/opencode-mcp/watchlist_mcp.py \
                       scripts/opencode-mcp/uzi_mcp.py \
                       scripts/opencode-mcp/hunter_user_mcp.py \
                       /opt/opencode-workspace/mcp/

# ── 覆盖 / 新增两个插件 ───────────────────────────────────────
# hunter-mcp-context.ts  HUNTER_TOOLS 里加了 watchlist_add,否则 tool.execute.before
#                        不注入 _hermes_user_id,api 收到的请求没有 X-Hunter-User-Id → 401,
#                        LLM 会告诉用户"请登录",很误导。
# hunter-lang.ts         语言守卫 · 把整段回复发给 api 的 /api/internal/lang/guard 判断
#                        有没有英文散文(铁律 A10:prompt 约束单独用无效,要出口强校验)。
COPY --chown=1001:1001 scripts/opencode-mcp/plugins/hunter-mcp-context.ts \
                       scripts/opencode-mcp/plugins/hunter-lang.ts \
                       /opt/opencode-workspace/plugins/

# ── 内置 SKILL ────────────────────────────────────────────────
# 原挂载:./skills:/opt/opencode-workspace/.opencode/skills:ro
# opencode 自己会扫这个目录(实测见 _14 §2);它会遮掉镜像自带的 effect skill
# (那是讲 Effect TS 库的,金融场景用不上)。
#
# 用户自己加的 SKILL(原来挂 ./user-skills 到 ~/.config/opencode/skills)**不再挂载** ——
# 改由 api 的导出接口 + opencode 原生 skills.urls 同步(R0 第二节,M1 子任务 D)。
COPY --chown=1001:1001 skills/ /opt/opencode-workspace/.opencode/skills/

ENTRYPOINT ["/bin/sh", "/opt/hunter-boot/entrypoint.sh"]
