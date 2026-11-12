#!/bin/sh
# fin-worker 入口 · 自检（警告级）→ 起进程（内部 HTTP + Temporal Worker）
#
# ⚠️ 与 paper 不同：**外部依赖不可用不致命**。Temporal / paper 一时连不上时，
#    Worker 内部会退避重试，进程应留在原地等 —— 让它崩掉只会得到
#    「Restarting」循环，比「活着但还没连上」更难排查。致命项只有缺
#    HUNTER_INTERNAL_KEY（见 app/selfcheck.py）。
set -e

echo "[fin-worker] PAPER_BASE_URL=${PAPER_BASE_URL:-<默认>} TEMPORAL_ADDRESS=${TEMPORAL_ADDRESS:-<默认>}"
python -m app.selfcheck || echo "[fin-worker] 自检未通过（非致命项），继续启动"

exec python -m app.main
