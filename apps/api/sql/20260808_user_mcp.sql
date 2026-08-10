-- 用户自定义 MCP 组件 · P0 MVP
-- 2026-08-08 · 用户在 /mcp-config 页注册第三方 MCP (Polygon/AlphaVantage/自研)
-- Bridge MCP (hunter_user_mcp.py) 按 user_id 聚合暴露给 opencode LLM
-- 详见 doc/codex/自定义MCP/01-方案总纲.md

BEGIN;

-- ── 表 1 · 用户注册的 MCP 服务器 ──
CREATE TABLE IF NOT EXISTS user_mcp_registrations (
  id            BIGSERIAL   PRIMARY KEY,
  user_id       TEXT        NOT NULL,
  name          TEXT        NOT NULL,                   -- "Polygon 美股行情"
  slug          VARCHAR(32) NOT NULL,                   -- "polygon" · tool 命名空间前缀
  transport     VARCHAR(16) NOT NULL,                   -- 'sse' | 'http'（P0 · stdio 留 P1）
  endpoint      TEXT        NOT NULL,                   -- https://mcp.polygon.io/sse
  headers       JSONB       NOT NULL DEFAULT '{}'::jsonb, -- 附加 headers（明文 · 不含 auth）
  api_key_enc   TEXT,                                    -- AES-256-GCM base64 (nonce|ciphertext|tag)
  api_key_hint  VARCHAR(12) NOT NULL DEFAULT '',        -- 末 4 位 "****xxxx" 供 UI 回显
  enabled       BOOLEAN     NOT NULL DEFAULT TRUE,
  timeout_ms    INT         NOT NULL DEFAULT 15000,
  last_ok_at    TIMESTAMPTZ,
  last_err      TEXT,
  call_count    BIGINT      NOT NULL DEFAULT 0,
  error_count   BIGINT      NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_umr_user_slug
  ON user_mcp_registrations(user_id, slug);
CREATE INDEX IF NOT EXISTS idx_umr_user_enabled
  ON user_mcp_registrations(user_id) WHERE enabled;

-- ── 表 2 · tools 缓存（避免每次 list_tools 都远程查）──
CREATE TABLE IF NOT EXISTS user_mcp_tools_cache (
  mcp_id     BIGINT      PRIMARY KEY REFERENCES user_mcp_registrations(id) ON DELETE CASCADE,
  tools      JSONB       NOT NULL,                       -- [{name,description,inputSchema}]
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 表 3 · 调用日志（成本 + 滥用监控）──
CREATE TABLE IF NOT EXISTS user_mcp_call_log (
  id           BIGSERIAL   PRIMARY KEY,
  user_id      TEXT        NOT NULL,
  mcp_id       BIGINT      NOT NULL,
  tool_name    VARCHAR(64) NOT NULL,
  status       VARCHAR(8)  NOT NULL,                     -- 'ok' | 'err' | 'timeout'
  duration_ms  INT,
  error_code   VARCHAR(32),
  ts           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_umcl_user_ts ON user_mcp_call_log(user_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_umcl_mcp_ts  ON user_mcp_call_log(mcp_id, ts DESC);

COMMIT;
