#!/bin/bash
# 安装初始化 · 1Panel 在容器启动**之前**以「安装目录」为工作目录执行本脚本。
#
# Generated from official installation evidence:
#   https://github.com/agentpit-io/hunter-community/blob/main/deploy/Dockerfile.daemon
#     —— daemon 镜像以 USER=hca(uid 10001:10001) 运行
#   https://github.com/agentpit-io/hunter-community/blob/main/docker-compose.yml
#     —— daemon 服务挂载 /workspace、/data 与 /run/hca，属主需为 10001:10001
set -euo pipefail

mkdir -p data/postgres data/redis data/workspace data/atomcode data/token data/secrets data/user-skills data/packages data/experiments

chown -R 10001:10001 data/workspace data/atomcode data/token

echo "[hunter-community/init] data/{workspace,atomcode,token} 属主已设为 10001:10001 (daemon 容器以 uid 10001 运行)"
