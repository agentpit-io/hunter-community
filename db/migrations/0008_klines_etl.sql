-- klines Daily ETL · 阶段 1(P0 阻塞)
-- 2026-08-31 · 见 doc/开源hunter-community/04开源比赛/
--                2026-08-31_真生产化技术方案与开发计划.md §2.1
--
-- 为什么这是 P0:生产 klines 表 0 行 → backtest_engine 拿不到交易日
-- (SELECT DISTINCT ts FROM klines 返回空)→ 回测直接报 no_dates。
-- **交易成本模块做得再对,没有 K 线也跑不了一次。**
--
-- 幂等 · 只加列建表 · 不动任何已有数据

-- ── klines 补三个字段 ────────────────────────────────────
ALTER TABLE klines
  ADD COLUMN IF NOT EXISTS source      VARCHAR(16),
  ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS adj_close   DOUBLE PRECISION;

COMMENT ON COLUMN klines.source      IS '数据来源 tencent/sina/akshare —— 出问题时能定位是哪个源的锅';
COMMENT ON COLUMN klines.ingested_at IS '拉取时间 · 用于判断数据新鲜度';
COMMENT ON COLUMN klines.adj_close   IS '前复权收盘价 · 回测用这个而不是 close';

CREATE INDEX IF NOT EXISTS idx_klines_ingested ON klines(ingested_at);
CREATE INDEX IF NOT EXISTS idx_klines_code_ts  ON klines(code, period, ts DESC);

-- ── ETL 运行日志 ─────────────────────────────────────────
-- **有这张表才能回答"昨天的数据为什么少了"**。
-- 没有它的话,ETL 静默失败和"那天本来就没交易"长得一模一样。
CREATE TABLE IF NOT EXISTS klines_etl_log (
    id            SERIAL PRIMARY KEY,
    run_at        TIMESTAMPTZ DEFAULT NOW(),
    run_date      DATE NOT NULL,
    market        VARCHAR(8),
    codes_total   INT,
    codes_success INT,
    codes_failed  INT,
    source_used   VARCHAR(16),
    fallback_count INT DEFAULT 0,
    duration_ms   INT,
    error_sample  JSONB
);
CREATE INDEX IF NOT EXISTS idx_etl_log_run_date ON klines_etl_log(run_date DESC);

-- ── 股票池 ───────────────────────────────────────────────
-- priority: 1 = 核心必跑(演示/评委会看的)· 100 = 普通
-- 分优先级的理由:全 A 股 5500 只跑一轮要小时级,
-- 而演示只需要那几十只。核心的先跑完,普通的慢慢补。
CREATE TABLE IF NOT EXISTS stock_universe (
    code     VARCHAR(20) PRIMARY KEY,
    market   VARCHAR(8) NOT NULL,
    name     VARCHAR(64),
    enabled  BOOLEAN DEFAULT TRUE,
    added_at TIMESTAMPTZ DEFAULT NOW(),
    priority INT DEFAULT 100
);
CREATE INDEX IF NOT EXISTS idx_universe_pri ON stock_universe(priority, market) WHERE enabled;
