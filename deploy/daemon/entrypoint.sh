#!/usr/bin/env bash
# HCA daemon 容器入口。
#
# 顺序是有讲究的，每一步都对应一个 M0 实测出来的约束：
#
#  1. hca-init.py 铺工作区 + 渲染 config.toml（必须在 daemon 起来之前 ——
#     daemon 启动时就会读 config.toml 和工作目录里的 .mcp.json / .hooks.json）。
#  2. 起 daemon，**cwd 设成工作区**。daemon 的初始项目目录取
#     `default_workdir` → 进程 cwd（上游 resolve_initial_working_dir），
#     两条我们都占上，不留悬念。
#  3. 等 /health。
#  4. 把 daemon 自己生成的 token 抄到共享卷，供 web / api 读。
#     **daemon 没有 --token 参数**（5.1.0 的 `daemon --help` 只有 --port /
#     --client / --idle-timeout / --no-auth / --no-telemetry），token 只能由它
#     自己生成写进 $ATOMCODE_HOME/daemon-<port>.json，我们只能转发。
#  5. POST /cd → POST /live/mcp/trust → POST /mcp/reload。
#     项目级 .mcp.json 有**信任门**：没信任过的项目里 server 根本不连
#     （M0 §5.2，`blocked: untrusted project`）。发行版必须自动过这道门，
#     不能让用户第一次问行情时撞上一个 "blocked"。
#  6. 起 socat 把 daemon 暴露到容器网络。
#     **daemon 恒定绑 127.0.0.1**（5.1.0 的 daemon 子命令没有 --host，
#     上游 README 写了但实际没有），compose 里 web/api 直连容器 IP 连不上。
#     所以 daemon 听内网回环 $HCA_DAEMON_INTERNAL_PORT，socat 在
#     0.0.0.0:$HCA_DAEMON_PORT 上转发过去。鉴权仍然是 daemon 的 Bearer token，
#     socat 只是搬运，不降低任何安全性；而且这个端口按总控端口表**不发布到宿主**。
#
# 安全：不打印 token / key；出错即退出（set -euo pipefail），让 compose 的
# restart 策略接手，不留一个"起来了但没连上 MCP"的半死状态。
set -euo pipefail

WORKSPACE="${HCA_WORKSPACE:-/workspace}"
ATOMCODE_HOME="${ATOMCODE_HOME:-/data/atomcode}"
PORT="${HCA_DAEMON_PORT:-13456}"                      # 容器网络上对外的端口
INTERNAL_PORT="${HCA_DAEMON_INTERNAL_PORT:-13457}"    # daemon 真正监听的回环端口
TOKEN_DIR="${HCA_TOKEN_DIR:-/run/hca}"
BOOT_TIMEOUT="${HCA_BOOT_TIMEOUT:-90}"                # 等 /health 的秒数
MCP_TIMEOUT="${HCA_MCP_TIMEOUT:-120}"                 # 单次等 MCP 收敛的秒数
MCP_RELOAD_ATTEMPTS="${HCA_MCP_RELOAD_ATTEMPTS:-3}"   # 有 server 报 error 时重挂几次
MCP_RETRY_WAIT="${HCA_MCP_RETRY_WAIT:-15}"            # 两次重挂之间等多久

log() { printf '[entrypoint] %s\n' "$*"; }

# ── 1. 初始化 ────────────────────────────────────────────────────────────────
python3 /opt/hca/bin/hca-init.py

# ── 1.5 起 hook 常驻服务（I2）────────────────────────────────────────────────
#
# hook 的固定开销：上游每触发一次就 spawn 一次 command，而 guard + audit 是
# **每次工具调用都要跑**的两个 python。容器里实测 python 空转 158 ms、
# guard 一次 1 236 ms —— 一道 22 次调用的题光 hook 就 35 秒。
# hookd 把五个 hook 预加载进一个常驻进程，.sh 包装改成 bash 客户端。
# **只听容器内回环**；起不来不算失败 —— 客户端会退回原来的「自己起 python」。
HOOKD_PORT="${HCA_HOOKD_PORT:-13458}"
HOOKD_PID=""
if [ "${HCA_HOOKD:-1}" != "0" ] && [ -f "${WORKSPACE}/.hooks/hookd.py" ]; then
  # ⚠️ 必须用 $HCA_PYTHON（= /opt/hca/venv/bin/python），不能用系统 python3。
  # hook 的退回路径本来就是 `PY="${HCA_PYTHON:-python3}"`，两条路径得是同一个解释器；
  # 而且 context.py 在交易日历缓存过期时要 `import akshare` —— akshare 只装在
  # /opt/hca/venv 里，用系统 python3 起服务的话那一行会**悄无声息地不输出**。
  "${HCA_PYTHON:-python3}" "${WORKSPACE}/.hooks/hookd.py" &
  HOOKD_PID=$!
  for i in $(seq 1 20); do
    if { exec 9<>"/dev/tcp/127.0.0.1/${HOOKD_PORT}"; } 2>/dev/null; then
      exec 9<&- 2>/dev/null
      log "hook 常驻服务就绪（127.0.0.1:${HOOKD_PORT}，第 ${i} 次探测）"
      break
    fi
    sleep 0.5
    if [ "$i" -eq 20 ] ; then
      log "⚠ hook 常驻服务 10 秒内没起来 —— hook 退回「每次起一个 python」，功能不受影响"
    fi
  done
else
  log "hook 常驻服务未启用（HCA_HOOKD=${HCA_HOOKD:-1}）"
fi

# ── 2. 起 daemon ─────────────────────────────────────────────────────────────
cd "$WORKSPACE"
log "启动 daemon：回环 127.0.0.1:${INTERNAL_PORT}，工作目录 ${WORKSPACE}"
atomcode daemon --port "$INTERNAL_PORT" --idle-timeout 0 --no-telemetry &
DAEMON_PID=$!

SOCAT_PID=""
SOCAT_LEGACY_PID=""
cleanup() {
  log "收到退出信号，关闭子进程"
  [ -n "$HOOKD_PID" ] && kill "$HOOKD_PID" 2>/dev/null || true
  [ -n "$SOCAT_PID" ] && kill "$SOCAT_PID" 2>/dev/null || true
  [ -n "$SOCAT_LEGACY_PID" ] && kill "$SOCAT_LEGACY_PID" 2>/dev/null || true
  kill "$DAEMON_PID" 2>/dev/null || true
  wait "$DAEMON_PID" 2>/dev/null || true
}
trap cleanup TERM INT

# ── 3. 等 /health ────────────────────────────────────────────────────────────
BASE="http://127.0.0.1:${INTERNAL_PORT}"
for i in $(seq 1 "$BOOT_TIMEOUT"); do
  if curl -fsS -m 3 "${BASE}/health" >/tmp/health.json 2>/dev/null; then
    log "daemon 就绪（第 ${i} 秒）：$(cat /tmp/health.json)"
    break
  fi
  if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
    log "✗ daemon 进程已退出"
    wait "$DAEMON_PID"
    exit 1
  fi
  sleep 1
  if [ "$i" -eq "$BOOT_TIMEOUT" ]; then
    log "✗ ${BOOT_TIMEOUT} 秒内 /health 没通"
    cleanup
    exit 1
  fi
done

# ── 4. 发布 token ────────────────────────────────────────────────────────────
TOKEN_SRC="${ATOMCODE_HOME}/daemon-${INTERNAL_PORT}.json"
for i in $(seq 1 30); do
  [ -f "$TOKEN_SRC" ] && break
  sleep 1
done
if [ ! -f "$TOKEN_SRC" ]; then
  log "✗ 没等到 token 文件 ${TOKEN_SRC}"
  cleanup
  exit 1
fi
mkdir -p "$TOKEN_DIR"
# 只抄 token 字段；顺带写一份带 URL 的 json，省得调用方再拼地址
python3 - "$TOKEN_SRC" "$TOKEN_DIR" "$PORT" <<'PY'
import json, os, pathlib, sys
src, out_dir, port = sys.argv[1], pathlib.Path(sys.argv[2]), sys.argv[3]
tok = json.loads(pathlib.Path(src).read_text(encoding="utf-8"))["token"]
host = os.environ.get("HCA_DAEMON_SERVICE_NAME", "daemon")
(out_dir / "daemon-token").write_text(tok, encoding="utf-8")
(out_dir / "daemon.json").write_text(json.dumps({
    "base_url": f"http://{host}:{port}",
    "token": tok,
}, ensure_ascii=False) + "\n", encoding="utf-8")
for name in ("daemon-token", "daemon.json"):
    os.chmod(out_dir / name, 0o640)
print(f"[entrypoint] token 已发布到 {out_dir}/daemon-token（0640，{len(tok)} 字符，不打印内容）")
PY

AUTH="Authorization: Bearer $(cat "${TOKEN_DIR}/daemon-token")"

# ── 5. 切目录 + 过信任门 + 重载 MCP ──────────────────────────────────────────
CD_BODY=$(python3 -c 'import json,sys; print(json.dumps({"path": sys.argv[1], "set_default": True}))' "$WORKSPACE")
log "POST /cd → $(curl -fsS -m 10 -X POST -H "$AUTH" -H 'Content-Type: application/json' -d "$CD_BODY" "${BASE}/cd")"
log "POST /live/mcp/trust → $(curl -fsS -m 10 -X POST -H "$AUTH" "${BASE}/live/mcp/trust")"
# MCP 是异步补挂的（M0 §5.3 冷启动实测 3.40 秒），这里等它们收敛再放行健康检查，
# 免得 web 一上来就看到一堆 connecting。
#
# **为什么要重试**：9 个 stdio server 同时冷启动，其中 akshare 要 import
# pandas/akshare 一大家子。M1 在测试机（2 核）上实测，恰好撞上一次 docker build
# 收尾（load average 11.4）时，5 个 server 报
# `MCP request initialize timed out after 60000ms` —— 是机器抢不过来，不是配置错
# （同一份配置在空闲时 9/9 全连上）。这种情况重挂一次就好了，
# 不重试的话一次冷启动的运气不好会让整个发行版看起来"少了 5 个数据源"。
#
# 等不齐**不算失败**：少一个 MCP 不该让 daemon 起不来，如实打日志就行。
mcp_status() { curl -fsS -m 5 -H "$AUTH" "${BASE}/mcp/status" || echo '{}'; }

# POST /cd 与 POST /live/mcp/trust 本身已异步触发 MCP 子进程拉起。
# 先等第一轮自然收敛，避免一上来无条件 POST /mcp/reload 把正在初始化的进程杀掉（触发 "MCP server closed stdout" 假报错）。
STATUS="{}"
for i in $(seq 1 "$MCP_TIMEOUT"); do
  STATUS=$(mcp_status)
  if printf '%s' "$STATUS" | grep -q '"connected"' || printf '%s' "$STATUS" | grep -q '"error"'; then
    printf '%s' "$STATUS" | grep -q '"connecting"' || break
  fi
  sleep 1
done

if printf '%s' "$STATUS" | grep -q '"connected"' && ! printf '%s' "$STATUS" | grep -q '"error"' && ! printf '%s' "$STATUS" | grep -q '"connecting"'; then
  log "MCP 全部就绪：$(printf '%s' "$STATUS" | head -c 600)"
else
  for attempt in $(seq 1 "$MCP_RELOAD_ATTEMPTS"); do
    log "POST /mcp/reload（第 ${attempt}/${MCP_RELOAD_ATTEMPTS} 次）→ $(curl -fsS -m 30 -X POST -H "$AUTH" "${BASE}/mcp/reload" | head -c 300)"
    for i in $(seq 1 "$MCP_TIMEOUT"); do
      STATUS=$(mcp_status)
      printf '%s' "$STATUS" | grep -q '"connecting"' || break
      sleep 1
    done
    if printf '%s' "$STATUS" | grep -q '"error"'; then
      log "⚠ 有 MCP 没连上：$(printf '%s' "$STATUS" | head -c 600)"
      # 写成 if 而不是 `[ ... ] && { ...; }`：后者在条件为假时整条 AND 列表返回非零，
      # 在 set -e 下会把脚本直接带走（经典坑）。
      if [ "$attempt" -lt "$MCP_RELOAD_ATTEMPTS" ]; then
        log "等 ${MCP_RETRY_WAIT} 秒重挂"
        sleep "$MCP_RETRY_WAIT"
        continue
      fi
      log "⚠ 重试用尽，带着残缺的 MCP 继续起 —— 用 docker exec hca-daemon python3 /opt/hca/tools/dump_mcp_tools.py 看每个 server 的报错原文"
    else
      log "MCP 全部就绪：$(printf '%s' "$STATUS" | head -c 600)"
    fi
    break
  done
fi

# ── 6. 暴露到容器网络 ────────────────────────────────────────────────────────
log "socat 0.0.0.0:${PORT} → 127.0.0.1:${INTERNAL_PORT}"
socat "TCP-LISTEN:${PORT},fork,reuseaddr" "TCP:127.0.0.1:${INTERNAL_PORT}" &
SOCAT_PID=$!

# 兼容存量 HunterLauncher 0.1.22 将宿主 3921 映射到容器 3901 端口的转发
LEGACY_PORT="${HCA_LEGACY_OPENCODE_PORT:-3901}"
if [ -n "$LEGACY_PORT" ] && [ "$LEGACY_PORT" != "$PORT" ] && [ "$LEGACY_PORT" != "$INTERNAL_PORT" ]; then
  log "socat 0.0.0.0:${LEGACY_PORT} → 127.0.0.1:${INTERNAL_PORT} (兼容 0.1.22 端口映射)"
  socat "TCP-LISTEN:${LEGACY_PORT},fork,reuseaddr" "TCP:127.0.0.1:${INTERNAL_PORT}" &
  SOCAT_LEGACY_PID=$!
fi

log "就绪。健康检查 http://127.0.0.1:${PORT}/health"
wait "$DAEMON_PID"
