-- daily_close v2 · 阶段 4 · 加 adv_20d(20 日均成交额)· 供 sqrt_impact 冲击模型用
-- klines.volume 单位是手(100 股)· 成交额 = 收盘价 × 手数 × 100
-- 注意:window 每次全表算 · 若性能问题 · 后续改物化 view
-- apply(用户手动 · 别自动跑):
--   docker compose exec -T postgres psql -U hunter -d hunter < db/migrations/0014_daily_close_v2.sql
CREATE OR REPLACE VIEW daily_close AS
SELECT
    code || CASE WHEN code LIKE '6%' THEN '.SH' ELSE '.SZ' END AS symbol,
    ts AS trade_date,
    open, high, low, close, volume,
    close * volume * 100 AS amount,
    -- 20 日均成交额 · 只对每个 code 内部按时间滚动
    AVG(close * volume * 100) OVER (
        PARTITION BY code
        ORDER BY ts
        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    ) AS adv_20d
FROM klines
WHERE period='daily';
