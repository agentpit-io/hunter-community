#!/bin/bash
# HCA hook 客户端（I2）—— 先问常驻服务（hookd.py），问不到就退回原来的 python 跑法。
#
#     hook_client.sh <hook 名> <退回时要跑的脚本>
#
# 为什么用 bash 而不是 python：这一层的全部意义就是**不要再起 python**。
# 容器里实测 python 空转 158 ms，而 guard + audit 是每次工具调用都要跑的。
# bash 的 /dev/tcp 走回环连一下，几毫秒。
#
# 退回路径是硬要求：guard 是本发行版唯一的硬约束，服务没起来/答不了的时候
# **必须**照常跑一遍真正的 guard.py，不能因为「服务不在」就放行。
set -u
name="$1"
script="$2"
dir=$(dirname "$0")
port="${HCA_HOOKD_PORT:-13458}"

# stdin 先收进来 —— 退回路径还要用同一份 payload
payload=$(cat)

fallback() {
  PY="${HCA_PYTHON:-python3}"
  command -v "$PY" >/dev/null 2>&1 || PY=python3
  printf '%s' "$payload" | "$PY" "$dir/$script"
  exit $?
}

[ "${HCA_HOOKD:-1}" = "0" ] && fallback

if ! { exec 3<>"/dev/tcp/127.0.0.1/${port}"; } 2>/dev/null; then
  fallback
fi

printf '%s\n%s' "$name" "$payload" >&3
# ⚠️ 必须带读超时。服务端卡住的话 `cat` 会一直阻塞到 **hook 自己超时**，
# 而上游对超时的 hook 是**放行**的 —— 那等于 guard 悄悄失效了。
# 超时就走退回路径，慢一点但判定不会丢。超时值要明显小于 .hooks.json 里
# 最紧的那个 timeout_ms（guard 是 5000 ms）。
resp=$(timeout "${HCA_HOOKD_TIMEOUT:-2}" cat <&3)
exec 3<&- 2>/dev/null

case "$resp" in
  OK)   exit 0 ;;                       # 服务答了，而且输出是空的（= 放行）
  OK$'\n'*) printf '%s\n' "${resp#OK$'\n'}"; exit 0 ;;
  *)    fallback ;;                     # 没有状态行 = 服务出错，照常跑一遍
esac
