#!/bin/sh
# HCA 上下文注入 hook · UserPromptSubmit · 同 guard.sh 的两文件结构。
#
# stdout 会被上游**追加到用户这一条消息的正文后面**（cc_hooks.rs:680-684），
# 所以这里输出什么，模型就多看到什么。拿不到的数据一律不输出（总控红线 1）。
#
# I2：改成先问常驻服务（.hooks/hookd.py），服务不在就由 hook_client.sh
# 退回原来的「起一个 python 跑 context.py」。判定逻辑一个字没变 ——
# 服务端调的就是同一份 context.py。`HCA_HOOKD=0` 可以整条关掉。
set -u
exec "$(dirname "$0")/hook_client.sh" context context.py

