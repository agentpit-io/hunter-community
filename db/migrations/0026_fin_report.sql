-- ════════════════════════════════════════════════════════════════════════
-- 0026_fin_report.sql · 智能炒股 · M5 每日报告
--
-- 依据：`plan/ref/09-数据库结构方案.md` §4.8（fin_report / fin_report_fact /
--       fin_publish_receipt —— 三张表 0023 已建，本文件**只补一列**）。
--
-- ⚠️ 与 `db/migrations/` 的既有约定一致：**已有部署不会执行
--    `docker-entrypoint-initdb.d`**；本文件由 `app.migrate` 在 api 启动时按文件名
--    顺序执行（已执行过的跳过）。同一段 DDL 在
--    `apps/api/app/services/fin/report.py::ensure_columns` 里**再幂等执行一次**，
--    让「不跑迁移的一次性容器」也能用。两处必须一致。
--
-- 幂等：一律 IF NOT EXISTS；**不 DROP、不改列类型、不重命名**。重复执行不报错。
--
-- 补的两处：
--   ① `fin_report.artifact_ref` —— 报告 HTML 产物引用（M5 新增，指向
--      `hunter_artifacts.published_artifact.short_id`）。
--   ② `hunter_artifacts.published_artifact` 的 html 两列 —— M5 复用现仓产物表存
--      报告 HTML。老库这个表是「只有 markdown」的版本（本机上 M4 那个验收库实测
--      确认），不补列 insert 会报 `column "artifact_type" does not exist`。
--      与 `apps/api/sql/20260807_artifact_html.sql` 内容一致（那份是留档）。
-- ════════════════════════════════════════════════════════════════════════

ALTER TABLE fin_report ADD COLUMN IF NOT EXISTS artifact_ref TEXT;

ALTER TABLE hunter_artifacts.published_artifact
  ADD COLUMN IF NOT EXISTS artifact_type TEXT NOT NULL DEFAULT 'markdown',
  ADD COLUMN IF NOT EXISTS content_html TEXT;

-- 老数据 content_md 是 NOT NULL；html 类型的记录 content_md 允许空。
ALTER TABLE hunter_artifacts.published_artifact
  ALTER COLUMN content_md DROP NOT NULL;

-- 类型与内容一致（与 sql/20260807_artifact_html.sql 同一条约束）。
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_artifact_content') THEN
    ALTER TABLE hunter_artifacts.published_artifact
      ADD CONSTRAINT chk_artifact_content
      CHECK (
        (artifact_type = 'markdown' AND content_md IS NOT NULL AND content_md != '')
        OR (artifact_type = 'html' AND content_html IS NOT NULL AND content_html != '')
      );
  END IF;
END$$;
