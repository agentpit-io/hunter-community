"""按新口径重算指定因子的历史 factor_value(2026-09-09)

用法(在 api 容器里 · 必须带 PYTHONPATH=/app):
    python scripts/recompute_factor_history.py rsi momentum_12m_1m
    python scripts/recompute_factor_history.py rsi --limit 5      # 先试 5 期看耗时

为什么需要:`factor_value` 表存的是**定时任务用默认参数算好的** z_score,
打分链路默认查这张表。所以任何改动了"默认口径"的修复,都必须把历史重算一遍,
否则回测会横跨两套口径 —— 而且从界面上完全看不出来,只会看到一段
无法解释的业绩变化。

2026-09-09 有两处口径修正需要它:
  · `rsi`             超买/超卖线处封顶(原来不封顶,导致超买卖线参数其实不生效)
  · `momentum_12m_1m` 降级式子写反(`min(-22, -(len//3))` 让"剔除最近 1 月"
                      在 270 根历史下变成"剔除最近 90 个交易日")

**只重算表里已经有的 (因子, 日期)**,并且用那一期原本参与打分的那批 code ——
不新增日期、不扩大股票池,重算前后行数应当基本一致。
`compute_and_store` 内部是 upsert,中断了重跑即可,不需要先删。
"""
import sys
import time
from datetime import date

from app.services.database import get_conn
from app.services.quant import factor_engine


def dates_and_codes(factor_key: str) -> list[tuple[date, list[str]]]:
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """SELECT trade_date, array_agg(code ORDER BY code)
             FROM factor_value WHERE factor_key = %s
            GROUP BY trade_date ORDER BY trade_date""",
        (factor_key,))
    rows = [(r[0], list(r[1])) for r in cur.fetchall()]
    cur.close(); conn.close()
    return rows


def recompute(factor_key: str, limit: int | None = None) -> None:
    rows = dates_and_codes(factor_key)
    if limit:
        rows = rows[-limit:]
    print(f"[{factor_key}] 待重算 {len(rows)} 期 · {rows[0][0]} → {rows[-1][0]}", flush=True)
    t0 = time.time()
    total = 0
    for i, (d, codes) in enumerate(rows, 1):
        n = factor_engine.compute_and_store(factor_key, codes, d)
        total += n
        if i % 20 == 0 or i == len(rows):
            el = time.time() - t0
            print(f"[{factor_key}] {i}/{len(rows)} 期 · 累计 {total} 行 · "
                  f"已用 {el:.0f}s · 平均 {el / i:.2f}s/期", flush=True)
    print(f"[{factor_key}] 完成 · {len(rows)} 期 · {total} 行 · {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    # 手写解析,不用 argparse —— 这脚本只有"因子名 + --limit N"两种输入。
    # 注意在容器里跑要带 PYTHONPATH=/app(`python scripts/x.py` 的 sys.path[0]
    # 是 scripts/,import app 会失败)
    keys: list[str] = []
    limit: int | None = None
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--limit":
            limit = int(argv[i + 1]); i += 2; continue
        if a.startswith("--limit="):
            limit = int(a.split("=", 1)[1]); i += 1; continue
        keys.append(a); i += 1
    if not keys:
        print(__doc__)
        sys.exit(1)
    for key in keys:
        recompute(key, limit)
