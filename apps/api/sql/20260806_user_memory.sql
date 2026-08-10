-- 用户记忆体:主动设置的画像 + 从对话自动浓缩的记忆
-- 2026-08-06 · 方案见 agentpit/doc/云清每日总结/8月/20260806-AI对话三步升级方案-*.md §四
--
-- ⚠️ 与老板文档里的 hunter-memory 不是同一个东西, 别混:
--   hunter-memory  = 投资论点 + 关键假设("我买三一是因为海外订单要涨到35%"), 季度核对
--   本表 user_*    = 用户偏好 + 对话习惯("这人偏保守, 常看半导体"), 每次对话作 context
--
-- 纯 ADD, 不动任何既有表。

BEGIN;

-- 用户主动设置的画像。结构化字段单独建列, 便于 admin 后台做分布统计。
CREATE TABLE IF NOT EXISTS user_profile (
  user_id      TEXT        PRIMARY KEY,
  risk_style   TEXT        NOT NULL DEFAULT '',    -- conservative|steady|balanced|active|aggressive
  max_drawdown INT,                                -- 10|20|30|null(不设限)
  horizon      TEXT        NOT NULL DEFAULT '',    -- intraday|week|month|quarter|year
  markets      TEXT[]      NOT NULL DEFAULT '{}',  -- {A,HK,US}
  sectors      TEXT[]      NOT NULL DEFAULT '{}',
  cap_pref     TEXT        NOT NULL DEFAULT '',    -- large|mid|small|any
  weight_order TEXT[]      NOT NULL DEFAULT '{}',  -- {fundamental,technical,flow,news}
  verbosity    TEXT        NOT NULL DEFAULT '',    -- brief|points|detailed
  taboos       TEXT[]      NOT NULL DEFAULT '{}',
  onboarded    BOOLEAN     NOT NULL DEFAULT FALSE, -- 是否走过引导(跳过也算)
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 系统从对话浓缩出来的记忆。结构会随需求演进, 用 JSONB 不做强 schema。
CREATE TABLE IF NOT EXISTS user_memory (
  user_id       TEXT        PRIMARY KEY,
  memory        JSONB       NOT NULL DEFAULT '{}',
  session_count INT         NOT NULL DEFAULT 0,     -- 已浓缩过多少会话
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 浓缩历史。记忆写错时能回溯是怎么写进去的。
CREATE TABLE IF NOT EXISTS user_memory_log (
  id         BIGSERIAL   PRIMARY KEY,
  user_id    TEXT        NOT NULL,
  session_id TEXT        NOT NULL DEFAULT '',
  before     JSONB,
  after      JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_uml_user ON user_memory_log (user_id, created_at DESC);

COMMENT ON TABLE user_profile IS '用户主动设置的投资偏好画像, 每次对话注入 system prompt';
COMMENT ON TABLE user_memory  IS '从对话自动浓缩的记忆(只记事实不记推断), 用户可查看/编辑/清空';

COMMIT;
