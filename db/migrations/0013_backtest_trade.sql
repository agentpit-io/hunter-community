-- 逐笔交易记录 · 阶段 4 · 供前端 "逐笔明细" 面板 + CSV 导出
-- apply(用户手动 · 别自动跑):
--   docker compose exec -T postgres psql -U hunter -d hunter < db/migrations/0013_backtest_trade.sql
CREATE TABLE IF NOT EXISTS backtest_trade (
    id BIGSERIAL PRIMARY KEY,
    result_id BIGINT NOT NULL REFERENCES backtest_result(id) ON DELETE CASCADE,
    trade_date DATE NOT NULL,
    code VARCHAR(20) NOT NULL,           -- 带后缀 · 如 600519.SH
    side VARCHAR(4) NOT NULL,            -- buy / sell
    shares INT NOT NULL,                 -- 股数(不是手)
    price DOUBLE PRECISION NOT NULL,
    turnover DOUBLE PRECISION NOT NULL,  -- shares * price(元)
    commission DOUBLE PRECISION DEFAULT 0,   -- 元
    stamp_tax DOUBLE PRECISION DEFAULT 0,
    slippage DOUBLE PRECISION DEFAULT 0,
    other DOUBLE PRECISION DEFAULT 0,
    total_cost DOUBLE PRECISION DEFAULT 0,   -- 4 项加总
    net_pnl DOUBLE PRECISION,                -- 卖出时算(可 null)
    slippage_model VARCHAR(16),              -- bp_static / sqrt_impact
    impact_bps_actual DOUBLE PRECISION,      -- sqrt_impact 实际算出 bps
    adv_20d DOUBLE PRECISION,                -- 当日 20 日均成交额(元)
    order_value_to_adv_ratio DOUBLE PRECISION  -- turnover / adv_20d
);
CREATE INDEX IF NOT EXISTS idx_bt_trade_result ON backtest_trade(result_id);
CREATE INDEX IF NOT EXISTS idx_bt_trade_code_date ON backtest_trade(code, trade_date);

-- backtest_result 加三列 · 持久化 trading_cost / gross_metrics(解决缓存命中丢失问题)
ALTER TABLE backtest_result
  ADD COLUMN IF NOT EXISTS trading_cost JSONB,
  ADD COLUMN IF NOT EXISTS gross_metrics JSONB,
  ADD COLUMN IF NOT EXISTS slippage_model VARCHAR(16);
