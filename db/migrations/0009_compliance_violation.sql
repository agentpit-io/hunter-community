-- 合规改写命中记录 · 供后续 fine-tune 数据 + metrics
CREATE TABLE IF NOT EXISTS compliance_violation_log (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID,
    session_id VARCHAR(64),
    violations TEXT[],               -- 命中的词/规则列表
    original_text TEXT,               -- 原始 LLM 输出(截 2000 字)
    fixed_text TEXT,                  -- 改写后
    model VARCHAR(64),                -- gemini-3.5-flash 等
    mode VARCHAR(16),                 -- strict / permissive / off
    occurred_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cvl_occurred ON compliance_violation_log(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_cvl_user ON compliance_violation_log(user_id, occurred_at DESC);
