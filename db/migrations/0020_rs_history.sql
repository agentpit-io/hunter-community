-- 全市场日线(RS 线上涨天数 + 精确 RS 评级的数据来源)
--
-- ⚠️ 已有部署不会执行这个文件:db/migrations 挂在 postgres 的
-- /docker-entrypoint-initdb.d,只在数据卷第一次初始化时跑。
-- 真正生效的是 apps/api/app/services/quant/rs_history.py 里随代码走的 _DDL,
-- 管线第一次运行时 _ensure_tables() 建表。本文件给全新安装用 + 留档,两处必须一致。
--
-- 为什么不复用 klines:klines 是站内因子引擎(factor_engine)的数据源,
-- 口径、复权、单位(科创板 volume 是股、其他是手)都有既有约定;
-- 这里只要一列前复权收盘价,而且每晚整窗覆盖重写,混进去会互相污染。
CREATE TABLE IF NOT EXISTS rs_daily (
    market      TEXT             NOT NULL,
    code        TEXT             NOT NULL,
    trade_date  DATE             NOT NULL,
    close       DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (market, code, trade_date)
);
CREATE TABLE IF NOT EXISTS rs_line_stat (
    market           TEXT    NOT NULL,
    code             TEXT    NOT NULL,
    as_of            DATE    NOT NULL,
    n_days           INT     NOT NULL,
    rs_line          DOUBLE PRECISION,
    rs_ma21          DOUBLE PRECISION,
    up_days          INT,
    up_days_censored BOOLEAN NOT NULL DEFAULT FALSE,
    rs_raw_exact     DOUBLE PRECISION,
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (market, code)
);
-- 2026-09-11 · VCP 字段(口径见 apps/api/app/services/quant/vcp.py)。
-- 腾讯每根 K 线本来就带 [日期, 开, 收, 高, 低, 量],原来只存收盘 —— 同一次响应多存三列,零新增请求。
ALTER TABLE rs_daily ADD COLUMN IF NOT EXISTS high   DOUBLE PRECISION;
ALTER TABLE rs_daily ADD COLUMN IF NOT EXISTS low    DOUBLE PRECISION;
ALTER TABLE rs_daily ADD COLUMN IF NOT EXISTS volume DOUBLE PRECISION;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS vcp_contractions   INT;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS vcp_depths         TEXT;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS vcp_first_depth    DOUBLE PRECISION;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS vcp_last_depth     DOUBLE PRECISION;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS vcp_vol_declining  SMALLINT;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS vcp_last_vol_ratio DOUBLE PRECISION;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS vcp_pivot          DOUBLE PRECISION;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS vcp_pivot_dist     DOUBLE PRECISION;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS vcp_base_days      INT;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS vcp_low_vol_ratio  DOUBLE PRECISION;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS up_days_20d        INT;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS down_days_20d      INT;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS ud_vol_ratio_20d   DOUBLE PRECISION;
