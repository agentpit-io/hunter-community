-- 修 daily_close.amount · klines.volume 单位是手(100 股)· 成交额需 × 100
CREATE OR REPLACE VIEW daily_close AS
SELECT
    code || CASE WHEN code LIKE '6%' THEN '.SH' ELSE '.SZ' END AS symbol,
    ts AS trade_date,
    open, high, low, close, volume,
    close * volume * 100 AS amount  -- A股 volume 单位是手 · 成交额 = 均价 × 手数 × 100
FROM klines
WHERE period='daily';
