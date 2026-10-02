#!/bin/bash
# 智能炒股 · 对账告警投递（宿主脚本）
#
# 为什么在宿主跑：告警通道是 `notify-qq`，而它是**宿主上的脚本**（走
# /home/support/.mixplode-qq/env 的 SMTP），`paper` / `fin-worker` 容器里没有它，
# 也没有挂进来。容器负责把「账本不平」写成一条**可查询、可投递**的记录
# （`fin_alert_log.sent_at IS NULL`），本脚本负责把它发出去并回执。
#
# **没有第二套告警系统**（08 §七「不要另起一个」）：通道就是 notify-qq。
#
# 用法：
#   scripts/fin_recon_alert.sh              # 发未投递的告警
#   scripts/fin_recon_alert.sh --dry-run    # 只打印，不发、不回执
#
# 环境（默认值对齐本机 dev compose，可用 env 覆盖）：
#   FIN_PG_HOST=127.0.0.1 FIN_PG_PORT=5598 FIN_PG_DB=hunter
#   FIN_PG_USER=fin_paper_rw FIN_PG_PASSWORD=<.env 里的 FIN_PAPER_PASSWORD>
#   NOTIFY_QQ=/home/support/bin/notify-qq
set -euo pipefail

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

NOTIFY_QQ="${NOTIFY_QQ:-/home/support/bin/notify-qq}"
FIN_PG_HOST="${FIN_PG_HOST:-127.0.0.1}"
FIN_PG_PORT="${FIN_PG_PORT:-5598}"
FIN_PG_DB="${FIN_PG_DB:-hunter}"
FIN_PG_USER="${FIN_PG_USER:-fin_paper_rw}"
# 从仓库根 .env 里取账本口令（不与 api 的 postgres 超级用户混用）
if [ -z "${FIN_PG_PASSWORD:-}" ] && [ -f "$(dirname "$0")/../.env" ]; then
  FIN_PG_PASSWORD=$(grep -E '^FIN_PAPER_PASSWORD=' "$(dirname "$0")/../.env" | cut -d= -f2-)
fi
export PGPASSWORD="${FIN_PG_PASSWORD:?FIN_PAPER_PASSWORD 未设置}"

# psql 有两种拿到的方式：宿主机上有就用本机的；没有（这台开发机就是）就经
# docker exec 进 postgres 容器用**管理员连接**（`fin_alert_log` 只有这一处要写状态，
# 而回执那一步是运维动作，不属于账本写入）。
if command -v psql >/dev/null 2>&1; then
  psql_q() { psql -h "$FIN_PG_HOST" -p "$FIN_PG_PORT" -U "$FIN_PG_USER" -d "$FIN_PG_DB" -tA -F $'\t' -c "$1"; }
else
  FIN_PG_CONTAINER="${FIN_PG_CONTAINER:-hunter-community-postgres-1}"
  psql_q() { docker exec -i "$FIN_PG_CONTAINER" psql -U "${POSTGRES_USER:-hunter}" -d "$FIN_PG_DB" -tA -F $'\t' -c "$1"; }
fi

# 未投递的告警：id / subject / body（body 里的制表符/换行已由 paper 侧压平）
ROWS=$(psql_q "SELECT id, COALESCE(subject,''), COALESCE(body,'') FROM fin_alert_log WHERE sent_at IS NULL ORDER BY id LIMIT 20;")

if [ -z "$ROWS" ]; then
  echo "[fin_recon_alert] 没有未投递的告警"
  exit 0
fi

SENT_IDS=()
while IFS=$'\t' read -r id subject body; do
  [ -z "$id" ] && continue
  if [ "$DRY_RUN" = "1" ]; then
    echo "[dry-run] 会发送 id=$id · $subject"
    continue
  fi
  "$NOTIFY_QQ" "$subject" "$body"
  SENT_IDS+=("$id")
done <<< "$ROWS"

if [ "$DRY_RUN" = "1" ] || [ "${#SENT_IDS[@]}" -eq 0 ]; then
  exit 0
fi

# 回执：把发出去的标成已投递（只有这一步改 fin_alert_log，账本表一行不动）
IDS=$(IFS=,; echo "${SENT_IDS[*]}")
psql_q "UPDATE fin_alert_log SET sent_at = now() WHERE id IN ($IDS);" >/dev/null
echo "[fin_recon_alert] 已投递 ${#SENT_IDS[@]} 条（ids: $IDS）"
