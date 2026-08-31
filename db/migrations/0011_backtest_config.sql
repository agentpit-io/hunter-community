-- 回测配置表 · 单行(id=1) · admin 后台可改
--
-- ⚠ 列必须与 app/services/backtest/config.py 的 _FIELDS(= DEFAULTS.keys())
--   逐一对齐。get_config() 执行:
--       SELECT <_FIELDS> FROM backtest_config WHERE id = 1
--   save_config() 执行:
--       UPDATE backtest_config SET <...>, updated_at = NOW(), updated_by = %s WHERE id = 1
--   少一列 get_config 就抛异常 → 回退 DEFAULTS(warning 依旧刷屏),等于白建表。
--   故此处照抄 config.py DEFAULTS 的全部 28 个字段 + id + updated_at + updated_by。
--
-- 默认值取自 config.py DEFAULTS,保证「表存在」与「表缺失回退」行为一致;
-- 仅两处按 T12 目标覆盖:
--   pool_mode = 'watchlist'  —— 让 scheduler 走 stocks 池(见 seed 脚本灌入的 CSI300)
--   run_minute = 30          —— 明确 16:30 CST 收盘后跑
CREATE TABLE IF NOT EXISTS backtest_config (
    id                SERIAL PRIMARY KEY,

    -- 调度 / 股票池
    pool_mode         VARCHAR(32)   DEFAULT 'watchlist',  -- core/custom/chain_all/watchlist
    pred_len          SMALLINT      DEFAULT 5,
    run_hour          SMALLINT      DEFAULT 16,
    run_minute        SMALLINT      DEFAULT 30,
    concurrency       SMALLINT      DEFAULT 12,            -- 并发调 Kronos
    enabled           BOOLEAN       DEFAULT TRUE,

    -- 命中判定
    flat_band         NUMERIC(6,2)  DEFAULT 0.5,
    strict_dir        BOOLEAN       DEFAULT TRUE,
    rel_err_pct       NUMERIC(6,2)  DEFAULT 20.0,
    abs_err_pp        NUMERIC(6,2)  DEFAULT 1.5,
    reversal_min      NUMERIC(6,2)  DEFAULT 1.0,
    strength_delta    NUMERIC(6,2)  DEFAULT 1.5,
    driver_min_share  NUMERIC(6,2)  DEFAULT 30.0,
    model_ver         VARCHAR(32)   DEFAULT 'pro-v1',

    -- P0 数据质量过滤
    skip_suspended    BOOLEAN       DEFAULT TRUE,
    skip_limit        BOOLEAN       DEFAULT TRUE,
    skip_st           BOOLEAN       DEFAULT TRUE,          -- 需 company_master(见 0012)
    min_list_days     INT           DEFAULT 60,
    min_amount_wan    INT           DEFAULT 5000,          -- 万元 · 5000 万成交额门槛
    adjust_mode       VARCHAR(8)    DEFAULT 'qfq',

    -- P1 基准与极端值
    benchmark_code    VARCHAR(20)   DEFAULT '000300.SH',
    max_pred_pct      NUMERIC(6,2)  DEFAULT 11.0,
    outlier_mode      VARCHAR(16)   DEFAULT 'clip',        -- clip/exclude/keep

    -- P2 运维
    retain_days       INT           DEFAULT 365,
    kronos_retry      SMALLINT      DEFAULT 2,
    kronos_timeout    INT           DEFAULT 300,           -- httpx read timeout(秒)
    alert_hit_rate    NUMERIC(6,2)  DEFAULT 50.0,          -- 命中率(%)低于此值告警

    updated_at        TIMESTAMPTZ   DEFAULT NOW(),
    updated_by        VARCHAR(255)  DEFAULT ''
);

-- seed 默认一行(若无)· 显式点名两处覆盖项,其余走列默认
INSERT INTO backtest_config (id, enabled, pool_mode, run_hour, run_minute)
VALUES (1, TRUE, 'watchlist', 16, 30)
ON CONFLICT (id) DO NOTHING;
