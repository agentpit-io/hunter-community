-- 0040 · 幂等写入港股 / 美股 2026 全年交易日历（各 365 行 · 港股 247 个交易日 / 美股 251 个）
--
-- 为什么需要它
-- ------------
-- `0039` 补上了 A 股，并如实标注「港美股仍是缺口」—— 本文件把那个缺口补掉。
-- 在此之前，从迁移建起来的库里 `fin_market_calendar` 的 HK / US 两行一段都没有，
-- `markets.py:market_status` 对缺行判 `state='unknown'`（「未知 ≠ 交易日」）——
-- 「只选港股」这类单市场项目在演示站/新部署上会一直读不到港股日历。
--
-- 假期清单来源（三方核对一致，不是凭记忆写的）
-- ----------------------------------------------
--   · 主来源：交易所**官方页**结构化数据 ——
--     港股 HKEX `https://www.hkex.com.hk/News/HKEX-Calendar?sc_lang=en`
--     （页内 `var DataSource = '{"monthly":[...]}'`，取 `holidayIcon == "HongKongPublicHolidays"`）；
--     美股 NYSE `https://www.nyse.com/markets/hours-calendars`
--     （已 302 到 `/trade/hours-calendars`，页内 `Holiday | 2026 | 2027 | 2028` 三列表）。
--   · 取数代码：`apps/api/app/services/fin/market_calendar.py` 的纯解析函数
--     `parse_hkex_calendar` / `parse_nyse_calendar`（**下面这份清单就是拿这两个函数
--     跑上面两张官方页得到的输出**，2026-10-03 抓取）。
--   · 交叉核对：与同模块 `MANUAL_SEED`（人工兜底种子）**差集为空**；
--     与演示站 `fin_market_calendar` 现网数据**逐条一致**。
--
-- 口径（与 `scripts/seed_hk_us_calendar.py` 写入结果**逐字节一致**）
-- ----------------------------------------------------------------
--   · 交易日 = 周一~周五 且 不在下面的法定休市清单里；
--   · 休市日 `note` = `<来源标签> · <官方假名>`；交易日 `note` = `<来源标签> · 交易日`；
--     其余（本身落在周末且非假日）= `周末`。**注意**：落在周六的假日写假名而非「周末」，
--     这是种子脚本的既有行为，照抄以免产生无意义 diff；
--   · `calendar_source`：港股 `hkex_official`、美股 `nyse_official`（**不是** `manual_seed`
--     —— 这两份清单来自官方页解析成功的那条路径）；
--   · `sessions` 现取 `fin_market_rule`（港股 09:30-12:00 / 13:00-16:00 / **16:00-16:10 收市竞价**，
--     美股 09:30-16:00），不写死。依赖关系安全：`0030_market_rule.sql` 先于本文件执行并种下
--     HK / US 两行；万一它不在，本迁移会**当场报错让 api 起不来**（`sessions` 是 NOT NULL），
--     而不是静默写入空时段 —— 这是故意的，见 `app/migrate.py` 文件头。
--
-- 半日市（不是休市，按正常交易时段处理 —— 拍板 §3.1 第 4 条）
-- ------------------------------------------------------------
--   港股 2026 年半日市：`2026-02-16`（农历年除夕）、`2026-12-24`（平安夜）、
--   `2026-12-31`（除夕）。三家**当日上午照常交易**，故本文件把它们算作交易日、
--   写完整 `sessions`；`market_calendar.parse_hkex_calendar` 也把半日市单独返回、
--   **不**混进休市集合，与本文件口径一致。（港股收市竞价 `16:00-16:10` 那一段
--   `fin_market_rule` 已含，见 `0030` / `0034` 与 v1.5.1 的修复。）
--
-- 幂等：`ON CONFLICT (market, trade_date) DO UPDATE`，重复执行结果不变。
-- 只 INSERT / UPDATE，不 DROP / ALTER。
--
-- ⚠️ 本文件**不带** `BEGIN;` / `COMMIT;` —— `app/migrate.py:apply_one` 已经把每个迁移文件
--    包在**独立事务**里执行（失败整体回滚 + 记账），文件内再写事务边界会破坏那个承诺。
--    `db/migrations/` 下现存文件无一自带事务边界，保持一致。
--
-- 谁才是权威（别把本文件当唯一真值）
-- ----------------------------------
--   运行时（`fin-worker` 的 `preopen` 时点 → `sync_calendar` → api 的
--   `GET /internal/calendar/trading-days?market=hk|us`）**不读本表**，而是**现抓官方页**
--   （`internal_etl.py:112` 调 `mcal.market_holidays`）后写回。本文件的作用是让
--   **从零建的库 / 官方页抓不到时** 也有正确日历可读，而不是替代那条链路。
--
-- 未覆盖（如实标注）：只做 **2026 全年**。2027 年及以后仍由运行时同步或后续迁移补
--   （同一次抓取其实已覆盖港股到 2027-10-31、美股到 2028-12-31，需要时另开迁移即可）。

-- ═══════════════════════════════════════════════════════════════
-- 1 · 2026 年法定休市日（港股 17 天 / 美股 10 天，含落在周六的 3 天港股假期）
-- ═══════════════════════════════════════════════════════════════
WITH holiday(market, d, name) AS (
  VALUES
    -- ── 港股 · HKEX 官方页（17 天；02-16 / 12-24 / 12-31 是**半日市**，不在休市清单里）──
    ('HK', date '2026-01-01', 'The first day of January'),
    ('HK', date '2026-02-17', 'Lunar New Year’s Day'),
    ('HK', date '2026-02-18', 'The second day of Lunar New Year'),
    ('HK', date '2026-02-19', 'The third day of Lunar New Year'),
    ('HK', date '2026-04-03', 'Good Friday'),
    ('HK', date '2026-04-04', 'The day following Good Friday'),              -- 周六
    ('HK', date '2026-04-06', 'The day following Ching Ming Festival'),
    ('HK', date '2026-04-07', 'The day following Easter Monday'),
    ('HK', date '2026-05-01', 'Labour Day'),
    -- 官方页原文此处是**双空格**（"the  Birthday"），照抄以免与官方页解析结果产生无意义 diff
    ('HK', date '2026-05-25', 'The day following the  Birthday of the Buddha'),
    ('HK', date '2026-06-19', 'Tuen Ng Festival'),
    ('HK', date '2026-07-01', 'Hong Kong Special Administrative Region Establishment Day'),
    ('HK', date '2026-09-26', 'The day following the Chinese Mid-Autumn Festival'),  -- 周六
    ('HK', date '2026-10-01', 'National Day'),
    ('HK', date '2026-10-19', 'The day following Chung Yeung Festival'),
    ('HK', date '2026-12-25', 'Christmas Day'),
    ('HK', date '2026-12-26', 'The first weekday after Christmas Day'),      -- 周六
    -- ── 美股 · NYSE 官方页（10 天，全部落在工作日；07-04 是周六，官方给的是 07-03 补休）──
    ('US', date '2026-01-01', 'New Year’s Day'),
    ('US', date '2026-01-19', 'Martin Luther King, Jr. Day'),
    ('US', date '2026-02-16', 'Washington''s Birthday'),
    ('US', date '2026-04-03', 'Good Friday'),
    ('US', date '2026-05-25', 'Memorial Day'),
    ('US', date '2026-06-19', 'Juneteenth National Independence Day'),
    ('US', date '2026-07-03', 'Independence Day'),
    ('US', date '2026-09-07', 'Labor Day'),
    ('US', date '2026-11-26', 'Thanksgiving Day'),
    ('US', date '2026-12-25', 'Christmas Day')
),
-- ═══════════════════════════════════════════════════════════════
-- 2 · 展开两个市场 × 2026-01-01 ~ 2026-12-31 并判定交易日
-- ═══════════════════════════════════════════════════════════════
series AS (
  SELECT gs::date AS d
  FROM generate_series(date '2026-01-01', date '2026-12-31', interval '1 day') AS gs
),
grid AS (
  SELECT mk.market, s.d
  FROM series s
  CROSS JOIN (VALUES ('HK'), ('US')) AS mk(market)
),
calc AS (
  SELECT g.market,
         g.d,
         (EXTRACT(ISODOW FROM g.d) < 6
          AND NOT EXISTS (SELECT 1 FROM holiday h
                          WHERE h.market = g.market AND h.d = g.d)) AS is_trading,
         (SELECT h.name FROM holiday h
          WHERE h.market = g.market AND h.d = g.d) AS hname
  FROM grid g
)
-- ═══════════════════════════════════════════════════════════════
-- 3 · 写入（文案 / 时段与 seed_hk_us_calendar.py 同口径）
-- ═══════════════════════════════════════════════════════════════
INSERT INTO fin_market_calendar
  (market, trade_date, is_trading, sessions, note, calendar_source, fetched_at)
SELECT c.market,
       c.d,
       c.is_trading,
       CASE WHEN c.is_trading
            THEN (SELECT sessions FROM fin_market_rule WHERE market = c.market)
            ELSE '[]'::jsonb END,
       CASE
         -- 休市且有官方假名（含落在周六的）→ 「<标签> · <假名>」
         WHEN NOT c.is_trading AND c.hname IS NOT NULL
           THEN (CASE c.market WHEN 'HK' THEN 'hkex_official'
                               ELSE 'nyse_official' END) || ' · ' || c.hname
         WHEN c.is_trading
           THEN (CASE c.market WHEN 'HK' THEN 'hkex_official'
                               ELSE 'nyse_official' END) || ' · 交易日'
         ELSE '周末'
       END,
       CASE c.market WHEN 'HK' THEN 'hkex_official' ELSE 'nyse_official' END,
       now()
FROM calc c
ON CONFLICT (market, trade_date) DO UPDATE SET
  is_trading      = EXCLUDED.is_trading,
  sessions        = EXCLUDED.sessions,
  note            = EXCLUDED.note,
  calendar_source = EXCLUDED.calendar_source,
  fetched_at      = EXCLUDED.fetched_at;
