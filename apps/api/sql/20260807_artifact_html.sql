-- Sprint E · Artifact 系统支持 HTML 类型
-- 兼容策略:artifact_type 默认 'markdown' · 老数据无缝
-- content_md 与 content_html 二选一 · 用 CHECK 约束保证

ALTER TABLE hunter_artifacts.published_artifact
  ADD COLUMN IF NOT EXISTS artifact_type TEXT NOT NULL DEFAULT 'markdown',
  ADD COLUMN IF NOT EXISTS content_html TEXT;

-- 老数据 content_md 是 NOT NULL · 新 HTML 记录 content_md 允许空
ALTER TABLE hunter_artifacts.published_artifact
  ALTER COLUMN content_md DROP NOT NULL;

-- 加约束:type='markdown' → content_md 必填 · type='html' → content_html 必填
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'chk_artifact_content'
  ) THEN
    ALTER TABLE hunter_artifacts.published_artifact
      ADD CONSTRAINT chk_artifact_content
      CHECK (
        (artifact_type = 'markdown' AND content_md IS NOT NULL AND content_md != '')
        OR (artifact_type = 'html' AND content_html IS NOT NULL AND content_html != '')
      );
  END IF;
END$$;

-- 部分索引 · 按 type 统计发布量
CREATE INDEX IF NOT EXISTS ix_artifact_type_published
  ON hunter_artifacts.published_artifact (artifact_type, published_at DESC)
  WHERE is_published = true;
