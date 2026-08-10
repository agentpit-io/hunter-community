-- /chat 能力面板:用户自定义能力 + 对内置能力的个性化覆盖
-- 2026-08-06 · 方案见 agentpit/doc/云清每日总结/8月/20260806-AI对话三步升级方案-*.md §三
--
-- 为什么是"提问模板"而不是让用户写 SKILL.md:
--   opencode 的 SKILL 是给 AI 看的方法论(Markdown),普通用户不会写;
--   但用户会写"帮我看看 XXX 的北向资金和龙虎榜"。两层不冲突 ——
--   SKILL.md 由我们写、走 opencode 机制;这里的模板是给用户用的入口。
--
-- 纯 ADD, 不动任何既有表。

BEGIN;

CREATE TABLE IF NOT EXISTS chat_user_skill (
  id          BIGSERIAL   PRIMARY KEY,
  user_id     TEXT        NOT NULL,
  name        TEXT        NOT NULL,                 -- "我的板块扫描"
  icon        TEXT        NOT NULL DEFAULT '⭐',
  prompt_tpl  TEXT        NOT NULL,                 -- 含 {股票} 占位符
  enabled     BOOLEAN     NOT NULL DEFAULT TRUE,
  sort_order  INT         NOT NULL DEFAULT 0,
  builtin_key TEXT        NOT NULL DEFAULT '',      -- 非空 = 对该内置能力的覆盖/开关
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cus_user
  ON chat_user_skill (user_id, sort_order, id);

-- 同一用户对同一个内置能力只能有一条覆盖记录
CREATE UNIQUE INDEX IF NOT EXISTS idx_cus_builtin
  ON chat_user_skill (user_id, builtin_key)
  WHERE builtin_key <> '';

COMMENT ON TABLE chat_user_skill IS
  '/chat 能力面板:builtin_key 非空表示对内置能力的开关/改写,为空表示用户自建能力';

COMMIT;
