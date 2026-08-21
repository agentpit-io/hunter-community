"""把「只靠本地 K 线就能算」的技术因子补齐 —— 不需要任何 key。

为什么单独做这一个脚本:

`factor_value` 表里只有 pe_inv / pb_inv / dividend_yield 三个因子有数据,
而因子广场列了 20 个。用户随手选几个动量、均线类的因子,回测就一只票
都选不出来 —— 这是 B1 那份「年化 -5.07% 却全程 0 持仓」报告的源头。

而这些技术因子**根本不需要外部数据**:它们全部从本地 `klines` 表算,
既不走 hunter 网关,也不碰 AKShare。它们没有数据纯粹是因为从来没人
跑过计算,不是因为拿不到。

用法:
  docker compose exec -T api python3 /app/scripts/backfill_local_factors.py [月数]

默认补最近 12 个月(klines 的完整覆盖从 2025-08 开始,更早只有 3 只股票)。
"""
import sys
from datetime import date, timedelta

sys.path.insert(0, "/app")

from app.services.quant import backtest_engine as bt          # noqa: E402
from app.services.quant import factor_engine as fe            # noqa: E402
from app.services.quant import universe as uv                 # noqa: E402

# 名单在 factor_engine 里 —— 每日定时任务用的是同一份。
# 抄成两份的话,加了新因子只改一处,而"回填补了、每日没算"这种
# 不一致要等用户回测选不出票才会发现。
LOCAL_FACTORS = fe.LOCAL_ONLY


def main() -> int:
    months = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    end = date.today()
    start = end - timedelta(days=months * 31)

    codes = uv.query_current("000300")
    if len(codes) < 100:
        print(f"[!] 股票池只有 {len(codes)} 只 · 先 seed 成分股再跑")
        return 1

    # 调仓日历取 W —— 它是最密的一档,M/Q/H 的调仓日是它的子集之外还可能
    # 落在别的日子上,所以把 W 和 M 的并集都算上,四种频率就都有数据了
    days = sorted(set(bt._rebalance_dates(start, end, "W"))
                  | set(bt._rebalance_dates(start, end, "M")))
    if not days:
        print("[!] 这个区间 klines 里没有交易日")
        return 1

    print(f"股票池 {len(codes)} 只 · {len(days)} 个调仓日 "
          f"({days[0]} ~ {days[-1]}) · {len(LOCAL_FACTORS)} 个因子")

    total = 0
    for i, d in enumerate(days, 1):
        line = []
        for key in LOCAL_FACTORS:
            try:
                n = fe.compute_and_store(key, codes, d)
            except Exception as e:                              # noqa: BLE001
                # 一个因子炸掉不该带走整轮 —— 但**要打出来**,
                # 静默跳过的结果是最后看到"补完了"而表里还是空的
                print(f"    {d} {key} 失败: {type(e).__name__} {str(e)[:60]}")
                n = 0
            total += n
            line.append(f"{key.split('_')[0]}={n}")
        print(f"[{i}/{len(days)}] {d} · " + " ".join(line))
    print(f"\n完成 · 共写入 {total} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
