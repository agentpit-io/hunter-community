#!/bin/sh
# HCA 研究守卫 hook · PreToolUse · 选一个 python 然后把 stdin 原样交给 guard.py。
#
# 为什么是 .sh + .py 两个文件（和 audit.sh / audit.py 同一个理由）：
# 在 .sh 里用 heredoc 喂 python 会占掉 stdin，python 读到的是脚本自己而不是
# hook 的输入（M1 实测踩过，session_id / tool 全是 null）。
#
# 退出码恒 0：判定走 stdout 最后一行 JSON（上游 cc_hooks.rs:794 last_json_line）。
# 不用 exit 2 —— exit 2 且无输出会被上游当成"脚本坏了"而放行，语义更绕。
#
# I2：改成先问常驻服务（.hooks/hookd.py），服务不在就由 hook_client.sh
# 退回原来的「起一个 python 跑 guard.py」。判定逻辑一个字没变 ——
# 服务端调的就是同一份 guard.py。`HCA_HOOKD=0` 可以整条关掉。
set -u
exec "$(dirname "$0")/hook_client.sh" guard guard.py

