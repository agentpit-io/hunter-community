#!/bin/sh
# HCA 预算 hook · UserPromptSubmit（拦）+ Stop / StopFailure（记账）· 默认关闭。
# 开关是 HCA_BUDGET_ENABLED=1；关着的时候 budget.py 读完 stdin 立刻 exit 0、什么都不做。
#
# I2：改成先问常驻服务（.hooks/hookd.py），服务不在就由 hook_client.sh
# 退回原来的「起一个 python 跑 budget.py」。判定逻辑一个字没变 ——
# 服务端调的就是同一份 budget.py。`HCA_HOOKD=0` 可以整条关掉。
set -u
exec "$(dirname "$0")/hook_client.sh" budget budget.py

