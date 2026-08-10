-- Sprint H · Kronos 预测报告持久化(与 chat_debate.reports 同构)
-- 目的: 用户切换/刷新 session 后仍能看到之前的 kpred 报告
--
-- 关键点:
--   · task_id 是 kpd_<12hex> · 全局唯一
--   · session_id 关联 opencode session · 加载时按此拉取
--   · content_html 存整份 HTML 报告(约 15-20 KB)
--   · summary_md 存 chat 消息流的短摘要

CREATE SCHEMA IF NOT EXISTS chat_kpred;

CREATE TABLE IF NOT EXISTS chat_kpred.reports (
    id                BIGSERIAL PRIMARY KEY,
    task_id           TEXT NOT NULL UNIQUE,          -- kpd_<12hex>
    user_id           TEXT NOT NULL,
    session_id        TEXT,                           -- opencode session · 可空
    stock_code        TEXT NOT NULL,
    stock_name        TEXT NOT NULL,
    days              INT NOT NULL,
    composite_score   INT NOT NULL,                   -- -100 ~ +100 (score * 100)
    rating            TEXT NOT NULL,                  -- 偏多/偏空/中性观望 等
    adj_return_pct    NUMERIC(6, 2),                  -- 调整后预期收益
    content_html      TEXT NOT NULL,                  -- 完整 HTML 报告
    summary_md        TEXT NOT NULL,                  -- chat 流用的摘要
    question          TEXT,                           -- 用户原始问题
    elapsed_sec       INT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 按用户时间倒序 · 未来若做"我的 kpred 列表"页
CREATE INDEX IF NOT EXISTS ix_kpred_reports_user_time
    ON chat_kpred.reports (user_id, created_at DESC);

-- session 加载时按 session_id 拉本会话所有 kpred
CREATE INDEX IF NOT EXISTS ix_kpred_reports_session
    ON chat_kpred.reports (session_id, created_at)
    WHERE session_id IS NOT NULL;
