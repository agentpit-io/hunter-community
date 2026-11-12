#!/usr/bin/env bash
# HCA 一键拉起。**可重复执行** —— 再跑一遍就是「确保栈是健康的」，
# 不会因为容器已经在跑、或者上次跑到一半被人清掉而失败。
#
#     bash deploy/up.sh              # 构建（有缓存）+ 启动 + 等健康 + 自检
#     bash deploy/up.sh --no-build   # 不构建，只启动
#     bash deploy/up.sh --rebuild    # 强制不用缓存重建
#     bash deploy/up.sh --down       # 停掉并删容器（**不删数据卷**）
#     bash deploy/up.sh --status     # 只看状态
#     bash deploy/up.sh --admin      # 只建/重置管理员账号（M3）
#
# 测试机注意（总控「测试机与 HunterLauncher 链路共用」）：
#   · 只碰 compose 项目 hca 与 hca-* 镜像/卷，绝不 prune、绝不 stop docker 服务。
#   · docker 不是 active 时**等**（另一条链路在测"无 Docker"场景，会自己恢复），
#     每 60 秒查一次，最多 60 分钟。
#   · 构建走 flock ~/.hca-heavy.lock 串行化，避免和对方的 cargo build 抢内存。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${HERE}/docker-compose.yml"
ENV_FILE="${HERE}/.env"
# compose 项目名。默认 hca；install.sh 装到别的目录时会给一个不同的名字，
# 这样同一台机器上可以并存两套栈（**卷也是按项目名隔离的**，互不覆盖）。
# .env 是在 prepare_env 里才 source 的，而这里就要用，所以直接从文件里取一行。
PROJECT="${HCA_COMPOSE_PROJECT:-}"
if [ -z "$PROJECT" ] && [ -f "$ENV_FILE" ]; then
  PROJECT="$(sed -n 's/^HCA_COMPOSE_PROJECT=//p' "$ENV_FILE" | tail -1)"
fi
PROJECT="${PROJECT:-hca}"
HEAVY_LOCK="${HOME}/.hca-heavy.lock"
DOCKER_WAIT_MINUTES="${HCA_DOCKER_WAIT_MINUTES:-60}"
HEALTH_TIMEOUT="${HCA_HEALTH_TIMEOUT:-300}"

MODE=up
BUILD=1
NOCACHE=0
for a in "$@"; do
  case "$a" in
    --no-build) BUILD=0 ;;
    --rebuild)  NOCACHE=1 ;;
    --down)     MODE=down ;;
    --status)   MODE=status ;;
    --admin)    MODE=admin ;;
    -h|--help)  sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数：$a" >&2; exit 2 ;;
  esac
done

log() { printf '[up.sh] %s\n' "$*"; }
warn() { printf '[up.sh] ⚠ %s\n' "$*" >&2; }
die() { printf '[up.sh] ✗ %s\n' "$*" >&2; exit 1; }

dc() { docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }

# ── docker 可用性 ───────────────────────────────────────────────────────────
wait_for_docker() {
  local waited=0
  while ! systemctl is-active --quiet docker 2>/dev/null; do
    if docker info >/dev/null 2>&1; then return 0; fi   # 没有 systemd 的环境
    if [ "$waited" -ge "$DOCKER_WAIT_MINUTES" ]; then
      die "等了 ${DOCKER_WAIT_MINUTES} 分钟 docker 还没起来。这台机器上另一条链路可能正在测「无 Docker」场景，稍后再试。"
    fi
    log "docker 服务不是 active，等 60 秒再看（已等 ${waited} 分钟 / 上限 ${DOCKER_WAIT_MINUTES}）"
    sleep 60
    waited=$((waited + 1))
  done
  docker info >/dev/null 2>&1 || die "docker 服务 active，但 docker info 失败（权限？）"
}

# ── 密钥目录的权限：**容器里是 uid 10001(hca)，不是你** ────────────────────
#
# daemon 镜像里 `USER hca`（uid/gid 固定 10001，见 Dockerfile.daemon:173），
# 而宿主上 install.sh 写出来的 key 文件是 0600 owner=运维自己 ——
# 容器**读不到**，hca-init 直接 PermissionError 起不来（M4 从零安装实测撞到；
# 主部署一直没暴露这个问题，因为它走的是 HCA_LLM_API_KEY 环境变量那条路）。
#
# 目标权限：owner 仍是运维（方便换 key），**group = 10001**，
# 目录 0750、文件 0640。这样容器按组能读，同机其它用户读不到 ——
# 比"退成 0644"强。
#
# 改组需要 root。优先 `sudo -n`（无密码 sudo）；没有就借一个容器以 root
# 通过 bind mount 改（docker 本来就在，不额外要权限）。两条都不行才退成
# 0644/0755 并**显著警告**。
harden_secrets() {
  local dir="$1"
  [ -d "$dir" ] || return 0
  local gid=10001
  # 已经对好了就不动（避免每次启动都起一个容器）。
  #
  # ⚠️ 这里**必须连文件一起看，不能只看目录**（I3 在测试机上真撞到）：
  # 目录一旦 chgrp 过，之后再往里放一把新 key（`cp` / 编辑器写出来的文件带的是
  # **当前用户的组**），这个函数会因为「目录已经对了」直接返回，新文件的组
  # 永远改不过来。后果是 daemon（uid 10001）读不到 key →
  # `[hca-init] provider=… key=（空）` → 第一轮真实对话被网关回 401，
  # **而 up.sh 的自检是过的**（自检只读模型清单，那一跳不需要 key）。
  local cur_g cur_m bad_files
  cur_g="$(stat -c '%g' "$dir" 2>/dev/null || echo -)"
  cur_m="$(stat -c '%a' "$dir" 2>/dev/null || echo -)"
  bad_files="$(find "$dir" -maxdepth 1 -type f \
                 \( ! -group "$gid" -o ! -perm -040 \) -printf '%f ' 2>/dev/null || echo '?')"
  if [ "$cur_g" = "$gid" ] && { [ "$cur_m" = 750 ] || [ "$cur_m" = 710 ]; } \
     && [ -z "$bad_files" ]; then
    log "密钥目录与其中的文件权限已对齐容器 uid 10001（${cur_m}, gid ${cur_g}）"
    return 0
  fi
  [ -n "$bad_files" ] && [ "$bad_files" != "? " ] \
    && log "密钥目录里这几个文件容器读不到，重新对齐：${bad_files}"

  if sudo -n true 2>/dev/null; then
    sudo chgrp -R "$gid" "$dir" && sudo chmod 0750 "$dir" \
      && find "$dir" -maxdepth 1 -type f -exec sudo chmod 0640 {} + 2>/dev/null
    log "密钥目录已 chgrp ${gid} + 0750/0640（用 sudo）"
    return 0
  fi

  # 借一个容器改（以 root 跑，只 chgrp/chmod，不动内容）。
  # 挑一个**本机已有**的镜像，避免为了 chmod 去拉一个新镜像。
  local img=""
  for cand in "hca-daemon:${HCA_IMAGE_TAG:-dev}" postgres:16-alpine redis:7-alpine alpine:3 busybox:stable; do
    if docker image inspect "$cand" >/dev/null 2>&1; then img="$cand"; break; fi
  done
  if [ -n "$img" ] && docker run --rm --user 0 -v "${dir}:/s" "$img" \
        sh -c "chgrp -R ${gid} /s && chmod 0750 /s && find /s -maxdepth 1 -type f -exec chmod 0640 {} +" >/dev/null 2>&1; then
    log "密钥目录已 chgrp ${gid} + 0750/0640（借 ${img} 以 root 改）"
    return 0
  fi

  chmod 0755 "$dir" 2>/dev/null || true
  find "$dir" -maxdepth 1 -type f -exec chmod 0644 {} + 2>/dev/null || true
  log "⚠ 改不了组（没有免密 sudo，也没有可用的本地镜像），密钥目录退成 0755/0644 ——"
  log "  **同机其它用户能读到模型 key**。想更严：用 sudo 跑一次本脚本，"
  log "  或者改用 HCA_LLM_API_KEY 环境变量（不落盘，但 docker inspect 看得到）。"
}

# ── 环境文件与密钥目录 ──────────────────────────────────────────────────────
prepare_env() {
  if [ ! -f "$ENV_FILE" ]; then
    log "没有 deploy/.env，从 env.example 复制一份（记得填 key）"
    cp "${HERE}/env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
  fi
  # ── M3：web 对公网，这四项必须有值，缺了就地生成并写回 deploy/.env ──
  #
  # 为什么要写回文件而不是只 export：compose 的 `${X:?}` 读的是 --env-file，
  # 而且**重建容器时必须还是同一把** —— 每次现生成的话，JWT_SECRET 一变
  # 所有人的登录态失效、postgres 口令一变 api 直接连不上库。
  local changed=0
  for pair in \
      "HCA_PG_PASSWORD:postgres 口令" \
      "FIN_PAPER_PASSWORD:智能炒股账本口令" \
      "HCA_JWT_SECRET:JWT 签名密钥" \
      "HUNTER_SETUP_TOKEN:首启向导口令" \
      "HUNTER_INTERNAL_KEY:/api/internal 共享密钥"; do
    local var="${pair%%:*}" what="${pair#*:}"
    if ! grep -qE "^${var}=.+" "$ENV_FILE"; then
      # 只写文件，不回显 —— 总控红线 2
      local val
      val="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
      sed -i "/^${var}=/d" "$ENV_FILE"
      printf '%s=%s\n' "$var" "$val" >> "$ENV_FILE"
      log "生成 ${var}（${what}），已写进 deploy/.env（值不回显）"
      changed=1
    fi
  done
  [ "$changed" = 1 ] && chmod 600 "$ENV_FILE"

  # shellcheck disable=SC1090
  set -a; . "$ENV_FILE"; set +a

  # ── 公网暴露前置检查（总控「端口与暴露」）────────────────────────────
  # web 是本栈里唯一对公网的服务。单用户免登录开着 = 谁打开页面谁就是管理员。
  if [ "${HUNTER_SINGLE_USER:-0}" = "1" ]; then
    die "web 对公网（${HCA_WEB_HOST_PORT:-3200}），但 HUNTER_SINGLE_USER=1（免登录）。改成 0 再跑。"
  fi

  local secrets_dir="${HCA_SECRETS_DIR:-./secrets}"
  case "$secrets_dir" in
    /*) : ;;
    *)  secrets_dir="${HERE}/${secrets_dir#./}" ;;
  esac
  if [ ! -d "$secrets_dir" ]; then
    log "建密钥目录 ${secrets_dir}（700）"
    mkdir -p "$secrets_dir"
    chmod 700 "$secrets_dir"
  fi

  # ── 管理员白名单：不写这一行，策略中心/回测页对自己建的管理员是 403 ──────────
  #
  # `apps/api/app/routers/backtest.py:_require_admin` 的判据是
  # 「JWT 里 role == 'ADMIN'（**大写**）或 email 在 HUNTER_ADMIN_EMAILS 白名单里」，
  # 而库里 role 的 CHECK 约束只允许小写 `'user'|'admin'`（auth.py:71/245）——
  # 也就是说 role 这条**永远不成立**，唯一进得去的路是白名单。
  # M4 回归实测：不设这一项时 `/api/backtest/config` 与 `/backtest/run/status` 都是 403，
  # 页面加载不出来。这里按我们自己建的那个管理员邮箱自动补上（用户显式设过就不动）。
  if ! grep -qE '^HUNTER_ADMIN_EMAILS=.+' "$ENV_FILE"; then
    local admin_email
    admin_email="$(sed -n 's/^HCA_ADMIN_EMAIL=//p' "$ENV_FILE" | tail -1)"
    admin_email="${admin_email:-admin@hca.agentpit.io}"
    sed -i "/^HUNTER_ADMIN_EMAILS=/d" "$ENV_FILE"
    printf 'HUNTER_ADMIN_EMAILS=%s\n' "$admin_email" >> "$ENV_FILE"
    log "补上 HUNTER_ADMIN_EMAILS=${admin_email}（否则策略中心/回测对管理员 403）"
    # shellcheck disable=SC1090
    set -a; . "$ENV_FILE"; set +a
  fi

  harden_secrets "$secrets_dir"

  # 只检查「有没有」，绝不打印内容
  local key_file="${HCA_LLM_API_KEY_FILE:-}"
  if [ -n "${HCA_LLM_API_KEY:-}" ]; then
    log "模型 key：来自 HCA_LLM_API_KEY（环境变量）"
  elif [ -n "$key_file" ]; then
    local host_path="${secrets_dir}/$(basename "$key_file")"
    if [ -s "$host_path" ]; then
      log "模型 key：${host_path}（$(wc -c <"$host_path" | tr -d ' ') 字节，内容不打印）"
    else
      log "⚠ 模型 key 文件 ${host_path} 不存在或为空 —— daemon 起得来，但没有可用模型。"
      log "  放一把 key 进去再跑一次即可（不用重建镜像）。"
    fi
  else
    log "⚠ 既没有 HCA_LLM_API_KEY 也没有 HCA_LLM_API_KEY_FILE"
  fi
}

# ── 健康等待 ────────────────────────────────────────────────────────────────
wait_healthy() {
  local svc="$1" waited=0 cid state
  while [ "$waited" -lt "$HEALTH_TIMEOUT" ]; do
    cid="$(dc ps -q "$svc" 2>/dev/null || true)"
    if [ -n "$cid" ]; then
      state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || echo unknown)"
      case "$state" in
        healthy|running) log "${svc}: ${state}（第 ${waited} 秒）"; return 0 ;;
        exited|dead)     log "✗ ${svc} 已退出，最后 40 行日志："; dc logs --tail 40 "$svc"; return 1 ;;
      esac
    fi
    sleep 3
    waited=$((waited + 3))
  done
  log "✗ ${svc} 在 ${HEALTH_TIMEOUT} 秒内没到 healthy，最后 60 行日志："
  dc logs --tail 60 "$svc"
  return 1
}

# ── 管理员账号（总控：web 对公网必须关免登录，所以得有个真账号）────────────
create_admin() {
  local api_port="${HCA_API_HOST_PORT:-8200}"
  # admin.txt 默认写在容器挂载的那个密钥目录里；**但那个目录是只读挂进 daemon 的**，
  # 而 daemon 里跑着模型的 bash 工具。把管理员口令和模型能碰到的目录分开更稳妥，
  # 所以留一个单独的开关（测试机上指向 ~/hca/secrets，见部署记录）。
  local secrets_dir="${HCA_ADMIN_SECRETS_DIR:-${HCA_SECRETS_DIR:-./secrets}}"
  case "$secrets_dir" in
    /*) : ;;
    *)  secrets_dir="${HERE}/${secrets_dir#./}" ;;
  esac
  HCA_WEB_HOST_PORT="${HCA_WEB_HOST_PORT:-3200}" \
  HCA_PUBLIC_HOST="${HCA_PUBLIC_HOST:-127.0.0.1}" \
    python3 "${HERE}/create_admin.py" --api "http://127.0.0.1:${api_port}" --secrets "$secrets_dir"
}

# ── 部署后自检（数字都来自真实调用，拿不到就写 —）─────────────────────────
selfcheck() {
  log "── 自检 ──"
  local cid
  cid="$(dc ps -q daemon)"
  [ -n "$cid" ] || { log "daemon 容器不在"; return 1; }

  local port="${HCA_DAEMON_PORT:-13456}"
  echo -n "  GET /health   : "
  docker exec "$cid" curl -fsS -m 5 "http://127.0.0.1:${port}/health" || echo "—（取不到）"
  echo
  echo -n "  GET /skills   : "
  docker exec "$cid" sh -c \
    "curl -fsS -m 10 -H \"Authorization: Bearer \$(cat /run/hca/daemon-token)\" http://127.0.0.1:${port}/skills" \
    | python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d),'个：',' '.join(x['name'] for x in d))" \
    2>/dev/null || echo "—（取不到）"
  echo -n "  GET /mcp/status: "
  docker exec "$cid" sh -c \
    "curl -fsS -m 10 -H \"Authorization: Bearer \$(cat /run/hca/daemon-token)\" http://127.0.0.1:${port}/mcp/status" \
    | python3 -c "
import json,sys
d=json.load(sys.stdin)
s=d.get('servers',[])
ok=[x for x in s if x.get('status')=='connected']
print(f\"{len(ok)}/{len(s)} connected，工具 {sum(x.get('tool_count') or 0 for x in s)} 个，trusted={d.get('trusted')}，blocked={d.get('blocked')}\")
for x in s:
    if x.get('status')!='connected':
        print('     ⚠', x.get('name'), x.get('status'), x.get('error') or '')
" 2>/dev/null || echo "—（取不到）"
  echo "  token 卷       : $(docker exec "$cid" sh -c 'ls -l /run/hca | tail -n +2 | wc -l') 个文件（内容不打印）"

  local rc=0
  # 下面两项是**断言**，不是打印。两者都属于「坏了但服务照样 healthy」那一类：
  #   · `.hooks.json` 解析失败是**静默**的（questions A8），hook 全不生效也不报错；
  #   · 大小闸不在镜像里，MCP 返回就退回「被内核砍成半截 JSON」（待办池 P0-11）。
  echo -n "  hook 注册      : "
  local hn
  hn="$(docker exec -w /workspace "$cid" atomcode hooks list 2>/dev/null \
        | awk '/^ *Total/{print $2}' | head -1)"
  if [ "${hn:-0}" -ge "${HCA_EXPECT_HOOKS:-8}" ] 2>/dev/null; then
    echo "${hn} 条"
  else
    echo "—（拿到 ${hn:-空}，期望 ≥ ${HCA_EXPECT_HOOKS:-8}；.hooks.json 可能解析失败了）"
    rc=1
  fi
  # 只查"文件在不在"是不够的 —— kronos / truesource 跑在另一个 venv 里，
  # 它们能不能 import 到这份闸，取决于 `.mcp.json` 里给没给 PYTHONPATH=/opt/hca/mcp。
  # 少了那一行，文件照样在、自检照样绿，而这两个 server 的 import 会失败、
  # 静默退回"不裁"（server.py 里的 ImportError 分支只往 stderr 打一行）。
  # 所以断言三件事：文件在、两个 server 的 PYTHONPATH 配着、按那个 PYTHONPATH 真能 import。
  echo -n "  MCP 大小闸     : "
  local sg_msg sg_rc
  sg_msg="$(docker exec "$cid" sh -c '
      set -e
      test -f /opt/hca/mcp/hca_size_guard.py || { echo "缺失：/opt/hca/mcp/hca_size_guard.py 不在镜像里"; exit 1; }
      PYTHONPATH=/opt/hca/mcp /opt/hca/venv/bin/python -c "import hca_size_guard" \
        || { echo "在，但 /opt/hca/venv 的 python import 不到（kronos / truesource 会静默不裁）"; exit 1; }
      echo ok' 2>&1)" && sg_rc=0 || sg_rc=1
  if [ "$sg_rc" -eq 0 ]; then
    # 再核一遍工作区里真正生效的那份 .mcp.json（AtomCode 读的是它，不是模板）
    local sg_pp
    sg_pp="$(docker exec "$cid" /opt/hca/venv/bin/python - <<'PYEOF' 2>/dev/null
import json, re, sys
try:
    t = open("/workspace/.mcp.json", encoding="utf-8").read()
except OSError as e:
    print("读不到 /workspace/.mcp.json:", e); sys.exit(0)
t = re.sub(r"(?m)^\s*//.*$", "", t)               # 模板里有 // 注释，AtomCode 许，json 不许
try:
    srv = (json.loads(t).get("mcpServers") or {})
except Exception as e:
    print("解析不了 .mcp.json:", e); sys.exit(0)
bad = [n for n in ("kronos", "truesource")
       if "/opt/hca/mcp" not in ((srv.get(n) or {}).get("env") or {}).get("PYTHONPATH", "")]
print("缺 PYTHONPATH: " + ", ".join(bad) if bad else "")
PYEOF
)"
    if [ -n "$sg_pp" ]; then
      echo "—（$sg_pp —— 这两个 server 的大小闸不会生效）"; rc=1
    else
      echo "在且可 import（kronos / truesource 的 PYTHONPATH 也配着）"
    fi
  else
    echo "—（${sg_msg}）"; rc=1
  fi

  # ── M3：api 与 web ──
  local api_port="${HCA_API_HOST_PORT:-8200}" web_port="${HCA_WEB_HOST_PORT:-3200}"
  local paper_port="${HCA_PAPER_HOST_PORT:-8220}" fin_worker_port="${HCA_FIN_WORKER_HOST_PORT:-8320}"
  echo -n "  api /api/health: "
  curl -fsS -m 10 "http://127.0.0.1:${api_port}/api/health" || echo "—（取不到）"
  echo
  echo -n "  paper /healthz : "
  curl -fsS -m 10 "http://127.0.0.1:${paper_port}/healthz" || echo "—（取不到）"
  echo
  echo -n "  fin-worker     : "
  curl -fsS -m 10 "http://127.0.0.1:${fin_worker_port}/healthz" || echo "—（取不到）"
  echo
  echo -n "  api 注册策略   : "
  curl -fsS -m 10 "http://127.0.0.1:${api_port}/api/auth/status" \
    | python3 -c "import json,sys;d=json.load(sys.stdin);print('single_user=',d.get('single_user'),' registration_mode=',d.get('registration_mode'),sep='')" \
    2>/dev/null || echo "—（取不到）"
  echo -n "  web 首页       : "
  curl -fsS -o /dev/null -w 'HTTP %{http_code}（%{time_total}s）\n' -m 20 "http://127.0.0.1:${web_port}/" \
    || { echo "—（取不到）"; rc=1; }
  echo -n "  web→daemon     : "
  # 走 BFF 的公共资源端点：通了说明 web 读到了 daemon token 且内网可达。
  # **这一跳失败就是部署失败** —— M3 首次部署时它打的是 `model=—`，
  # 而 web 容器本身 healthy（健康检查只看首页），差点被当成正常（报告 §3.2）。
  if curl -fsS -m 20 "http://127.0.0.1:${web_port}/api/opencode/config" \
       | python3 -c "
import json,sys
d=json.load(sys.stdin)
m=d.get('model')
if not m: raise SystemExit(1)
print('model=',m,sep='')
"; then :; else echo "—（取不到：web 读不到 daemon token，或内网不通）"; rc=1; fi
  return "$rc"
}

case "$MODE" in
  down)
    wait_for_docker
    prepare_env
    log "停止并删除 hca 的容器（数据卷保留：workspace / atomcode-home / daemon-token）"
    dc down --remove-orphans
    exit 0
    ;;
  status)
    wait_for_docker
    prepare_env
    dc ps
    selfcheck || true
    exit 0
    ;;
  admin)
    wait_for_docker
    prepare_env
    create_admin
    exit $?
    ;;
esac

wait_for_docker
prepare_env

if [ "$BUILD" = 1 ]; then
  # web 那一层是 `next build`，**是这个栈里最吃内存的一步**（总控要求构建前查内存）。
  # 低于 3G 就等：这台机器上另一条链路的 cargo build 会把内存吃光，硬上会 OOM
  # 被杀，而 OOM 的报错在 docker build 里长得像编译错误，很容易误判成代码问题。
  waited=0
  while :; do
    avail=$(free -g | awk '/^Mem:/{print $7}')
    [ "${avail:-0}" -ge 3 ] && break
    if [ "$waited" -ge 30 ]; then
      die "等了 30 分钟可用内存仍不足 3G（当前 ${avail}G）。另一条链路可能在跑 cargo，稍后再试。"
    fi
    log "可用内存 ${avail}G < 3G，等 60 秒（已等 ${waited} 分钟 / 上限 30）"
    sleep 60
    waited=$((waited + 1))
  done
  log "可用内存 ${avail}G，开始构建"
  log "取重负载锁 ${HEAVY_LOCK}（和这台机器上另一条链路的 cargo build 串行化）"
  # next build 的堆上限由 compose 的 build arg 传进构建容器
  # （在宿主 export NODE_OPTIONS 是没用的，构建跑在容器里）——
  # 见 deploy/docker-compose.yml 的 web.build.args 与 apps/web/Dockerfile。
  # **构建时必须把 HCA_DAEMON_VARIANT 清空。** daemon 服务的 `image:` 同时是
  # 「构建产物的 tag」和「启动时用的镜像」；变体非空时 compose 会把**官方二进制那份
  # Dockerfile 的产物**打成 `hca-daemon:<tag>-fork`，等于悄悄把 fork 那一层覆盖掉
  # —— 香港那台就这么中过一次：跑着 `-fork` 的 tag，里面却是官方二进制。
  # 清空之后构建产物固定是基础 tag，fork 那一层由 deploy/switch-to-fork.sh 另外叠。
  if [ "$NOCACHE" = 1 ]; then
    HCA_DAEMON_VARIANT= flock "$HEAVY_LOCK" docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build --no-cache
  else
    HCA_DAEMON_VARIANT= flock "$HEAVY_LOCK" docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build
  fi

  # 跑 fork 变体时：基础镜像刚重建过，fork 那一层就旧了（它是叠在基础镜像上的）。
  # 不提醒的话表现是「代码更新了但模型行为没变」，而且看不出原因。
  if [ -n "${HCA_DAEMON_VARIANT:-}" ]; then
    base="hca-daemon:${HCA_IMAGE_TAG:-dev}"
    fork="hca-daemon:${HCA_IMAGE_TAG:-dev}${HCA_DAEMON_VARIANT}"
    b=$(docker image inspect "$base" --format '{{.Created}}' 2>/dev/null || echo "")
    f=$(docker image inspect "$fork" --format '{{.Created}}' 2>/dev/null || echo "")
    fork_bin="${HCA_FORK_BIN:-/mnt/mixplode/hca-forkbin/atomcode-i2}"
    if [ -z "$f" ] || { [ -n "$b" ] && [ "$f" \< "$b" ]; }; then
      if [ -f "$fork_bin" ]; then
        log "基础镜像已更新，使用 ${fork_bin} 自动重叠 ${fork}"
        bash "${HERE}/fork-image.sh" "$fork_bin"
      else
        warn "fork 那一层（${f}）比基础镜像（${b}）旧且未找到 ${fork_bin}，请手动跑 bash deploy/switch-to-fork.sh <二进制>"
      fi
    fi
  fi
fi

log "启动"
dc up -d --remove-orphans

wait_healthy llm-shim   || die "llm-shim 没起来"
wait_healthy daemon     || die "daemon 没起来"
wait_healthy postgres   || die "postgres 没起来"
wait_healthy redis      || die "redis 没起来"
wait_healthy api        || die "api 没起来"
wait_healthy paper      || die "paper 没起来"
wait_healthy temporal   || die "temporal 没起来"
wait_healthy fin-worker || die "fin-worker 没起来"
wait_healthy web        || die "web 没起来"

# web 对公网 + 免登录已关 = 没有管理员账号这套栈就没法用。所以这一步失败就是部署失败，
# 不能只打个 ⚠ 就往下走（M3 首次部署正是这么漏过去的：邮箱 422，栈却报「完成」）。
admin_rc=0
create_admin || admin_rc=$?

sc_rc=0
selfcheck || sc_rc=$?
[ "$sc_rc" -eq 0 ] || die "自检没全过（上面标 — 的那几项）"
[ "$admin_rc" -eq 0 ] || die "管理员账号没建成（上面有原因）。栈在跑但没法登录；修完跑 bash deploy/up.sh --admin"
log "完成。web http://<本机 IP>:${HCA_WEB_HOST_PORT:-3200} · api 127.0.0.1:${HCA_API_HOST_PORT:-8200} · daemon 只在内网"
