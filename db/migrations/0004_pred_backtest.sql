-- Hunter Community · 预测回测三张表(本地版)
-- 让 hunter-community 不再依赖 finance-data 库就能跑 backtest 看板
-- 字段与 apps/api/app/services/backtest/store.py 保持一致
-- Idempotent: 只 CREATE TABLE IF NOT EXISTS · 严禁改动任何现有表 · 严禁 DROP

-- ① 预测快照:每次预测完整留档(不覆盖),含8因子分值
CREATE TABLE IF NOT EXISTS pred_snapshot (
  symbol      TEXT NOT NULL,
  run_date    DATE NOT NULL,          -- 预测发起日
  pred_date   DATE NOT NULL,          -- 被预测的目标交易日
  horizon     SMALLINT NOT NULL,      -- 第几个交易日 1..5
  base_date   DATE NOT NULL,          -- 基准日(通常 = run_date · 冲突键)
  last_close  NUMERIC(12,4),
  pred_close  NUMERIC(12,4),
  change_pct  NUMERIC(8,4),
  direction   TEXT,                   -- up/down/flat
  score       NUMERIC(8,4),
  signal      TEXT,                   -- 强多/偏多/中性/偏空/强空
  confidence  NUMERIC(5,2),
  factors     JSONB,
  model_ver   TEXT DEFAULT 'pro-v1',
  clipped     BOOLEAN DEFAULT FALSE,
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (symbol, base_date, pred_date)
);
CREATE INDEX IF NOT EXISTS idx_pred_snap_target ON pred_snapshot(symbol, pred_date, run_date DESC);
CREATE INDEX IF NOT EXISTS idx_pred_snap_run    ON pred_snapshot(run_date);

-- ② 事后准确性回测:预测 vs 真实收盘
CREATE TABLE IF NOT EXISTS pred_backtest (
  symbol       TEXT NOT NULL,
  run_date     DATE NOT NULL,
  pred_date    DATE NOT NULL,
  horizon      SMALLINT,
  base_date    DATE NOT NULL,
  pred_change  NUMERIC(8,4),
  real_change  NUMERIC(8,4),
  abs_error    NUMERIC(8,4),
  rel_error    NUMERIC(8,4),
  dir_hit      BOOLEAN,
  amt_hit      BOOLEAN,
  signal       TEXT,
  factors      JSONB,
  model_ver    TEXT,
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (symbol, base_date, pred_date)
);
CREATE INDEX IF NOT EXISTS idx_pred_bt_date ON pred_backtest(pred_date);
CREATE INDEX IF NOT EXISTS idx_pred_bt_sym  ON pred_backtest(symbol, pred_date DESC);

-- ③ 重叠一致性:相邻两次预测对同一目标日的对比 + 因子级归因
CREATE TABLE IF NOT EXISTS pred_consistency (
  symbol       TEXT NOT NULL,
  pred_date    DATE NOT NULL,
  prev_run     DATE NOT NULL,
  curr_run     DATE NOT NULL,
  prev_base    DATE NOT NULL,
  curr_base    DATE NOT NULL,
  prev_change  NUMERIC(8,4),
  curr_change  NUMERIC(8,4),
  delta        NUMERIC(8,4),
  verdict      TEXT,                  -- consistent/strengthen/weaken/reversal
  factor_delta JSONB,
  top_driver   TEXT,
  driver_share NUMERIC(6,2),
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (symbol, pred_date, curr_base)
);
CREATE INDEX IF NOT EXISTS idx_pred_cons_run     ON pred_consistency(curr_run, verdict);
CREATE INDEX IF NOT EXISTS idx_pred_cons_sym     ON pred_consistency(symbol, pred_date DESC);
