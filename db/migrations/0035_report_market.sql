-- ════════════════════════════════════════════════════════════════════════
-- 0035_report_market.sql · 智能炒股二期 · 报告加市场维度（只加列 + 放宽唯一键）
--
-- 依据：`plan/ref/11-美股港股模拟交易需求分析与技术实施方案.md` §3.6（报告按市场出）
--       + `plan/N5.md` §一.1（「每个市场一份 + 一份跨市场汇总」）
--
-- ⚠️ **已有部署不会执行这个文件**（`docker-entrypoint-initdb.d` 只在数据卷第一次初始化时跑）。
--    真正生效的 DDL 随代码走 —— 见 `apps/api/app/services/fin/report.py` 的 `ensure_columns()`
--    （`_DDL` 补列 + `_DDL_UNIQUE` 放宽唯一键），两处保持一致。本文件是**给全新安装用 + 留档**。
--
-- 改了什么：
--   ① `fin_report` 加 `market`（缺省 `'CN_A'` —— 一期报告全是 A 股，回填 `CN_A` 是事实不是猜）；
--   ② `fin_report_fact` 加 `market` / `currency`（事实层的市场维度 + 该行的币种）；
--   ③ **唯一键从 `(project_id, trade_date)` 放宽为 `(project_id, market, trade_date)`**
--      —— 三个市场各一份 + 一份 `MULTI` 汇总要能在同一天共存。
--
-- 幂等：`ADD COLUMN IF NOT EXISTS` + 用 DO 块按当前约束形状判（名字在不同环境里
--       可能是自动生成的），重复执行都是空操作。
-- ════════════════════════════════════════════════════════════════════════

ALTER TABLE fin_report      ADD COLUMN IF NOT EXISTS market   TEXT NOT NULL DEFAULT 'CN_A';
ALTER TABLE fin_report_fact ADD COLUMN IF NOT EXISTS market   TEXT;
ALTER TABLE fin_report_fact ADD COLUMN IF NOT EXISTS currency TEXT;

DO $$
DECLARE
  con TEXT;
BEGIN
  -- 先把一期那条两列唯一键找出来（名字在不同环境里可能是自动生成的）。
  SELECT c.conname INTO con
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
   WHERE t.relname = 'fin_report' AND c.contype = 'u'
     AND (SELECT array_agg(a.attname::text ORDER BY a.attname)
            FROM unnest(c.conkey) k JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k)
         = ARRAY['project_id','trade_date'];
  IF con IS NOT NULL THEN
    EXECUTE format('ALTER TABLE fin_report DROP CONSTRAINT %I', con);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
     WHERE t.relname = 'fin_report' AND c.contype = 'u'
       AND (SELECT array_agg(a.attname::text ORDER BY a.attname)
              FROM unnest(c.conkey) k JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k)
           = ARRAY['market','project_id','trade_date']
  ) THEN
    ALTER TABLE fin_report ADD CONSTRAINT fin_report_project_market_date_key
      UNIQUE (project_id, market, trade_date);
  END IF;
END $$;
