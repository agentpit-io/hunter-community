#!/bin/bash
# 安装初始化 · 1Panel 在容器启动**之前**以「安装目录」为工作目录执行本脚本。
#
# Generated from official installation evidence:
#   https://github.com/agentpit-io/hunter-community/blob/main/deploy/opencode.Dockerfile
#     —— 基础镜像 USER=hunter(uid 1001);所有 COPY 都带 --chown=1001:1001
#   https://github.com/agentpit-io/hunter-community/blob/main/docker-compose.yml
#     —— opencode 服务挂 /home/hunter/.local(会话正文),注释里写明「卷根目录
#        属主必须是 1001,否则入口脚本会因为不可写而报错退出」
#
# 为什么必须有这一步:具名卷首次挂载会把镜像里 /home/hunter/.local 的内容与属主
# 一起拷进卷,而**宿主目录 bind mount 不会** —— 目录由 docker 以 root 建出来,
# 容器里的 uid 1001 写不进去,表现是 opencode 容器反复重启。
# 实测(2026-09-18,镜像 ghcr.io/agentpit-io/hunter-community-opencode:1.1.0-rc1):
#   docker run --rm --entrypoint sh <镜像> -c 'ls -la /home/hunter/.local; id'
#   → .local 下只有空的 share/ 与 state/ 两个目录,属主 hunter(1001);id 为 uid=1001
#   所以 bind mount 不会遮盖任何有用内容,只需要把属主改对。
set -euo pipefail

# 六个持久化目录随包分发(各带 .gitkeep),这里只兜底,不作为创建的主路径。
mkdir -p data/postgres data/redis data/opencode data/secrets data/user-skills data/packages

# 只改 opencode 的会话目录 —— 另外五个目录的写方(postgres / redis 官方镜像的
# entrypoint、以 root 运行的 api)自己会处理属主,不要多动。
chown -R 1001:1001 data/opencode

echo "[hunter-community/init] data/opencode 属主已设为 1001:1001(opencode 容器以 uid 1001 运行)"
