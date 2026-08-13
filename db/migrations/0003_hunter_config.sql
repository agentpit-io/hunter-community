-- 0003 · hunter_config · instance-wide key/value for the Hunter platform key
--
-- Community Edition chats with your own LLM key, but the tools and SKILLs run
-- on Hunter's servers and need a key issued by us (free · hunter.agentpit.io/dev/api-keys).
--
-- The key can come from .env (HUNTER_API_KEY) or be pasted in the UI; the UI
-- path stores it here, AES-256-GCM encrypted with a key derived from JWT_SECRET
-- (same scheme as user_saas_config in 0002 · see apps/api/app/utils/crypto.py).
--
-- Instance-wide, not per-user: this is software you run on your own machine and
-- the data-source provider is a process-level singleton. See
-- apps/api/app/services/hunter_key.py for the resolution order.
--
-- The API applies this DDL idempotently at first use, so running this file is
-- optional · it exists so the schema is reviewable in one place.

CREATE TABLE IF NOT EXISTS hunter_config (
  k          VARCHAR(64) PRIMARY KEY,
  v          TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE hunter_config IS
  'Instance-wide settings. Row hunter_api_key_enc holds the encrypted Hunter platform key.';
