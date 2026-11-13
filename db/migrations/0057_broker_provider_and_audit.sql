-- ============================================================================
-- 0057_broker_provider_and_audit.sql
-- 阶段 M1：合作证券公司数据源注册表、公告页码索引表与券商 MCP 调用合规审计日志表
-- 铁律：严禁写 BEGIN/COMMIT，只做幂等加法（CREATE TABLE / ADD COLUMN / CREATE INDEX IF NOT EXISTS）
-- ============================================================================

-- 1. 合作券商与备用数据源注册表
CREATE TABLE IF NOT EXISTS broker_provider_registry (
    provider_id         VARCHAR(64) PRIMARY KEY,               -- 数据源唯一标识，如 galaxy_broker / akshare_public_fallback
    provider_name       VARCHAR(128) NOT NULL,                 -- 中文名称，如 "中国银河证券官方数据源"
    is_official_broker  BOOLEAN NOT NULL DEFAULT TRUE,         -- 是否为合作券商官方源
    priority_order      INT NOT NULL DEFAULT 10,               -- 主备路由优先级（越小越优先）
    supported_markets   JSONB NOT NULL DEFAULT '["CN_A","HK","US"]'::jsonb,
    supported_domains   JSONB NOT NULL DEFAULT '["security","market","disclosure","fundamental"]'::jsonb,
    gateway_url         TEXT NOT NULL DEFAULT '',
    license_scope       VARCHAR(64) NOT NULL DEFAULT 'authorized_user_readonly',
    allow_fallback      BOOLEAN NOT NULL DEFAULT TRUE,         -- 主源超时或未覆盖时是否允许降级至备用源
    health_status       VARCHAR(32) NOT NULL DEFAULT 'ok',     -- ok / degraded / down
    last_checked_at     TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_broker_provider_priority
    ON broker_provider_registry(priority_order ASC, is_official_broker DESC);

-- 2. 券商接口与 MCP 调用全量合规审计留痕表（满足证券公司科技与合规审计要求）
CREATE TABLE IF NOT EXISTS broker_audit_log (
    id                  BIGSERIAL PRIMARY KEY,
    trace_id            VARCHAR(64) NOT NULL DEFAULT '',       -- 调用追踪 ID
    user_id             TEXT NOT NULL DEFAULT 'anonymous',     -- 当前会话用户标识
    session_id          VARCHAR(128) NOT NULL DEFAULT '',      -- 对话或工作流会话 ID
    mcp_server          VARCHAR(64) NOT NULL,                  -- MCP 服务名，如 broker_market / broker_fundamental
    tool_name           VARCHAR(64) NOT NULL,                  -- 工具名，如 get_quote / get_financial_statements
    market              VARCHAR(16) NOT NULL DEFAULT 'CN_A',   -- 市场代码：CN_A / HK / US
    symbol              VARCHAR(32),                           -- 证券代码，如 600036.SH
    provider_id         VARCHAR(64) NOT NULL,                  -- 实际响应的数据源标识
    is_fallback         BOOLEAN NOT NULL DEFAULT FALSE,        -- 是否触发了主备降级
    fallback_reason     TEXT,                                  -- 降级原因说明
    as_of_ts            TIMESTAMPTZ,                           -- 行情/业务时点（as_of）
    published_at_ts     TIMESTAMPTZ,                           -- 披露时点（published_at，用于 PIT 核查）
    retrieved_at_ts     TIMESTAMPTZ NOT NULL DEFAULT NOW(),    -- 实际抓取时间（retrieved_at）
    license_scope       VARCHAR(64) NOT NULL DEFAULT 'authorized_user_readonly',
    response_bytes      INT NOT NULL DEFAULT 0,                -- 返回字节数（经过 16KB 大小闸后）
    size_guarded        BOOLEAN NOT NULL DEFAULT FALSE,        -- 是否触发了 16KB 大小闸裁剪
    latency_ms          NUMERIC(10, 2) NOT NULL DEFAULT 0,     -- 调用耗时（毫秒）
    status_code         VARCHAR(32) NOT NULL DEFAULT 'ok',     -- ok / partial / degraded / error / blocked
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_broker_audit_user_time
    ON broker_audit_log(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_broker_audit_mcp_tool
    ON broker_audit_log(mcp_server, tool_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_broker_audit_symbol
    ON broker_audit_log(symbol, created_at DESC);

-- 3. 上市公司公告与 PDF 页码溯源缓存表（支持 Point-in-Time 防偷看查询）
CREATE TABLE IF NOT EXISTS broker_announcement_cache (
    announcement_id     VARCHAR(64) PRIMARY KEY,               -- 公告唯一标识，如 ANN-20261028-600036-Q3
    symbol              VARCHAR(32) NOT NULL,                  -- 证券代码，如 600036.SH
    market              VARCHAR(16) NOT NULL DEFAULT 'CN_A',
    title               TEXT NOT NULL,
    category            VARCHAR(64) NOT NULL DEFAULT '定期报告',
    published_at        TIMESTAMPTZ NOT NULL,                  -- 公告正式公开披露时间（PIT 过滤基准）
    total_pages         INT NOT NULL DEFAULT 1,
    pdf_url             TEXT NOT NULL DEFAULT '',
    summary_text        TEXT NOT NULL DEFAULT '',
    pages_json          JSONB NOT NULL DEFAULT '[]'::jsonb,    -- [{"page_no":1,"page_title":"...","content_md":"..."}]
    provider_id         VARCHAR(64) NOT NULL DEFAULT 'galaxy_broker',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_broker_ann_symbol_pub
    ON broker_announcement_cache(symbol, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_broker_ann_category_pub
    ON broker_announcement_cache(category, published_at DESC);
