-- 量化策略系统 · 4 表 migration
-- (2026-08-17 · Phase A · 见 doc/开源hunter-community/参考/11量化策略/quant-strategy-tech-plan.md §3)

-- ═══════════════════════════════════════════════════════════════
-- 表 1 · factor_value · 每日截面因子值
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS factor_value (
  trade_date   DATE                     NOT NULL,
  factor_key   VARCHAR(32)              NOT NULL,
  code         VARCHAR(10)              NOT NULL,
  market       VARCHAR(4)               NOT NULL DEFAULT 'A',
  raw_value    DOUBLE PRECISION,
  z_score      DOUBLE PRECISION,
  pct_rank     REAL,
  updated_at   TIMESTAMPTZ              NOT NULL DEFAULT NOW(),
  PRIMARY KEY (trade_date, factor_key, code)
);
CREATE INDEX IF NOT EXISTS idx_fv_factor_date ON factor_value(factor_key, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_fv_code_date   ON factor_value(code, trade_date DESC);

-- ═══════════════════════════════════════════════════════════════
-- 表 2 · factor_ic · 因子 IC 时间序列(供"因子广场"IC 排行)
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS factor_ic (
  factor_key   VARCHAR(32)              NOT NULL,
  trade_date   DATE                     NOT NULL,
  universe     VARCHAR(16)              NOT NULL DEFAULT 'hs300',
  horizon_days INT                      NOT NULL DEFAULT 5,
  ic           DOUBLE PRECISION,
  ic_ir        DOUBLE PRECISION,
  updated_at   TIMESTAMPTZ              NOT NULL DEFAULT NOW(),
  PRIMARY KEY (factor_key, trade_date, universe, horizon_days)
);

-- ═══════════════════════════════════════════════════════════════
-- 表 3 · strategy · 用户与官方策略
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS strategy (
  id           BIGSERIAL                PRIMARY KEY,
  user_id      TEXT,                    -- NULL = 官方
  name         VARCHAR(64)              NOT NULL,
  description  TEXT,
  factors      JSONB                    NOT NULL,  -- [{key, weight_pct}]
  config       JSONB                    NOT NULL,  -- {universe, top_n, rebalance, cost_bps, benchmark}
  is_official  BOOLEAN                  NOT NULL DEFAULT FALSE,
  is_public    BOOLEAN                  NOT NULL DEFAULT FALSE,
  fork_from    BIGINT                   REFERENCES strategy(id) ON DELETE SET NULL,
  created_at   TIMESTAMPTZ              NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ              NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_strategy_user    ON strategy(user_id) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_strategy_public  ON strategy(is_public) WHERE is_public = TRUE;
CREATE INDEX IF NOT EXISTS idx_strategy_official ON strategy(is_official) WHERE is_official = TRUE;

-- ═══════════════════════════════════════════════════════════════
-- 表 4 · backtest_result · 回测缓存(spec_hash 去重)
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS backtest_result (
  id           BIGSERIAL                PRIMARY KEY,
  strategy_id  BIGINT                   REFERENCES strategy(id) ON DELETE SET NULL,
  spec_hash    VARCHAR(64)              NOT NULL,
  start_date   DATE                     NOT NULL,
  end_date     DATE                     NOT NULL,
  metrics      JSONB                    NOT NULL,
  nav_series   JSONB                    NOT NULL,
  quantile_ret JSONB,
  positions    JSONB,
  cost_used    DOUBLE PRECISION,
  duration_ms  INT,
  created_at   TIMESTAMPTZ              NOT NULL DEFAULT NOW(),
  CONSTRAINT uniq_spec_hash UNIQUE (spec_hash)
);
CREATE INDEX IF NOT EXISTS idx_bt_strategy ON backtest_result(strategy_id, created_at DESC);
