-- ============================================================================
-- 0058_broker_account_readonly_oauth.sql
-- 阶段 M4：用户单独授权的券商只读账户连接器（OAuth AES-256-GCM 凭证托管）与手工交割单导入表
-- 铁律：严禁写 BEGIN/COMMIT，只做幂等加法（CREATE TABLE / ADD COLUMN / CREATE INDEX IF NOT EXISTS）
-- ============================================================================

-- 1. 用户级券商 OAuth 只读授权凭证托管表（Token 强制 AES-256-GCM 加密存储，绝不明文落库）
CREATE TABLE IF NOT EXISTS broker_oauth_authorization (
    user_id             TEXT PRIMARY KEY,                      -- 关联当前登录用户标识
    broker_id           VARCHAR(64) NOT NULL DEFAULT 'galaxy_broker',
    broker_name         VARCHAR(128) NOT NULL DEFAULT '中国银河证券官方只读通道',
    account_id          VARCHAR(64) NOT NULL DEFAULT 'GZ-88001928',
    account_masked      VARCHAR(64) NOT NULL DEFAULT '8800****1928',
    token_cipher        TEXT,                                  -- AES-256-GCM 密文 base64(nonce[12] || ciphertext_with_tag)
    token_masked        VARCHAR(64),                           -- 脱敏展示掩码，如 ****9f2a
    token_fingerprint   VARCHAR(32),                           -- SHA-256 指纹前缀（不含任何明文）
    encryption_alg      VARCHAR(32) NOT NULL DEFAULT 'AES-256-GCM',
    scopes_json         JSONB NOT NULL DEFAULT '["account.read","summary.read","positions.read","trades.read"]'::jsonb,
    status              VARCHAR(32) NOT NULL DEFAULT 'unauthorized', -- unauthorized / authorized / revoked / expired
    authorized_at       TIMESTAMPTZ,
    expires_at          TIMESTAMPTZ,
    revoked_at          TIMESTAMPTZ,
    revoke_reason       TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_broker_oauth_status
    ON broker_oauth_authorization(status, updated_at DESC);

-- 2. 未授权用户手工导入交割单与持仓快照表（Excel/CSV 兜底通道）
CREATE TABLE IF NOT EXISTS broker_manual_import_batch (
    batch_id            VARCHAR(64) PRIMARY KEY,
    user_id             TEXT NOT NULL,
    import_type         VARCHAR(32) NOT NULL DEFAULT 'settlement_csv', -- settlement_csv / positions_csv / excel_xlsx
    filename            TEXT NOT NULL DEFAULT 'manual_import.csv',
    positions_json      JSONB NOT NULL DEFAULT '[]'::jsonb,
    trades_json         JSONB NOT NULL DEFAULT '[]'::jsonb,
    risk_summary_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
    row_count           INT NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_broker_manual_import_user
    ON broker_manual_import_batch(user_id, created_at DESC);
