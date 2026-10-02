-- ════════════════════════════════════════════════════════════════════════
-- 0029_market_scope.sql · 智能炒股二期 · 打开市场维度（`CN_A` / `HK` / `US`）
--
-- 依据：`plan/ref/11-美股港股模拟交易需求分析与技术实施方案.md` §4.1（迁移 `0029`）
--       + `plan/拍板-2026-10-03-港美股必须可交易.md`（港美股必须能模拟交易）
--       + `docs/开发文档/N0-数据源与日历可用性验证报告.md`（来源均已实测可用）
--
-- **只做加法，不删不改历史数据。** 三条都照本仓迁移规范：
--   · 一律 `ADD COLUMN IF NOT EXISTS` / `DROP CONSTRAINT IF EXISTS` + `ADD CONSTRAINT`（都不报错）；
--   · 新增列一律先建（可空）→ 回填现状 → `SET DEFAULT` + `SET NOT NULL`，重复执行是空操作；
--   · 只放松约束（CHECK 加值），**收紧的只有「新列 NOT NULL」这一处**，且回填后必然通过。
--
-- 为什么约束要 `DROP` 再 `ADD`：`fin_project.currency` / `market_scope`、
-- `fin_instrument.exchange` / `board` 的 CHECK 是**内联**建的（0023），
-- 名字由 Postgres 自动生成（已核实：`fin_project_currency_check` 等），
-- 改约束只能按名 DROP 再 ADD；`DROP ... IF EXISTS` 让这条可重复执行。
--
-- 现状（改动前实测 · 2026-10-03）：
--   fin_project 424 行全部 currency='CNY' / market_scope='CN_A'
--   fin_account   3 行全部 currency='CNY' / market_scope='CN_A'
--   fin_instrument 15 行全部 A 股（SH/SZ + main/chinext/star）
--   fin_market_calendar 435 行（A 股 akshare 日历，PK 只有 trade_date）
--   fin_fee_model 1 行 'fee-cn-a-v1'
--   fin_snapshot 375 行、账本五表 260/437/1182/221/30 行 —— **全部 A 股**
--   → 所以回填 `'CN_A'` / `'CNY'` 是**事实陈述，不是猜**（红线 §六.1）。
-- ════════════════════════════════════════════════════════════════════════


-- ── fin_project：币种与市场范围放开 ──────────────────────────────────────
ALTER TABLE fin_project DROP CONSTRAINT IF EXISTS fin_project_currency_check;
ALTER TABLE fin_project ADD  CONSTRAINT fin_project_currency_check
  CHECK (currency IN ('CNY','HKD','USD'));

ALTER TABLE fin_project DROP CONSTRAINT IF EXISTS fin_project_market_scope_check;
ALTER TABLE fin_project ADD  CONSTRAINT fin_project_market_scope_check
  CHECK (market_scope IN ('CN_A','HK','US','MULTI'));


-- ── fin_account：「项目 : 市场子账户」一挂多（新增 market 列）─────────────
-- 现状 fin_account 的 currency / market_scope 没有 CHECK（0023 只给了默认值），
-- 这里补上与 fin_project 同口径的约束 —— 现有 3 行都是 CNY / CN_A，必然通过。
-- ⚠️ `fin_account_one_per_user` 唯一索引保持不动：真正放开「一个用户多个市场
--    子账户」属于 N4（调度与账本）的开户流程改造，本期只加列、不动索引。
ALTER TABLE fin_account ADD COLUMN IF NOT EXISTS market TEXT;
UPDATE fin_account SET market = 'CN_A' WHERE market IS NULL;   -- 历史行 = A 股（事实）
ALTER TABLE fin_account ALTER COLUMN market SET DEFAULT 'CN_A';
ALTER TABLE fin_account ALTER COLUMN market SET NOT NULL;
ALTER TABLE fin_account DROP CONSTRAINT IF EXISTS fin_account_market_check;
ALTER TABLE fin_account ADD  CONSTRAINT fin_account_market_check
  CHECK (market IN ('CN_A','HK','US'));

ALTER TABLE fin_account DROP CONSTRAINT IF EXISTS fin_account_currency_check;
ALTER TABLE fin_account ADD  CONSTRAINT fin_account_currency_check
  CHECK (currency IN ('CNY','HKD','USD'));

ALTER TABLE fin_account DROP CONSTRAINT IF EXISTS fin_account_market_scope_check;
ALTER TABLE fin_account ADD  CONSTRAINT fin_account_market_scope_check
  CHECK (market_scope IN ('CN_A','HK','US','MULTI'));


-- ── fin_instrument：交易所 / 板块放开到港美股；新增 market、currency ──────
-- exchange：保留 A 股三所，加港交所（HKEX）与美股三所 + CBOE
--   （美股交易所代号沿用仓内既有口径 `screen_rs.US_EXCHANGES` /
--    `rs_history._US_SUFFIX`：NASDAQ / NYSE / AMEX / CBOE）。
-- board：保留 A 股四板块（main/chinext/star/bse），加港股（hk_main 主板 /
--   hk_gem GEM）与美股（us_main 交易所上市 / us_other 其余）。港股 GEM 是港交所
--   公开的第二个板；美股没有与 A 股对应的分层，"us_other" 是给非主板留的分档。
ALTER TABLE fin_instrument DROP CONSTRAINT IF EXISTS fin_instrument_exchange_check;
ALTER TABLE fin_instrument ADD  CONSTRAINT fin_instrument_exchange_check
  CHECK (exchange IN ('SH','SZ','BJ','HKEX','NASDAQ','NYSE','AMEX','CBOE'));

ALTER TABLE fin_instrument DROP CONSTRAINT IF EXISTS fin_instrument_board_check;
ALTER TABLE fin_instrument ADD  CONSTRAINT fin_instrument_board_check
  CHECK (board IN ('main','chinext','star','bse','hk_main','hk_gem','us_main','us_other'));

ALTER TABLE fin_instrument ADD COLUMN IF NOT EXISTS market TEXT;
ALTER TABLE fin_instrument ADD COLUMN IF NOT EXISTS currency TEXT;
UPDATE fin_instrument SET market = 'CN_A' WHERE market IS NULL;   -- 历史行 = A 股（事实）
UPDATE fin_instrument SET currency = 'CNY' WHERE currency IS NULL;
ALTER TABLE fin_instrument ALTER COLUMN market SET DEFAULT 'CN_A';
ALTER TABLE fin_instrument ALTER COLUMN market SET NOT NULL;
ALTER TABLE fin_instrument ALTER COLUMN currency SET DEFAULT 'CNY';
ALTER TABLE fin_instrument ALTER COLUMN currency SET NOT NULL;
ALTER TABLE fin_instrument DROP CONSTRAINT IF EXISTS fin_instrument_market_check;
ALTER TABLE fin_instrument ADD  CONSTRAINT fin_instrument_market_check
  CHECK (market IN ('CN_A','HK','US'));
ALTER TABLE fin_instrument DROP CONSTRAINT IF EXISTS fin_instrument_currency_check;
ALTER TABLE fin_instrument ADD  CONSTRAINT fin_instrument_currency_check
  CHECK (currency IN ('CNY','HKD','USD'));

-- 涨跌停幅度放开为**可空**：港股 / 美股**没有每日涨跌幅限制**（N0 报告 S6 口径、
-- 拍板 §四 `price_limit_mode='none'`），存 NULL 表示「无此值」——
-- 这就是「空的比假的好」：填 0 会被读成「涨跌停 0%」，填 0.10 是编一个不存在的限制。
-- ⚠️ A 股行原样保留（15 行都有值），语义一字不变；是否读这一列由
--    `fin_market_rule.price_limit_mode` 决定（N2 实现）。
ALTER TABLE fin_instrument ALTER COLUMN limit_up_pct   DROP NOT NULL;
ALTER TABLE fin_instrument ALTER COLUMN limit_down_pct DROP NOT NULL;


-- ── fin_market_calendar：加市场维度；主键改 (market, trade_date) ─────────
ALTER TABLE fin_market_calendar ADD COLUMN IF NOT EXISTS market TEXT;
ALTER TABLE fin_market_calendar ADD COLUMN IF NOT EXISTS calendar_source TEXT;
ALTER TABLE fin_market_calendar ADD COLUMN IF NOT EXISTS fetched_at TIMESTAMPTZ;

UPDATE fin_market_calendar SET market = 'CN_A' WHERE market IS NULL;   -- 历史行 = A 股（事实）
UPDATE fin_market_calendar SET calendar_source = 'akshare' WHERE calendar_source IS NULL;
-- calendar_source 的取值（不建 CHECK，避免把 N2 的种子来源写死在枚举里）：
--   'akshare'（A 股，现有同步通道）/ 'hkex_official' / 'nyse_official'（N0 报告
--   使用官方页面 + 对账，见拍板 §3.1）/ 'manual_seed'（拿不到官方来源时人工录入）
-- fetched_at 留空 = 「这一行的抓取时刻未知」——新种子由写入方填，不回填一个假时刻。

ALTER TABLE fin_market_calendar ALTER COLUMN market SET DEFAULT 'CN_A';
ALTER TABLE fin_market_calendar ALTER COLUMN market SET NOT NULL;
ALTER TABLE fin_market_calendar DROP CONSTRAINT IF EXISTS fin_market_calendar_market_check;
ALTER TABLE fin_market_calendar ADD  CONSTRAINT fin_market_calendar_market_check
  CHECK (market IN ('CN_A','HK','US'));

-- 主键 trade_date → (market, trade_date)：三个市场各自的日历要能共存
-- （2026-10-02 这天 A 股与港股都有记录，单列主键放不下）。
-- DO 块判当前主键是不是**单列**；是才换，换过之后重复执行是空操作。
-- ⚠️ 依赖 0029 的代码已同步（`fin_market_calendar` 的 `ON CONFLICT` 目标必须
--    一起改成 `(market, trade_date)`，见 `apps/paper/app/routers/reference.py`
--    与本仓 `apps/paper/tests/helpers.py`）—— 否则 A 股日历同步会在运行时报
--    「no unique or exclusion constraint matching the ON CONFLICT specification」。
DO $$
DECLARE
  pk_cols int;
BEGIN
  SELECT array_length(conkey, 1) INTO pk_cols
    FROM pg_constraint
   WHERE conrelid = 'fin_market_calendar'::regclass AND contype = 'p';
  IF pk_cols = 1 THEN
    ALTER TABLE fin_market_calendar DROP CONSTRAINT fin_market_calendar_pkey;
    ALTER TABLE fin_market_calendar
      ADD CONSTRAINT fin_market_calendar_pkey PRIMARY KEY (market, trade_date);
  END IF;
END $$;


-- ── fin_snapshot：加 market（快照归属哪个市场）──────────────────────────
ALTER TABLE fin_snapshot ADD COLUMN IF NOT EXISTS market TEXT;
UPDATE fin_snapshot SET market = 'CN_A' WHERE market IS NULL;   -- 历史行 = A 股（事实）
ALTER TABLE fin_snapshot ALTER COLUMN market SET DEFAULT 'CN_A';
ALTER TABLE fin_snapshot ALTER COLUMN market SET NOT NULL;
ALTER TABLE fin_snapshot DROP CONSTRAINT IF EXISTS fin_snapshot_market_check;
ALTER TABLE fin_snapshot ADD  CONSTRAINT fin_snapshot_market_check
  CHECK (market IN ('CN_A','HK','US'));


-- ── fin_fee_model：加市场 / 币种（费率按市场各一行）──────────────────────
-- 现有唯一一行 'fee-cn-a-v1' 是 A 股人民币费率 → 回填 CN_A / CNY（事实）。
-- 港美股费率本期**不落数**（`fin_market_rule.fee_model_version` 留空，
-- source 写 unavailable）—— 费率随年份变，必须按生效日取自公布口径，不许先编一个。
ALTER TABLE fin_fee_model ADD COLUMN IF NOT EXISTS market TEXT;
ALTER TABLE fin_fee_model ADD COLUMN IF NOT EXISTS currency TEXT;
UPDATE fin_fee_model SET market = 'CN_A' WHERE market IS NULL;
UPDATE fin_fee_model SET currency = 'CNY' WHERE currency IS NULL;
ALTER TABLE fin_fee_model ALTER COLUMN market SET DEFAULT 'CN_A';
ALTER TABLE fin_fee_model ALTER COLUMN market SET NOT NULL;
ALTER TABLE fin_fee_model ALTER COLUMN currency SET DEFAULT 'CNY';
ALTER TABLE fin_fee_model ALTER COLUMN currency SET NOT NULL;
ALTER TABLE fin_fee_model DROP CONSTRAINT IF EXISTS fin_fee_model_market_check;
ALTER TABLE fin_fee_model ADD  CONSTRAINT fin_fee_model_market_check
  CHECK (market IN ('CN_A','HK','US'));
ALTER TABLE fin_fee_model DROP CONSTRAINT IF EXISTS fin_fee_model_currency_check;
ALTER TABLE fin_fee_model ADD  CONSTRAINT fin_fee_model_currency_check
  CHECK (currency IN ('CNY','HKD','USD'));
