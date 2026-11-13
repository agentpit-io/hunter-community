#!/bin/sh
# HCA 审计 hook · PostToolUse / PostToolUseFailure · 选一个 python 然后把 stdin 原样交给 audit.py。
# 恒 exit 0：审计不参与权限判定，写不进日志也不能拖垮对话。
#
# 两个事件用的是同一个脚本（事件名从 stdin 的 hook_event_name 里读），
# 所以 .hooks.json 里是两条注册、一份实现。
#
# I2：改成先问常驻服务（.hooks/hookd.py），服务不在就由 hook_client.sh
# 退回原来的「起一个 python 跑 audit.py」。判定逻辑一个字没变 ——
# 服务端调的就是同一份 audit.py。`HCA_HOOKD=0` 可以整条关掉。
set -u
exec "$(dirname "$0")/hook_client.sh" audit audit.py

