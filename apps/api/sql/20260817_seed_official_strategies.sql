-- B5 · Seed 5 官方策略(展示 16 因子体系)
-- 2026-08-17 · Phase B B5
-- 策略配置见 doc/开源hunter-community/参考/11量化策略/quant-strategy-tech-plan.md §6

-- 1 · 价值 + 动量(已存在 · id=1) → 保留

-- 2 · 全能蓝筹(6 因子 · 价值/质量/动量平衡)
INSERT INTO strategy (user_id, name, description, factors, config, is_official)
SELECT NULL, '全能蓝筹', '价值/质量/动量三驾马车 · 稳健跑赢 hs300',
  '[{"key":"pe_inv","weight_pct":20},{"key":"pb_inv","weight_pct":10},
    {"key":"roe","weight_pct":20},{"key":"gross_margin","weight_pct":10},
    {"key":"momentum_12m_1m","weight_pct":25},{"key":"momentum_6m","weight_pct":15}]'::jsonb,
  '{"universe":"hs300","top_n":20,"rebalance":"monthly","cost_bps":10,"benchmark":"hs300"}'::jsonb,
  TRUE
WHERE NOT EXISTS (SELECT 1 FROM strategy WHERE is_official AND name='全能蓝筹');

-- 3 · 高股息防御(质量 + 低估 · 熊市套利)
INSERT INTO strategy (user_id, name, description, factors, config, is_official)
SELECT NULL, '低估防御', '低估值 + 低杠杆 + 高毛利 · 抗跌型',
  '[{"key":"pe_inv","weight_pct":25},{"key":"pb_inv","weight_pct":20},
    {"key":"gross_margin","weight_pct":20},{"key":"debt_ratio_inv","weight_pct":20},
    {"key":"vol_20d_inv","weight_pct":15}]'::jsonb,
  '{"universe":"hs300","top_n":15,"rebalance":"monthly","cost_bps":10,"benchmark":"hs300"}'::jsonb,
  TRUE
WHERE NOT EXISTS (SELECT 1 FROM strategy WHERE is_official AND name='低估防御');

-- 4 · 成长动能(纯动量 + 成长)
INSERT INTO strategy (user_id, name, description, factors, config, is_official)
SELECT NULL, '成长动能', '成长股 + 强动量 · 牛市加速器',
  '[{"key":"revenue_growth_yoy","weight_pct":20},{"key":"earnings_growth_yoy","weight_pct":25},
    {"key":"momentum_6m","weight_pct":20},{"key":"momentum_12m_1m","weight_pct":20},
    {"key":"ma_align","weight_pct":15}]'::jsonb,
  '{"universe":"hs300","top_n":15,"rebalance":"monthly","cost_bps":10,"benchmark":"hs300"}'::jsonb,
  TRUE
WHERE NOT EXISTS (SELECT 1 FROM strategy WHERE is_official AND name='成长动能');

-- 5 · 技术反转(短反转 + 超卖)
INSERT INTO strategy (user_id, name, description, factors, config, is_official)
SELECT NULL, '技术反转', 'RSI 超卖 + 1M 短反转 + MACD 启动 · 短线交易',
  '[{"key":"rsi","weight_pct":30},{"key":"momentum_1m","weight_pct":25},
    {"key":"macd","weight_pct":25},{"key":"candle_5d","weight_pct":10},
    {"key":"vol_20d_inv","weight_pct":10}]'::jsonb,
  '{"universe":"hs300","top_n":10,"rebalance":"monthly","cost_bps":15,"benchmark":"hs300"}'::jsonb,
  TRUE
WHERE NOT EXISTS (SELECT 1 FROM strategy WHERE is_official AND name='技术反转');
