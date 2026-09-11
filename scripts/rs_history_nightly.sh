#!/bin/sh
# 每晚拉全市场日线 → RS 线上涨天数 + 精确 RS 评级(apps/api/app/services/quant/rs_history.py)
#
# 由 fin-r1 宿主机 crontab 调用 —— 容器里 HUNTER_MINIMAL_BOOT=1,后台调度器是关的,
# 不能指望 api 进程自己定时跑。时间按上海时间(服务器时区):
#   30 6  * * *  ~/hunter-community/scripts/rs_history_nightly.sh us     >> ~/rs_history.log 2>&1
#   30 17 * * *  ~/hunter-community/scripts/rs_history_nightly.sh a hk   >> ~/rs_history.log 2>&1
# 美股收盘 = 上海时间凌晨 4 点(夏令时)/ 5 点(冬令时),06:30 两种都赶得上。
# 每天都跑(含周末)也没关系:整窗覆盖重写是幂等的,周末跑一次等于重算一遍同样的数。
#
# 退出码非 0 = 完整性检查没过(最新交易日有收盘价的 < 90%)或基准拉取失败,看日志。
cd "$(dirname "$0")/.." || exit 1
rc=0
for m in "$@"; do
  echo "=== $(date '+%F %T') rs_history $m"
  docker compose exec -T api python -m app.services.quant.rs_history run --market "$m" || rc=$?
done
exit $rc
