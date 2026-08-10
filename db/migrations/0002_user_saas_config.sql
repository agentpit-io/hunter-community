-- Hunter Community · P4 · per-user optional SaaS accelerator config
-- Idempotent · safe to re-run on live DB

CREATE TABLE IF NOT EXISTS user_saas_config (
  user_id        UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  data_url       TEXT,
  data_key_enc   TEXT,   -- AES-256-GCM · nonce||ciphertext, base64
  llm_url        TEXT,
  llm_key_enc    TEXT,
  llm_model      VARCHAR(120),
  kronos_url     TEXT,
  kronos_key_enc TEXT,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
