-- 组合级建议 · 目标权重字段
-- 2026-08-08 · SKILL 4 portfolio_rebalance 依赖此字段
-- 空 = 用等权重兜底

BEGIN;

ALTER TABLE position_thesis
  ADD COLUMN IF NOT EXISTS target_weight_pct NUMERIC(5,2);

COMMENT ON COLUMN position_thesis.target_weight_pct IS
  '用户设定的目标持仓比例 0-100; 组合级建议基准; NULL 时前端提示 "用等权重" 或让用户设置';

COMMIT;
