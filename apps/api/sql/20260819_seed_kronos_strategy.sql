-- C1.6 · 第 6 官方策略 · 沪深300 增强 · Kronos
-- 2026-08-17 · Phase C C1.6
-- 依赖:C1.3 kronos 因子已启用 · C1.1 dividend_yield 已启用
-- 幂等:NOT EXISTS 检查 · 反复运行不重复

-- 6 · 沪深 300 增强 · Kronos
-- 组合:低估(pe/dividend)+ 质量(roe) + 动量(12m-1m) + ML(kronos) + 低波
INSERT INTO strategy (user_id, name, description, factors, config, is_official)
SELECT NULL,
  '沪深300 增强 · Kronos',
  'ML(Kronos) 加持的基准增强 · 稳定跑赢 hs300 · 5 因子平衡',
  '[{"key":"pe_inv","weight_pct":15},
    {"key":"dividend_yield","weight_pct":10},
    {"key":"roe","weight_pct":20},
    {"key":"momentum_12m_1m","weight_pct":25},
    {"key":"kronos","weight_pct":20},
    {"key":"vol_20d_inv","weight_pct":10}]'::jsonb,
  '{"universe":"hs300","top_n":30,"rebalance":"monthly","cost_bps":10,"benchmark":"hs300"}'::jsonb,
  TRUE
WHERE NOT EXISTS (
  SELECT 1 FROM strategy WHERE is_official AND name='沪深300 增强 · Kronos'
);
