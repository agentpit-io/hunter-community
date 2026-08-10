-- Hermes 多租户改造迁移 SQL
-- 执行方式：psql -U hermes -d hermes -f sql/multi_tenant_migration.sql

BEGIN;

-- ─── 1. stocks 表加 user_id ─────────────────────────────────────────
-- 先删除 position_thesis 的 FK（后面一起重建）
ALTER TABLE position_thesis DROP CONSTRAINT IF EXISTS position_thesis_code_fkey;

-- 加 user_id 列
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS user_id VARCHAR(255) NOT NULL DEFAULT '';
ALTER TABLE position_thesis ADD COLUMN IF NOT EXISTS user_id VARCHAR(255) NOT NULL DEFAULT '';

-- 删除原来的单列主键
ALTER TABLE stocks DROP CONSTRAINT IF EXISTS stocks_pkey;

-- 建复合主键
ALTER TABLE stocks ADD PRIMARY KEY (code, user_id);

-- ─── 2. push_tasks 表加 user_id ──────────────────────────────────────
ALTER TABLE push_tasks ADD COLUMN IF NOT EXISTS user_id VARCHAR(255) NOT NULL DEFAULT '';

-- name 原来是全局 UNIQUE，现在改为 (name, user_id) 联合唯一
ALTER TABLE push_tasks DROP CONSTRAINT IF EXISTS push_tasks_name_key;
ALTER TABLE push_tasks ADD CONSTRAINT push_tasks_name_user_key UNIQUE (name, user_id);

-- 加索引
CREATE INDEX IF NOT EXISTS idx_push_tasks_user_id ON push_tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_stocks_user_id ON stocks(user_id);

-- ─── 3. position_thesis 复合主键 ──────────────────────────────────────
ALTER TABLE position_thesis DROP CONSTRAINT IF EXISTS position_thesis_pkey;
ALTER TABLE position_thesis ADD PRIMARY KEY (code, user_id);

-- 重建 FK（引用复合主键）
ALTER TABLE position_thesis ADD CONSTRAINT position_thesis_code_user_fkey
  FOREIGN KEY (code, user_id) REFERENCES stocks(code, user_id) ON DELETE CASCADE;

-- ─── 4. 新建 user_feishu_config 表 ───────────────────────────────────
CREATE TABLE IF NOT EXISTS user_feishu_config (
    user_id        VARCHAR(255) PRIMARY KEY,
    app_id         VARCHAR(255)  NOT NULL DEFAULT '',
    app_secret     VARCHAR(255)  NOT NULL DEFAULT '',  -- 明文存，和现有逻辑一致
    home_channel   VARCHAR(255)  NOT NULL DEFAULT '',
    enabled        BOOLEAN       NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

COMMIT;
