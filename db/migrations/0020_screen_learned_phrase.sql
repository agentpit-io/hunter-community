-- 扫描筛选对照表 · 从 AI 识别里学来的说法(全站共享)
--
-- ⚠️ 已有部署不会执行这个文件:db/migrations 挂在 postgres 的
-- /docker-entrypoint-initdb.d,只在数据卷第一次初始化时跑。
-- 真正生效的是 apps/api/app/services/quant/screen_learned.py 里随代码走的 _DDL,
-- 首次用到时 _ensure_table() 建表。本文件给全新安装用 + 留档,两处必须一致。
CREATE TABLE IF NOT EXISTS screen_learned_phrase (
  id           BIGSERIAL PRIMARY KEY,
  key          TEXT NOT NULL UNIQUE,
  exprs        JSONB NOT NULL,
  source_text  TEXT,
  model        TEXT,
  market       TEXT,
  created_by   TEXT,
  hits         INTEGER NOT NULL DEFAULT 0,
  last_hit_at  TIMESTAMPTZ,
  disabled     BOOLEAN NOT NULL DEFAULT FALSE,
  disabled_by  TEXT,
  disabled_at  TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
