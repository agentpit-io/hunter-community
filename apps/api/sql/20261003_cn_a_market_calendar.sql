-- A 股交易日历补种（2026 全年）· 只补数据，不改结构
-- 2026-10-03 · 见 docs/开发文档/P4-演示站部署与按市场验收.md
--
-- 背景：演示站 `fin_market_calendar` 只有 HK(669 行) / US(730 行)，**CN_A 一行都没有**。
-- 后果：`markets.py:market_status` 对缺行一律判 `state='unknown'`（「未知 ≠ 交易日」），
-- 于是自动交易页对 A 股一直显示「日历缺失」，`fin-worker` 的 CN_A 时点也永远 `calendar_unknown` 空跑。
--
-- 口径（与运行时写入完全一致，便于下次 preopen 同步原地覆盖不产生差异）：
--   · 来源：akshare `tool_trade_date_hist_sina`（交易所日历，新浪转载），经
--     `GET /api/internal/calendar/trading-days?market=a` 取出 —— **不是手抄**；
--   · 交易日 = 周一~周五 且 不在下面 holiday 清单里；
--   · note 文案沿用 `fin-worker/app/activities.py:sync_calendar` 的两句常量；
--   · sessions 现取 `fin_market_rule`（CN_A: 09:30-11:30 / 13:00-15:00），不写死。
--
-- 为什么值得**另外**种全年（运行时 preopen 只滚动同步 ±5/+12 天）：
-- HK/US 已有全年行，CN_A 只留一个 18 天窗口的话，页面上 A 股在窗口外仍然显示「日历缺失」。
--
-- 幂等：`ON CONFLICT (market, trade_date) DO UPDATE`，重复执行结果不变。
-- 只 INSERT / UPDATE，不 DROP / ALTER。
--
-- 应用方式（服务器上，不触发 build）：
--   docker exec -i hunter-community-postgres-1 \
--     psql -U hunter -d hunter -v ON_ERROR_STOP=1 < apps/api/sql/20261003_cn_a_market_calendar.sql

BEGIN;

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

COMMIT;

-- ── 自检（应输出 365 / 242 / 123）─────────────────────────────
-- SELECT count(*) AS rows,
--        count(*) FILTER (WHERE is_trading) AS trading_days,
--        count(*) FILTER (WHERE NOT is_trading) AS non_trading_days
--   FROM fin_market_calendar WHERE market = 'CN_A';
