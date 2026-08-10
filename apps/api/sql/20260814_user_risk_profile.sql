-- 持仓建议 · Sprint 1 · 用户风险画像 & 现金余额
-- 每个 user 一行 · 组合建议 / 情景模拟 tool 读取后应用约束
-- 参考：doc/codex/持仓建议/05-Chat-Skill-开发计划.md §4 Sprint 1

CREATE TABLE IF NOT EXISTS user_risk_profile (
    user_id         TEXT         PRIMARY KEY,
    cash_balance    NUMERIC(14,2) NOT NULL DEFAULT 0,     -- CNY 可用现金
    risk_tolerance  TEXT         NOT NULL DEFAULT 'medium', -- low / medium / high
    max_position    NUMERIC(4,3) NOT NULL DEFAULT 0.25,   -- 单票上限 0.05-0.40
    max_hk_ratio    NUMERIC(4,3) NOT NULL DEFAULT 0.40,   -- 港股合计上限
    max_sector      NUMERIC(4,3) NOT NULL DEFAULT 0.40,   -- 单行业上限
    extra           JSONB        NOT NULL DEFAULT '{}',   -- max_margin / use_margin / focus_sectors
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  user_risk_profile IS
  '用户风险画像与现金余额 · portfolio_rebalance/portfolio_stress tool 依赖';
COMMENT ON COLUMN user_risk_profile.cash_balance IS
  'CNY 可用现金 · 组合总资产 = 持仓市值 + cash_balance';
COMMENT ON COLUMN user_risk_profile.max_position IS
  '单票权重上限 · 0.05-0.40 · 默认 0.25';
COMMENT ON COLUMN user_risk_profile.risk_tolerance IS
  'low / medium / high · low 会自动收窄 max_position 到 0.20';
