"""三个市场的常设交易时段（当地时刻）· **L02 单一来源**。

⚠️ 本文件在 `apps/api/app/services/fin/market_sessions.py` 与
`apps/fin-worker/app/market_sessions.py` **逐字节相同**：两个服务各有各的构建上下文
（仓库根 / `apps/fin-worker/`），不能互相 import，只能各留一份同实现。
`apps/api/tests/test_l02_single_source.py` 逐字节比对两份文件，谁漂了谁红。

## 权威在数据库，这份常量只是离线兜底

`fin_market_rule.sessions`（`db/migrations/0030_market_rule.sql` 种下、`0034` 补上
港股收市竞价 16:00–16:10）才是**运行时唯一来源** —— paper 判「在不在交易时段」
读的就是那一行。本常量用于**读不到数据库 / HTTP 时**的兜底（fin-worker 的
`_market_sessions`、api 的 `seed_hk_us_calendar`），必须与那份种子**逐项一致**；
`test_l02_single_source.py` 直接从迁移文件里抠出种子值逐项比对，改一处漏一处会红。

港股那段 `16:00–16:10`（收市竞价 CAS）是**必须的**：数据源给的港股最新价本来就是
收市竞价的成交打印（腾讯 `00700.HK` 的 `event_time` = 16:08:10），少了它港股一笔都成交
不了（v1.5.1 修了另两份、漏了 api 那份，次日 v1.5.2 才补全 —— 这就是「同一事实多副本」
的代价，本段要根除）。
"""

from __future__ import annotations

MARKET_SESSIONS: dict[str, list[dict[str, str]]] = {
    "CN_A": [{"open": "09:30", "close": "11:30"}, {"open": "13:00", "close": "15:00"}],
    "HK": [{"open": "09:30", "close": "12:00"}, {"open": "13:00", "close": "16:00"},
           {"open": "16:00", "close": "16:10"}],
    "US": [{"open": "09:30", "close": "16:00"}],
}

# A 股时段（沿用旧名 `A_SESSIONS`，消费者不用改）。
A_SESSIONS = MARKET_SESSIONS["CN_A"]
