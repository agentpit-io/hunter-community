-- ════════════════════════════════════════════════════════════════════════
-- 0023_fin_core.sql · 智能炒股 · 模拟账本核心（一期 25 张 fin_* 表 + 运行期角色）
--
-- 依据：`plan/ref/09-数据库结构方案.md` §一（分库分角色）/ §二（通用约定）/
--       §三（表清单）/ §四（逐表 DDL，4.1~4.9）/ §七（迁移规范）
-- 执行：由 `app.migrate` 在 api 启动时按文件名顺序执行（`boot.sh` → `python -m app.migrate`）。
--       不挂 `docker-entrypoint-initdb.d`，不手工跑。
--
-- ⚠️ 与 `09 §一` 的一处**有意偏离**（M1 决定，理由见 `docs/开发文档/M1-成果与测试报告.md`）：
--   账本表建在**应用库**（`fin_` 前缀）而不是独立库 `hunter_fin`。三条实测原因：
--     ① `app.migrate` 每文件一个事务、只持一条连接（`app/migrate.py` 的 `apply_one`），
--        PG 禁止在事务块里 `CREATE DATABASE`，psycopg2 也没有 psql 的 `\gexec`；
--     ② 一期开户接口经 `DATABASE_URL` 连应用库（任务要求本轮不接 `paper` 服务），
--        表必须同库才可读写；
--     ③ `09 §十` 待拍板 #5 明确把「独立库 vs 同库」列为未决，取「同库」分支。
--   「谁能写账本必须是一条 GRANT」这条设计意图**保留**：运行期角色 `fin_paper_rw`
--   与逐表 GRANT 照 §一 建，只是授权的对象是应用库里的 `fin_*` 表。
--
-- 幂等：一律 `IF NOT EXISTS`；**不 DROP、不改列类型、不重命名**。
--   重复执行不报错（验收项之一）。角色用 `DO` 块判存在；`GRANT` 天然幂等。
-- 类型：金额 NUMERIC(18,4) · 价格 NUMERIC(12,4) · 比例 NUMERIC(9,6) · 时间 TIMESTAMPTZ。
--   全表**不许出现 float / double / REAL**。
--
-- ⚠️ 第二处**有意偏离**（M1 决定，理由是实测事实）：
--   `09 §四` 把 `fin_project.user_id` / `fin_account.user_id` 写成 `INTEGER`，
--   注释里假设它关联一个整数型 `hunter.users(id)`。**实测不成立**：本仓
--   `users.id` 是 `UUID`（`db/migrations/0001_local_users.sql:7`），鉴权中间件
--   经 JWT 传下来的 `request.state.user_id` 是字符串。往 `INTEGER` 里放 UUID
--   只能靠「编一个映射」，违反红线 1（不许编）。故两处取 `TEXT`，原样存 UUID
--   字符串 —— 与现仓同类的 `screen_quota_usage.user_id TEXT`（0021）同口径。
--   金额 / 价格 / 比例 / 时间四类**严格照抄 09**，未动。
--
-- 表数：§三「一期」行 20 张 + §4.9 一期就建的人机协作 5 张空表 = **25 张**。
--   （§零 的「19 张」与 §三/§四 的枚举对不上，以 §三/§四 为准——见成果报告的「偏离」一节。）
-- ════════════════════════════════════════════════════════════════════════


-- ── 0 · 运行期角色与授权（09 §一）────────────────────────────────────────
-- 迁移执行者（api 启动连接）= 建表；`fin_paper_rw` = 运行期唯一可写账本的角色。
-- 两者分开。密码**不写进仓库**：`paper` 容器（M4/M8）上线时经环境变量单独 provision。
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fin_paper_rw') THEN
    -- 无密码 LOGIN：TCP 口令认证下**登录不了**，只能等部署时 provision 密码；
    -- 一期没有 `paper` 容器，这个角色只是把「谁能写账本」这条边界先立起来。
    CREATE ROLE fin_paper_rw LOGIN;
  END IF;
END $$;

-- CONNECT 必须对「当前库」授权（这里不能用字面量库名：库名由部署的 DATABASE_URL 决定）
DO $$
BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO fin_paper_rw', current_database());
END $$;

GRANT USAGE ON SCHEMA public TO fin_paper_rw;


-- ── 4.1 项目与账户 ───────────────────────────────────────────────────────

-- 项目：一段「一个档位 + 一个账本 + 一套参数」的模拟运行。
CREATE TABLE IF NOT EXISTS fin_project (
  project_id      TEXT PRIMARY KEY,                    -- prj_...
  user_id         TEXT NOT NULL,                       -- hunter.users(id) 的 UUID 字符串，跨库不建 FK
  tier            TEXT NOT NULL CHECK (tier IN ('play','manage','operate')),
  status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','closed')),
  initial_capital NUMERIC(18,4) NOT NULL,              -- 由档位决定，写入后不可改
  currency        TEXT NOT NULL DEFAULT 'CNY' CHECK (currency = 'CNY'),
  market_scope    TEXT NOT NULL DEFAULT 'CN_A' CHECK (market_scope = 'CN_A'),
  version         BIGINT NOT NULL DEFAULT 0,           -- 账本版本：每笔成交 +1（幂等与并发校验）
  run_mode        TEXT NOT NULL DEFAULT 'auto'
                    CHECK (run_mode IN ('auto','copilot','manual')),  -- 一期恒 auto
  opened_at       TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 上海时间展示
  closed_at       TIMESTAMPTZ,
  close_reason    TEXT
);
-- 一个账户同时只能有一个进行中的项目 —— 由部分唯一索引保证，不靠界面拦（09 §六-1）
CREATE UNIQUE INDEX IF NOT EXISTS fin_project_one_active ON fin_project (user_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS fin_project_user ON fin_project (user_id, opened_at DESC);

-- 账户：用户身份级（一个用户一个账户）。项目是它的「一段」。
CREATE TABLE IF NOT EXISTS fin_account (
  account_id   TEXT PRIMARY KEY,          -- acct_...
  user_id      TEXT NOT NULL,             -- hunter.users(id) 的 UUID 字符串
  currency     TEXT NOT NULL DEFAULT 'CNY',
  market_scope TEXT NOT NULL DEFAULT 'CN_A',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  locked_at    TIMESTAMPTZ                -- 开启即锁；档位制下恒为真
);
CREATE UNIQUE INDEX IF NOT EXISTS fin_account_one_per_user ON fin_account (user_id);


-- ── 4.2 参数 ─────────────────────────────────────────────────────────────

-- 账户/项目参数：字段直接对应 03-新用户向导与操作参数方案.md §九
CREATE TABLE IF NOT EXISTS fin_param (
  project_id  TEXT PRIMARY KEY REFERENCES fin_project(project_id),
  board_flags JSONB NOT NULL DEFAULT '{}'::jsonb,   -- 主板/创业板/科创板/北交所/ST/次新 是否允许
  sector_prefs JSONB NOT NULL DEFAULT '[]'::jsonb,  -- 行业偏好（空 = 不限）
  liquidity_min_amount       NUMERIC(18,4),
  liquidity_max_participation NUMERIC(9,6),
  strategies   JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{key, params, version}]
  hold_days_max INTEGER NOT NULL DEFAULT 3,
  stop_loss_pct    NUMERIC(9,6) NOT NULL,
  take_profit_pct  NUMERIC(9,6) NOT NULL,
  max_positions    INTEGER NOT NULL,
  max_position_pct NUMERIC(9,6) NOT NULL,
  min_order_amount NUMERIC(18,4) NOT NULL,
  daily_max_new    INTEGER NOT NULL,
  daily_max_orders INTEGER NOT NULL,
  daily_loss_halt_pct        NUMERIC(9,6) NOT NULL,
  account_drawdown_halt_pct  NUMERIC(9,6) NOT NULL,
  params_locked_at TIMESTAMPTZ,                     -- = 项目开启时刻
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 参数改动日志：谁、何时、改前改后。只追加，不可修改。
CREATE TABLE IF NOT EXISTS fin_param_change_log (
  id          BIGSERIAL PRIMARY KEY,
  project_id  TEXT NOT NULL REFERENCES fin_project(project_id),
  actor       TEXT NOT NULL,             -- 'system' 或 user_id
  field       TEXT NOT NULL,
  old_value   JSONB,
  new_value   JSONB,
  changed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fin_param_log_ix ON fin_param_change_log (project_id, changed_at DESC);


-- ── 4.3 市场与标的 ───────────────────────────────────────────────────────

-- 标的元数据：从 hunter.company_master / stock_universe 同步而来（跨库不 join）。
CREATE TABLE IF NOT EXISTS fin_instrument (
  code           TEXT PRIMARY KEY,        -- 6 位代码
  name           TEXT NOT NULL,
  exchange       TEXT NOT NULL CHECK (exchange IN ('SH','SZ','BJ')),
  board          TEXT NOT NULL CHECK (board IN ('main','chinext','star','bse')),
  is_st          BOOLEAN NOT NULL DEFAULT false,
  limit_up_pct   NUMERIC(9,6) NOT NULL,   -- 0.10 / 0.20 / 0.05
  limit_down_pct NUMERIC(9,6) NOT NULL,
  lot_size       INTEGER NOT NULL DEFAULT 100,
  listed_at      DATE,
  is_active      BOOLEAN NOT NULL DEFAULT true,
  source         TEXT NOT NULL,           -- 元数据来源（待拍板项④）
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 交易日历 + 时段（撮合的「交易时段」判定）
CREATE TABLE IF NOT EXISTS fin_market_calendar (
  trade_date  DATE PRIMARY KEY,
  is_trading  BOOLEAN NOT NULL,
  sessions    JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{"open":"09:30","close":"11:30"}, ...]
  note        TEXT
);

-- 成本模型：费率与滑点带版本，报告里要能说清「用的是哪一版」
CREATE TABLE IF NOT EXISTS fin_fee_model (
  version        TEXT PRIMARY KEY,        -- 'fee-cn-a-v1'
  commission_pct NUMERIC(9,6) NOT NULL,   -- 0.00025
  commission_min NUMERIC(18,4) NOT NULL,  -- 5.00
  stamp_tax_pct  NUMERIC(9,6) NOT NULL,   -- 0.0005（仅卖出）
  transfer_fee_pct NUMERIC(9,6) NOT NULL, -- 0.00001
  effective_from TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 执行模型：滑点、成交量约束（参数化，不用魔法数）
CREATE TABLE IF NOT EXISTS fin_execution_model (
  version        TEXT PRIMARY KEY,        -- 'paper-model-v1'
  slippage_ticks NUMERIC(9,4) NOT NULL,   -- 市价单滑点 = N 个最小变动价位（待拍板项③）
  part_fill      BOOLEAN NOT NULL DEFAULT false,
  note           TEXT,
  effective_from TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ── 4.4 快照（不可变）────────────────────────────────────────────────────

-- 行情快照：成交价的唯一依据。 编号 = SNAP-{日期}-{时刻}-{代码}
-- snapshot_time 必须来自数据源，不取本机时间；断流时 missing_flag=true。
CREATE TABLE IF NOT EXISTS fin_snapshot (
  snapshot_id   TEXT PRIMARY KEY,          -- SNAP-20260930-093000-600519
  code          TEXT NOT NULL,
  snapshot_time TIMESTAMPTZ NOT NULL,      -- 数据源回报的时刻
  source        TEXT NOT NULL,             -- 'akshare' / 'yfinance' / 'qfinzero' / ...
  last_price    NUMERIC(12,4),
  bid1_price    NUMERIC(12,4),
  bid1_volume   INTEGER,
  ask1_price    NUMERIC(12,4),
  ask1_volume   INTEGER,
  prev_close    NUMERIC(12,4),
  quality       TEXT NOT NULL DEFAULT 'ok' CHECK (quality IN ('ok','stale','missing')),
  missing_flag  BOOLEAN NOT NULL DEFAULT false,
  raw_ref       TEXT,                      -- 原始报文引用（对象存储/文件路径）
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fin_snapshot_code_time ON fin_snapshot (code, snapshot_time DESC);


-- ── 4.5 委托、成交与账本 ─────────────────────────────────────────────────

-- 委托。source / actor / intent_ref / decline_reason 是「人机归因」的全部输入 ——
-- 一期就建好并恒为 ('ai','system',NULL,NULL)。
CREATE TABLE IF NOT EXISTS fin_order (
  order_id    TEXT PRIMARY KEY,            -- ord_...
  project_id  TEXT NOT NULL REFERENCES fin_project(project_id),
  segment_id  TEXT,                        -- 口径分段（二期起写值；一期为 NULL）
  code        TEXT NOT NULL,
  side        TEXT NOT NULL CHECK (side IN ('buy','sell')),
  qty         INTEGER NOT NULL CHECK (qty > 0),
  price_type  TEXT NOT NULL CHECK (price_type IN ('limit','market')),
  limit_price NUMERIC(12,4),
  status      TEXT NOT NULL CHECK (status IN
                ('pending','accepted','partially_filled','filled','rejected','cancelled','expired')),
  filled_qty  INTEGER NOT NULL DEFAULT 0,
  source      TEXT NOT NULL DEFAULT 'ai'
                CHECK (source IN ('ai','human','human_confirmed')),
  actor       TEXT NOT NULL DEFAULT 'system',
  intent_ref  JSONB,                       -- {reason, ai_view_snapshot}（L3 留痕，不可修改）
  decline_reason TEXT,                     -- 被风控拒绝的原因（人工委托同样记）
  decision_ref TEXT,                       -- 产生它的 StrategyDecision / 幂等键
  valid_until TIMESTAMPTZ,                 -- 过期不补单
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fin_order_proj_time ON fin_order (project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS fin_order_open ON fin_order (project_id, status) WHERE status IN ('pending','accepted','partially_filled');

-- 成交。每笔都绑快照编号 —— 「按当时快照价成交」这句话的技术兑现。
CREATE TABLE IF NOT EXISTS fin_trade (
  trade_id    TEXT PRIMARY KEY,            -- trd_...
  order_id    TEXT NOT NULL REFERENCES fin_order(order_id),
  project_id  TEXT NOT NULL REFERENCES fin_project(project_id),
  code        TEXT NOT NULL,
  side        TEXT NOT NULL CHECK (side IN ('buy','sell')),
  qty         INTEGER NOT NULL CHECK (qty > 0),
  price       NUMERIC(12,4) NOT NULL,
  amount      NUMERIC(18,4) NOT NULL,      -- qty * price
  commission  NUMERIC(18,4) NOT NULL DEFAULT 0,
  stamp_tax   NUMERIC(18,4) NOT NULL DEFAULT 0,
  transfer_fee NUMERIC(18,4) NOT NULL DEFAULT 0,
  total_fee   NUMERIC(18,4) NOT NULL DEFAULT 0,
  snapshot_id TEXT NOT NULL REFERENCES fin_snapshot(snapshot_id),
  source      TEXT NOT NULL CHECK (source IN ('ai','human','human_confirmed')),
  fee_model_version TEXT NOT NULL,
  traded_at   TIMESTAMPTZ NOT NULL,        -- 成交（= 委托到达）时刻
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fin_trade_proj_time ON fin_trade (project_id, traded_at DESC);
CREATE INDEX IF NOT EXISTS fin_trade_source ON fin_trade (project_id, source);   -- 归因统计用

-- 资金流水：追加式。可用/冻结的每一次变动都在这里，余额是「算出来的」不是「存出来的」。
CREATE TABLE IF NOT EXISTS fin_cash_ledger (
  entry_id    BIGSERIAL PRIMARY KEY,
  project_id  TEXT NOT NULL REFERENCES fin_project(project_id),
  kind        TEXT NOT NULL CHECK (kind IN
                ('deposit','freeze','unfreeze','buy','sell','fee','tax','adjust')),
  amount      NUMERIC(18,4) NOT NULL,      -- 正=入，负=出
  available_after NUMERIC(18,4) NOT NULL,
  frozen_after    NUMERIC(18,4) NOT NULL,
  order_id    TEXT REFERENCES fin_order(order_id),
  trade_id    TEXT REFERENCES fin_trade(trade_id),
  memo        TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fin_cash_proj_time ON fin_cash_ledger (project_id, entry_id);

-- 持仓。custody 三列一期就建（恒 'ai' / NULL / true），二期「接管」直接启用。
CREATE TABLE IF NOT EXISTS fin_position (
  project_id   TEXT NOT NULL REFERENCES fin_project(project_id),
  code         TEXT NOT NULL,
  qty          INTEGER NOT NULL DEFAULT 0,
  sellable_qty INTEGER NOT NULL DEFAULT 0,   -- T+1：当日买入不计入可卖
  avg_cost     NUMERIC(12,4) NOT NULL DEFAULT 0,
  opened_at    TIMESTAMPTZ,
  custody      TEXT NOT NULL DEFAULT 'ai' CHECK (custody IN ('ai','human')),
  custody_changed_at TIMESTAMPTZ,
  custody_stop_enabled BOOLEAN NOT NULL DEFAULT true,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, code)
);


-- ── 4.6 估值与对账 ───────────────────────────────────────────────────────

-- 估值：每日一次（收盘后 + 需要时冻结）。净值是收益率的分母载体。
CREATE TABLE IF NOT EXISTS fin_valuation (
  project_id   TEXT NOT NULL REFERENCES fin_project(project_id),
  as_of        TIMESTAMPTZ NOT NULL,       -- 估值时点
  cash_available NUMERIC(18,4) NOT NULL,
  cash_frozen    NUMERIC(18,4) NOT NULL,
  market_value   NUMERIC(18,4) NOT NULL,
  total_assets   NUMERIC(18,4) NOT NULL,   -- 三者之和（由对账校验）
  nav            NUMERIC(18,6) NOT NULL,   -- total_assets / initial_capital
  price_source   TEXT NOT NULL,            -- 估值用的价从哪来
  quality        TEXT NOT NULL DEFAULT 'ok' CHECK (quality IN ('ok','stale','missing')),
  missing_flag   BOOLEAN NOT NULL DEFAULT false,
  PRIMARY KEY (project_id, as_of)
);

-- 对账结果：每日核算自洽性，不平就写这里并告警。
CREATE TABLE IF NOT EXISTS fin_recon_log (
  id          BIGSERIAL PRIMARY KEY,
  project_id  TEXT NOT NULL REFERENCES fin_project(project_id),
  as_of       TIMESTAMPTZ NOT NULL,
  passed      BOOLEAN NOT NULL,
  checks      JSONB NOT NULL,              -- [{"name","expected","actual","passed"}]
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ── 4.7 恢复审计（幂等与长任务）──────────────────────────────────────────

-- 幂等：同一键、同一内容 → 返回原结果；同一键、不同内容 → 冲突，不覆盖。
CREATE TABLE IF NOT EXISTS fin_idempotency (
  idempotency_key     TEXT PRIMARY KEY,
  request_hash        TEXT NOT NULL,       -- sha256 of canonical payload
  response_ref        TEXT,                -- 原回执引用
  project_id          TEXT REFERENCES fin_project(project_id),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 长任务：submit → 持久化 job_id → get/完成事件 → 结果引用 → 可取消。
CREATE TABLE IF NOT EXISTS fin_job (
  job_id      TEXT PRIMARY KEY,            -- job_...
  project_id  TEXT REFERENCES fin_project(project_id),
  type        TEXT NOT NULL,               -- 'snapshot' / 'strategy_run' / 'report' / ...
  status      TEXT NOT NULL CHECK (status IN
                ('ACCEPTED','RUNNING','SUCCEEDED','FAILED','CANCEL_REQUESTED','CANCELLED')),
  params      JSONB NOT NULL DEFAULT '{}'::jsonb,
  checkpoint  JSONB,                       -- 业务检查点（Temporal 不保存任意进程内存）
  result_ref  TEXT,
  idempotency_key TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fin_job_status ON fin_job (status, created_at);


-- ── 4.8 报告与发布 ───────────────────────────────────────────────────────

-- 报告：事实层与表达层分开存 —— 「AI 不许写数字」的落地方式。
CREATE TABLE IF NOT EXISTS fin_report (
  report_id   TEXT PRIMARY KEY,            -- rpt_...
  project_id  TEXT NOT NULL REFERENCES fin_project(project_id),
  trade_date  DATE NOT NULL,
  valuation_as_of TIMESTAMPTZ NOT NULL,    -- 报告基于哪一次估值
  analysis_text   TEXT,                    -- 表达层：AI 写的文字
  self_review     JSONB,                   -- {did_well, did_bad, change_tomorrow}
  llm_provider    TEXT,
  llm_model       TEXT,
  prompt_version  TEXT,
  status      TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','validated','published','failed')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (project_id, trade_date)
);

-- 事实层：每个数字一行，带它算出来的依据引用。校验 = 逐行比对，不是让 AI 自查。
CREATE TABLE IF NOT EXISTS fin_report_fact (
  report_id   TEXT NOT NULL REFERENCES fin_report(report_id),
  metric_key  TEXT NOT NULL,               -- 'nav' / 'return_pct' / 'max_drawdown' / 'fee_total' ...
  value       NUMERIC(18,6),
  unit        TEXT,
  source_ref  TEXT NOT NULL,               -- 'fin_valuation:2026-09-30' / 'fin_trade:*'
  computed_by TEXT NOT NULL,               -- 代码位置（函数名/版本）
  PRIMARY KEY (report_id, metric_key)
);

-- 发布回执：结果不明时进 UNKNOWN，不盲目重发。
CREATE TABLE IF NOT EXISTS fin_publish_receipt (
  receipt_id  TEXT PRIMARY KEY,
  report_id   TEXT NOT NULL REFERENCES fin_report(report_id),
  channel     TEXT NOT NULL,               -- 'in_app' / 'email' / 'wechat' ...
  status      TEXT NOT NULL CHECK (status IN ('SUCCESS','FAILED','UNKNOWN')),
  external_id TEXT,
  detail      TEXT,
  attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS fin_receipt_unknown ON fin_publish_receipt (status) WHERE status = 'UNKNOWN';


-- ── 4.9 一期就建的人机协作表（一期只建不写，二期启用不迁移历史账本）──────

CREATE TABLE IF NOT EXISTS fin_watchlist (
  project_id TEXT NOT NULL REFERENCES fin_project(project_id),
  code       TEXT NOT NULL,
  added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, code)
);

CREATE TABLE IF NOT EXISTS fin_blocklist (
  project_id TEXT NOT NULL REFERENCES fin_project(project_id),
  code       TEXT NOT NULL,
  added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, code)
);

CREATE TABLE IF NOT EXISTS fin_daily_constraint (
  project_id TEXT NOT NULL REFERENCES fin_project(project_id),
  kind       TEXT NOT NULL,                -- 'sell_only' / 'no_new_position' / 'watchlist_only'
  expire_at  TIMESTAMPTZ NOT NULL,         -- 当日收盘，自动失效
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, kind, expire_at)
);

CREATE TABLE IF NOT EXISTS fin_human_action_log (   -- 与 fin_param_change_log 同构
  id         BIGSERIAL PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES fin_project(project_id),
  action     TEXT NOT NULL,                -- 'mode_switch' / 'custody_take' / 'pool_change' / 'manual_order'
  detail     JSONB NOT NULL,
  actor      TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fin_segment (            -- 口径分段：模式变更/接管事件开新段
  segment_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES fin_project(project_id),
  reason     TEXT NOT NULL,
  opened_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at  TIMESTAMPTZ
);


-- ── 权限：按表授权（09 §一 + §七「新表必须显式授权，不给默认权限」）─────
-- 追加式（只 SELECT / INSERT，不给 UPDATE / DELETE）：账本与不可变日志
GRANT SELECT, INSERT ON
  fin_trade, fin_cash_ledger, fin_snapshot, fin_valuation,
  fin_human_action_log, fin_param_change_log, fin_recon_log, fin_report_fact
  TO fin_paper_rw;

-- 状态会变的表（额外给 UPDATE，且 UPDATE 只用于状态列）：
GRANT SELECT, INSERT, UPDATE ON
  fin_order, fin_position, fin_project, fin_param,
  fin_job, fin_idempotency, fin_report, fin_publish_receipt,
  fin_account, fin_instrument, fin_market_calendar, fin_fee_model, fin_execution_model,
  fin_watchlist, fin_blocklist, fin_daily_constraint, fin_segment
  TO fin_paper_rw;

-- 任何角色都不给 DELETE（09 §一 末行）。
