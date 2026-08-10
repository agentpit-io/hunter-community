-- /chat 用户隔离:opencode session ↔ hunter 用户 的归属映射
-- 2026-08-06 · 方案见 agentpit/doc/云清每日总结/8月/20260806-AI对话三步升级方案-*.md
--
-- 为什么放在我们这层而不是改 opencode:
--   opencode 的会话按 project(目录)分组, 没有"用户"概念。
--   给它加 user 维度要动 packages/*, 触碰 fork 治理红线且每次 rebase 必冲突。
--   归属关系本来就是我们的业务数据, 放 hermes 库最合理。
--
-- 纯 ADD, 不动任何既有表。

BEGIN;

CREATE TABLE IF NOT EXISTS chat_session_owner (
  session_id   TEXT        PRIMARY KEY,              -- opencode 的 ses_xxx
  user_id      TEXT        NOT NULL,                 -- hunter 用户 (JWT sub)
  title        TEXT        NOT NULL DEFAULT '',      -- 冗余一份, 列表页免二次请求
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_used_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  archived     BOOLEAN     NOT NULL DEFAULT FALSE
);

-- 列表页按用户 + 最近使用倒序
CREATE INDEX IF NOT EXISTS idx_cso_user
  ON chat_session_owner (user_id, last_used_at DESC)
  WHERE NOT archived;

COMMENT ON TABLE chat_session_owner IS
  '/chat 会话归属:每个 opencode session 属于哪个 hunter 用户。BFF 据此过滤与鉴权';

COMMIT;
