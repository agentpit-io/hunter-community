#!/usr/bin/env bash
# 宿主机端口占用检测（macOS / Linux）。
#
# 读取仓库根 .env 里的 5 个 *_HOST_PORT（没有 .env 或没设置就用默认值），
# 逐个检查是否已有进程在监听。环境变量优先于 .env。
#
# 用法：
#   bash scripts/check_ports.sh
#
# 退出码：全部空闲 0；有冲突 1。
#
# 注意：如果冲突的端口是本项目自己已经 `docker compose up` 起来的容器，
# 占用进程会显示为 docker-proxy / com.docker.backend，这种情况可以忽略。

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"

# 读 .env 里的某个键（不 source，避免执行 .env 里的内容）。
read_env() { # $1 = 变量名  $2 = 默认值
  local key="$1" def="$2" val=""
  if [ -n "${!key-}" ]; then
    printf '%s' "${!key}"
    return
  fi
  if [ -f "$ENV_FILE" ]; then
    val="$(sed -n -E "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*([^#[:space:]]*).*/\1/p" "$ENV_FILE" \
      | tail -n 1 | tr -d "\r\"'")"
  fi
  printf '%s' "${val:-$def}"
}

# 端口是否有进程在监听
is_busy() { # $1 = 端口
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
  elif command -v ss >/dev/null 2>&1; then
    ss -ltn "sport = :$1" 2>/dev/null | grep -q LISTEN
  else
    (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
  fi
}

# 占用进程名（拿不到就输出空）
owner() { # $1 = 端口
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $1 " (pid " $2 ")"}'
  elif command -v ss >/dev/null 2>&1; then
    ss -ltnp "sport = :$1" 2>/dev/null | sed -n -E 's/.*users:\(\("([^"]*)",pid=([0-9]+).*/\1 (pid \2)/p' | head -n 1
  fi
}

conflicts=0

check() { # $1 = 服务名  $2 = 变量名  $3 = 默认端口
  local port who
  port="$(read_env "$2" "$3")"
  if ! [[ "$port" =~ ^[0-9]+$ ]]; then
    conflicts=$((conflicts + 1))
    echo "✗ $1: $2 的值 '$port' 不是端口号，请修改 .env 中的 $2"
    return
  fi
  if is_busy "$port"; then
    conflicts=$((conflicts + 1))
    who="$(owner "$port")"
    echo "✗ $1 端口 $port 已被占用${who:+：$who}"
    echo "    → 修改 .env 中的 $2（改成一个空闲端口）"
  else
    echo "✓ $1 端口 $port 空闲"
  fi
}

check web      WEB_HOST_PORT      3100
check api      API_HOST_PORT      8100
check postgres POSTGRES_HOST_PORT 5442
check redis    REDIS_HOST_PORT    6479
check opencode OPENCODE_HOST_PORT 3921

# 改了 API 端口且配置了 NEXT_PUBLIC_API_URL：前端地址要同步改，并重新构建 web
api_port="$(read_env API_HOST_PORT 8100)"
api_url="$(read_env NEXT_PUBLIC_API_URL "")"
if [ "$api_port" != "8100" ] && [ -n "$api_url" ]; then
  echo ""
  echo "提醒：你改了 API_HOST_PORT，且 .env 里设置了 NEXT_PUBLIC_API_URL（$api_url）。"
  echo "      请把 NEXT_PUBLIC_API_URL 改成新的 API 地址，然后重新构建 web："
  echo "      docker compose build web && docker compose up -d web"
fi

if [ "$conflicts" -gt 0 ]; then
  echo ""
  echo "发现 $conflicts 个端口冲突，请按上面的提示修改 .env 后再 docker compose up -d。"
  exit 1
fi

echo ""
echo "所有端口空闲。"
exit 0
