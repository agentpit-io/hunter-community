-- 用户保存的扫描策略 · per-user
--
-- ⚠️ 已有部署不会执行这个文件:db/migrations 挂在 postgres 的
-- /docker-entrypoint-initdb.d,只在数据卷第一次初始化时跑。
-- 真正生效的是 apps/api/app/services/screen_saved.py 里随代码走的 _DDL,
-- 首次用到时 _ensure_table() 建表。本文件给全新安装用 + 留档,两处必须一致。
CREATE TABLE IF NOT EXISTS user_screen_preset (
  id         BIGSERIAL PRIMARY KEY,
  user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  market     TEXT NOT NULL,
  script     TEXT NOT NULL,
  sort_by    TEXT,
  sort_desc  BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (user_id, name)
);
CREATE INDEX IF NOT EXISTS idx_user_screen_preset_user
  ON user_screen_preset (user_id, updated_at DESC);
