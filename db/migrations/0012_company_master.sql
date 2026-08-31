-- 公司基础信息 · 供 ST 判定 / 行业 / 主板等过滤
--
-- filters.py(skip_st 开启时)执行:
--   SELECT stock_code FROM company_master
--    WHERE stock_code = ANY($1) AND (name LIKE '%ST%' OR name LIKE '%退%')
-- 其中 $1 是去后缀的 6 位代码([c.split(".")[0] for c in syms]),
-- 故 stock_code 存**裸 6 位**(不带 .SH/.SZ 后缀)。
CREATE TABLE IF NOT EXISTS company_master (
    stock_code   VARCHAR(20) PRIMARY KEY,   -- 裸 6 位,如 600519
    name         TEXT,
    industry     TEXT,
    market       VARCHAR(8) DEFAULT 'cn',    -- cn / hk / us
    board        VARCHAR(16),                -- 沪主板/深主板/创业板/科创板/北交所
    listed_date  DATE,
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_company_master_market ON company_master(market);
