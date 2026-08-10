-- Artifact 发布 · 公开链接系统
-- 用户点"Publish artifact" · 生成短 URL · 匿名可访问
-- 严格遵 Claude 规则: unpublish 后不可 republish · 强制新 artifact

CREATE SCHEMA IF NOT EXISTS hunter_artifacts;

CREATE TABLE IF NOT EXISTS hunter_artifacts.published_artifact (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  short_id TEXT NOT NULL UNIQUE,               -- URL 短 ID · e.g. "aa42f292-bce1"
  owner_user_id TEXT NOT NULL,                  -- 发布者 (hermes JWT sub)
  session_id TEXT,                              -- 来源 session · 可空
  source_message_id TEXT,                       -- 来源 assistant message
  title TEXT NOT NULL,                          -- artifact 标题
  content_md TEXT NOT NULL,                     -- markdown 原文
  view_count INT NOT NULL DEFAULT 0,            -- 访问次数 (递增 · 不显示给普通用户)
  is_published BOOLEAN NOT NULL DEFAULT true,   -- 发布状态 · 软删
  published_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  unpublished_at TIMESTAMPTZ,                   -- 撤销时间
  metadata JSONB DEFAULT '{}'::jsonb,           -- 扩展 · agent/model/字数等
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_artifact_owner
  ON hunter_artifacts.published_artifact (owner_user_id, published_at DESC);

CREATE INDEX IF NOT EXISTS ix_artifact_short
  ON hunter_artifacts.published_artifact (short_id)
  WHERE is_published = true;

-- 唯一索引 · 每个 source_message_id 只允许发布一次
-- 与 Claude 规则一致 · unpublish 后不可 republish · 想再发必须新 message
CREATE UNIQUE INDEX IF NOT EXISTS ux_artifact_source
  ON hunter_artifacts.published_artifact (owner_user_id, source_message_id)
  WHERE source_message_id IS NOT NULL;
