-- 0033_snapshot_quote_quality.sql · 智能炒股二期 N3
--
-- 快照带上「盘口质量」（设计文档附 B 的 `quote_quality`）：
--   full       —— 有盘口（A 股：买一/卖一都有）
--   last_only  —— 只有最新价（港美股：数据源实测没有盘口）
--   no_book    —— 无盘口、按最新价撮合（本期不做的市价单路径）
--
-- ⚠️ 已有部署**不会**执行本目录（postgres 的 `docker-entrypoint-initdb.d` 只在数据卷
-- 第一次创建时跑一次）。真正生效的是 api 启动时 `python -m app.migrate` 按文件名顺序
-- 补跑 —— 本文件就是那条链的一环，所以必须幂等、可重复执行。
--
-- 历史行（N3 之前的 375 行）**不回填**：当时没有这个字段，填任何值都是编。
-- 它们保持 NULL = 「未记录」，读取方按未知处理。

ALTER TABLE fin_snapshot ADD COLUMN IF NOT EXISTS quote_quality TEXT;

ALTER TABLE fin_snapshot DROP CONSTRAINT IF EXISTS fin_snapshot_quote_quality_check;
ALTER TABLE fin_snapshot ADD  CONSTRAINT fin_snapshot_quote_quality_check
  CHECK (quote_quality IS NULL OR quote_quality IN ('full','last_only','no_book'));

-- 索引不建：quote_quality 只随快照行一起读（按 code / snapshot_id 取行），
-- 没有单独的按质量过滤的查询。
