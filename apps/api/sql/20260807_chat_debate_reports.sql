-- 多专家辩论报告持久化 · Sprint B
-- 目的:让用户刷新 chat 页后仍能看到之前跑过的辩论报告
--
-- 关键点:
--   · task_id 是 dbg_<12hex> · 全局唯一 · 可猜性极低
--   · session_id 关联 opencode session · 载入时按此拉取本会话所有报告
--   · content_md 存整份 markdown · 方便 Artifact 直接引用
--   · report_json 存决策元信息 · 供 admin / 未来分析

CREATE SCHEMA IF NOT EXISTS chat_debate;

CREATE TABLE IF NOT EXISTS chat_debate.reports (
    id           BIGSERIAL PRIMARY KEY,
    task_id      TEXT NOT NULL UNIQUE,           -- dbg_<12hex>
    user_id      TEXT NOT NULL,
    session_id   TEXT,                            -- opencode session id · 可空(无 session 场景)
    stock_code   TEXT NOT NULL,
    stock_name   TEXT NOT NULL,
    decision     TEXT NOT NULL,                   -- BUY / HOLD / SELL
    confidence   NUMERIC(4,2) NOT NULL,           -- 0.00 - 1.00
    content_md   TEXT NOT NULL,                   -- 完整 markdown 报告
    report_json  JSONB DEFAULT '{}'::jsonb,       -- {phase_meta, tokens, elapsed_sec, ...}
    elapsed_sec  INT,
    question     TEXT,                            -- 用户原始问题
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 用户 · 时间倒序 (列表)
CREATE INDEX IF NOT EXISTS ix_debate_reports_user_time
    ON chat_debate.reports (user_id, created_at DESC);

-- session 加载时按 session_id 拉本会话所有辩论
CREATE INDEX IF NOT EXISTS ix_debate_reports_session
    ON chat_debate.reports (session_id, created_at)
    WHERE session_id IS NOT NULL;
