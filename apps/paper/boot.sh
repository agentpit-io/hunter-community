#!/bin/sh
# Paper Service 入口 · 两件事：① 启动自检 ② 起 uvicorn
#
# 自检不过**直接退出**（`app/selfcheck.py`）：模式不是 PAPER、口令缺失、
# 账本库连不上、必需表缺失、或连的角色权限不对（比如被指到了管理员连接），
# 都在这里挡下来。宁可起不来，也不要一个「看着健康、一点就 500」的实例。
set -e

echo "[paper] PAPER_MODE=${PAPER_MODE:-<未设置>}"
python -m app.selfcheck

# 监听地址：与 apps/api 一致，交给 bind 处理 IPv4/IPv6。这里保持简单：
# 容器内 0.0.0.0:8000，宿主端口只绑 127.0.0.1（见 docker-compose.yml）。
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
