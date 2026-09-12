#!/bin/sh
# 每晚拉全市场日线 → RS 线上涨天数 + 精确 RS 评级(apps/api/app/services/quant/rs_history.py)
#
# 由 fin-r1 宿主机 crontab 调用 —— 容器里 HUNTER_MINIMAL_BOOT=1,后台调度器是关的,
# 不能指望 api 进程自己定时跑。目标时间是上海 06:30(美股)/ 17:30(A/港股)。
# ⚠ fin-r1 的 cron 进程按 **UTC** 计时(启动时系统还是 UTC,没重启过),所以 crontab 里写的是:
#   30 22 * * *  ~/hunter-community/scripts/rs_history_nightly.sh us     >> ~/rs_history.log 2>&1
#   30 9  * * *  ~/hunter-community/scripts/rs_history_nightly.sh a hk   >> ~/rs_history.log 2>&1
# 退出码 4 = 等了 90 分钟腾讯通道仍被占用(数据页的美股下载在跑),本轮没跑。
# 美股收盘 = 上海时间凌晨 4 点(夏令时)/ 5 点(冬令时),06:30 两种都赶得上。
# 每天都跑(含周末)也没关系:整窗覆盖重写是幂等的,周末跑一次等于重算一遍同样的数。
#
# 两个设计:
# · 用 `docker compose run --rm` 起**独立的一次性容器**,不 exec 进 api 容器 ——
#   A 股一轮要 90 分钟,期间 api 一重建(部署)exec 进去的进程就跟着死了
# · flock 互斥:上一轮没跑完(或者有人手动在跑)这一轮直接跳过。
#   两轮同时跑 = 两个进程各 1 次/秒打腾讯,限速就翻倍了(2026-09-11 WAF 事故)
#
# 退出码非 0 = 完整性检查没过(最新交易日有收盘价的 < 90%)/ 基准拉取失败 / 被 WAF 拦了,看日志。
cd "$(dirname "$0")/.." || exit 1
exec 9>/tmp/rs_history.lock
if ! flock -n 9; then
  echo "=== $(date '+%F %T') 上一轮还在跑,本轮跳过:$*"
  exit 0
fi
rc=0
for m in "$@"; do
  echo "=== $(date '+%F %T') rs_history $m"
  docker compose run --rm --no-deps -T api python -m app.services.quant.rs_history run --market "$m" || rc=$?
  # 美股日线落完 → 小鹿智能体跑今天(收盘后)这一步。它只读 rs_daily,不打腾讯;失败不影响日线的退出码
  if [ "$m" = "us" ]; then
    echo "=== $(date '+%F %T') agent daily"
    docker compose run --rm --no-deps -T api python -m app.services.quant.agent_run daily || echo "=== agent daily 失败 rc=$?"
  fi
done
echo "=== $(date '+%F %T') 结束 rc=$rc"
exit $rc
