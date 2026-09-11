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
