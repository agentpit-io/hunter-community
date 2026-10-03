-- 0039 · 幂等写入 A 股 2026 全年交易日历（365 行 / 242 个交易日）
--
-- 为什么需要它
-- ------------
-- `fin_market_calendar` **从建表起就没有任何迁移写过数据**（0023 只建表、0029 只加市场维度、
-- 0034 只 UPDATE 港美股时段）。于是**从迁移建起来的库，三个市场的日历都是空的**。
-- 后果（演示站实测）：`markets.py:market_status` 对缺行一律判 `state='unknown'`
-- （「未知 ≠ 交易日」），自动交易页对 A 股一直显示「日历缺失」，
-- `fin-worker` 的 CN_A 时点永远 `calendar_unknown` 空跑 —— A 股是本产品的**主市场**，
-- 这个缺口比港美股更致命。本文件把 A 股补上（港美股仍是缺口，见文末「未覆盖」）。
--
-- 口径（与运行时写入完全一致，便于下次 preopen 同步原地覆盖、不产生差异）
-- -------------------------------------------------------------------
--   · 交易日 = 周一~周五 且 不在下面 19 天法定休市清单里；
--   · 休市清单来源：akshare `tool_trade_date_hist_sina`（交易所日历，新浪转载），
--     经 `GET /api/internal/calendar/trading-days?market=a` 取出 —— **不是手抄**；
--   · `note` / `calendar_source` 两句常量沿用
--     `apps/fin-worker/app/activities.py:sync_calendar`；
--   · `sessions` 现取 `fin_market_rule`（CN_A: 09:30-11:30 / 13:00-15:00），不写死。
--     依赖关系安全：`0030_market_rule.sql` 先于本文件执行并种下 CN_A 那一行；
--     万一它不在，本迁移会**当场报错让 api 起不来**（`sessions` 是 NOT NULL），
--     而不是静默写入空时段 —— 这是故意的，见 `app/migrate.py` 文件头。
--
-- 幂等：`ON CONFLICT (market, trade_date) DO UPDATE`，重复执行结果不变。
-- 只 INSERT / UPDATE，不 DROP / ALTER。
--
-- ⚠️ 本文件**不带** `BEGIN;` / `COMMIT;` —— `app/migrate.py:apply_one` 已经把每个迁移文件
--    包在**独立事务**里执行（失败整体回滚 + 记账），文件内再写事务边界会破坏那个承诺。
--    `db/migrations/` 下现存文件无一自带事务边界，保持一致。
--
-- 📌 顺带更正：`0038_fee_model_cn_a.sql` 文件头那条「⚠️ 已有部署不会自动执行这个文件 ——
--    `db/migrations` 只在数据卷第一次初始化时跑」是**旧行为**（`docker-entrypoint-initdb.d`
--    时代）。自 `app/migrate.py` 上线后，**api 每次启动都按 `schema_migrations` 账本增量执行
--    `db/migrations/*.sql`**。演示站实测：api 容器 `2026-10-03T04:23:24Z` 启动，
--    `0038` 于 `04:23:27Z` 落账（+3 秒），即自动执行、无需手工补。
--    `0038` 文件本身**不改** —— 迁移一经发布不可变，改动会触发 checksum drift 告警。
--
-- 未覆盖（如实标注）：港美股 2026 全天日历仍只存在于演示站/本机库里，**不在迁移里**，
--    从零建的库这两个市场依旧「日历缺失」。本文件只补 CN_A，不顺手编港美股假期。

-- ═══════════════════════════════════════════════════════════════
-- 1 · 2026 年 A 股法定休市日（工作日部分，本身落在周末的不在此列）
-- ═══════════════════════════════════════════════════════════════
-- 19 天，来源见文件头。分节便于人工核对：
--   元旦 2 · 春节 6 · 清明 1 · 劳动节 3 · 端午 1 · 中秋 1 · 国庆 5
WITH holiday(d) AS (
  VALUES
    (date '2026-01-01'),  -- 元旦
    (date '2026-01-02'),  -- 元旦（调休放假）
    (date '2026-02-16'),  -- 春节（除夕前一日）
    (date '2026-02-17'),  -- 春节 正月初一
    (date '2026-02-18'),
    (date '2026-02-19'),
    (date '2026-02-20'),
    (date '2026-02-23'),  -- 春节（假期最后一天，周一）
    (date '2026-04-06'),  -- 清明（04-04/05 为周末）
    (date '2026-05-01'),  -- 劳动节
    (date '2026-05-04'),
    (date '2026-05-05'),
    (date '2026-06-19'),  -- 端午
    (date '2026-09-25'),  -- 中秋
    (date '2026-10-01'),  -- 国庆
    (date '2026-10-02'),
    (date '2026-10-05'),
    (date '2026-10-06'),
    (date '2026-10-07')
),
-- ═══════════════════════════════════════════════════════════════
-- 2 · 展开 2026-01-01 ~ 2026-12-31 并判定交易日
-- ═══════════════════════════════════════════════════════════════
series AS (
  SELECT gs::date AS d
  FROM generate_series(date '2026-01-01', date '2026-12-31', interval '1 day') AS gs
),
calc AS (
  SELECT s.d,
         (EXTRACT(ISODOW FROM s.d) < 6
          AND NOT EXISTS (SELECT 1 FROM holiday h WHERE h.d = s.d)) AS is_trading
  FROM series s
)
-- ═══════════════════════════════════════════════════════════════
-- 3 · 写入（文案 / 时段与运行时 sync_calendar 同口径）
-- ═══════════════════════════════════════════════════════════════
INSERT INTO fin_market_calendar
  (market, trade_date, is_trading, sessions, note, calendar_source, fetched_at)
SELECT 'CN_A',
       c.d,
       c.is_trading,
       CASE WHEN c.is_trading
            THEN (SELECT sessions FROM fin_market_rule WHERE market = 'CN_A')
            ELSE '[]'::jsonb END,
       CASE WHEN c.is_trading
            THEN 'akshare 交易日历'
            ELSE '非交易日（不在 akshare A 股交易日历中）' END,
       'akshare_official',
       now()
FROM calc c
ON CONFLICT (market, trade_date) DO UPDATE SET
  is_trading      = EXCLUDED.is_trading,
  sessions        = EXCLUDED.sessions,
  note            = EXCLUDED.note,
  calendar_source = EXCLUDED.calendar_source,
  fetched_at      = EXCLUDED.fetched_at;
