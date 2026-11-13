#!/bin/sh
# HCA 语言守卫（提示词侧）· UserPromptSubmit · 同 guard.sh / context.sh 的两文件结构。
# stdout 会被上游追加到用户这一条消息正文后面（cc_hooks.rs:680-684）。
#
# I2：改成先问常驻服务（.hooks/hookd.py），服务不在就由 hook_client.sh
# 退回原来的「起一个 python 跑 lang.py」。判定逻辑一个字没变 ——
# 服务端调的就是同一份 lang.py。`HCA_HOOKD=0` 可以整条关掉。
set -u
exec "$(dirname "$0")/hook_client.sh" lang lang.py

