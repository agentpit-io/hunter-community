-- 用户自定义数据源 · `_21` §7.1 步 2
-- 2026-08-17
--
-- 老板的核心要求:开源版必须能在「完全不用我们的服务」的前提下跑起来。
-- 这张表就是那句话的落点 —— 在这之前,用户想换数据源只能改 env
-- (DATA_SOURCE_PROVIDER),而那是**全局单选**:换了 A股也一并换掉港美股。
--
-- 与 user_mcp_registrations 同构(加密方式、hint 回显、熔断字段都照抄),
-- 因为它们解决的是同一类问题:用户给的凭证要安全存、要能回显、
-- 要能在失败时降级而不是每次都卡超时。
--
-- 迁移规则:只 ADD,不 DROP/RENAME/ALTER TYPE。
-- 同 DDL 在 app/services/database.py:init_db() 里有幂等副本 ——
-- 那份是新环境自动建表用的,这份是给已有环境手工执行的,两份必须一致。

BEGIN;

CREATE TABLE IF NOT EXISTS user_data_sources (
  id            BIGSERIAL   PRIMARY KEY,
  user_id       TEXT        NOT NULL,
  name          TEXT        NOT NULL,                    -- "我的 Tushare"
  -- 真实上游 · 对齐 source_catalog.UPSTREAM_LABEL 的键。
  -- 选了已知来源(tushare/akshare/…)我们就知道它的返回格式,字段映射内置;
  -- 'custom' 表示完全自定义,那时 field_map 必须自己填(步 6)
  upstream      VARCHAR(32) NOT NULL,
  market        VARCHAR(8)  NOT NULL,                    -- a / hk / us / global
  kind          VARCHAR(16) NOT NULL,                    -- quote / kline / news / ...
  endpoint      TEXT        NOT NULL,
  -- 「这个接口要不要 key」由用户自己勾(用户原话)。
  -- 不勾就不存 key —— 但调用失败时必须把上游的 401 原样带出来,
  -- 而不是笼统的"数据获取失败",否则用户永远不知道是缺 key
  requires_key  BOOLEAN     NOT NULL DEFAULT TRUE,
  key_in        VARCHAR(16) NOT NULL DEFAULT 'header',   -- header / query / body
  key_name      VARCHAR(64) NOT NULL DEFAULT 'Authorization',
  key_prefix    VARCHAR(16) NOT NULL DEFAULT '',         -- "Bearer " 之类 · 拼在值前面
  api_key_enc   TEXT,                                    -- AES-256-GCM · 复用 mcp_crypto.py
  api_key_hint  VARCHAR(12) NOT NULL DEFAULT '',         -- 末 4 位供 UI 回显
  headers       JSONB       NOT NULL DEFAULT '{}'::jsonb,-- 附加 header(不含 auth)
  field_map     JSONB       NOT NULL DEFAULT '{}'::jsonb,-- JSONPath 映射 · 已知来源留空
  enabled       BOOLEAN     NOT NULL DEFAULT TRUE,
  timeout_ms    INT         NOT NULL DEFAULT 15000,

  -- ── 熔断与健康 ──
  -- 为什么要熔断:降级链是"用户的失败了才走我们的"。没有熔断的话,
  -- 用户配了个连不上的源,**每一次请求**都要先卡满超时再降级 ——
  -- 表现是"整个平台变慢了",而根因藏在一个他自己填错的地址里
  fail_streak    INT         NOT NULL DEFAULT 0,
  cooldown_until TIMESTAMPTZ,
  last_ok_at     TIMESTAMPTZ,
  last_err       TEXT,
  call_count     BIGINT      NOT NULL DEFAULT 0,
  error_count    BIGINT      NOT NULL DEFAULT 0,

  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 解析链每次取数都要查这个索引 —— (user, market, kind) 是它的三段主键
CREATE INDEX IF NOT EXISTS idx_uds_user_lookup
  ON user_data_sources(user_id, market, kind) WHERE enabled;

-- 同一用户同一 (market, kind, upstream) 只允许一条:
-- 允许两条的话,"优先用户的"就成了"优先用户的哪一条?" —— 没有答案。
-- 用户想换就改这一条,想并存就用不同的 upstream
CREATE UNIQUE INDEX IF NOT EXISTS idx_uds_user_slot
  ON user_data_sources(user_id, market, kind, upstream);

COMMIT;
