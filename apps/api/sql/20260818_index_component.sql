-- E-4 · 指数成分股表 · 保留历史(生存者偏差防治)
-- 2026-08-18 · Phase E-4

CREATE TABLE IF NOT EXISTS index_component (
  index_code     VARCHAR(10) NOT NULL,   -- '000300' 沪深 300 · '000905' 中证 500 · '000852' 中证 1000
  stock_code     VARCHAR(10) NOT NULL,   -- '600519'
  effective_from DATE        NOT NULL,   -- 生效日
  effective_to   DATE,                    -- NULL = 至今
  weight         DOUBLE PRECISION,        -- 成分权重 %(可选 · v2 追加)
  updated_at     TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (index_code, stock_code, effective_from)
);

CREATE INDEX IF NOT EXISTS idx_ic_current
  ON index_component(index_code)
  WHERE effective_to IS NULL;

CREATE INDEX IF NOT EXISTS idx_ic_stock
  ON index_component(stock_code);
