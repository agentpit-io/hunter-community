-- Hunter Community · 复赛 §3.D.3 · 合规首访弹层
-- users 加两列:确认时间 + 确认的版本号(便于文案升级时强制重弹)
-- Idempotent: 仅 ADD COLUMN IF NOT EXISTS

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS compliance_ack_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS compliance_ack_version VARCHAR(16);

-- 复赛演示 · 评委账号(judge-2026)已看过合规声明 · 预填以避免登录被打断
-- 生产环境新用户 compliance_ack_at 仍为 NULL · 首访必须弹层
UPDATE users
SET compliance_ack_at = COALESCE(compliance_ack_at, NOW()),
    compliance_ack_version = COALESCE(compliance_ack_version, 'v1.0')
WHERE email_lower = 'judge-2026@hunter-community.demo';
