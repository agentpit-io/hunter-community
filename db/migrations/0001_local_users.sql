-- Hunter Community · P3 · local user + session + invite tables
-- Idempotent: safe to run on existing DB and on empty postgres via docker-entrypoint-initdb.d

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email          VARCHAR(255) UNIQUE NOT NULL,
  email_lower    VARCHAR(255) UNIQUE NOT NULL,
  pw_hash        TEXT NOT NULL,
  role           VARCHAR(20) NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin')),
  display_name   VARCHAR(80),
  avatar_url     TEXT,
  status         VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  email_verified BOOLEAN NOT NULL DEFAULT FALSE,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_login     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_users_email_lower ON users(email_lower);

CREATE TABLE IF NOT EXISTS user_sessions (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  refresh_hash TEXT NOT NULL,
  expires_at   TIMESTAMPTZ NOT NULL,
  user_agent   TEXT,
  ip           INET,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  revoked_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_user_sessions_user
    ON user_sessions(user_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_user_sessions_refresh_hash
    ON user_sessions(refresh_hash);

CREATE TABLE IF NOT EXISTS invite_codes (
  code       VARCHAR(64) PRIMARY KEY,
  created_by UUID REFERENCES users(id),
  used_by    UUID REFERENCES users(id),
  used_at    TIMESTAMPTZ,
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
