-- ============================================================================
-- 对话框多格式文件导入（PDF / Excel / Word / Markdown / TXT）存储表
-- 幂等执行：支持 SaaS (hermes DB) 与 hunter-community
-- 严禁写 BEGIN/COMMIT，只做加法（CREATE TABLE / ADD COLUMN / CREATE INDEX IF NOT EXISTS）
-- ============================================================================

CREATE TABLE IF NOT EXISTS chat_uploaded_files (
    file_id           VARCHAR(64) PRIMARY KEY,               -- 格式: file_<16位hex>
    user_id           TEXT NOT NULL DEFAULT '',              -- 上传用户 ID（严格隔离）
    user_key          TEXT NOT NULL DEFAULT '',              -- 兼容 user_key 字段别名
    session_id        VARCHAR(128) NOT NULL DEFAULT '',      -- 绑定的对话 session_id
    filename          VARCHAR(512) NOT NULL,                 -- 原始文件名，如 "贵州茅台2025年报.pdf"
    file_ext          VARCHAR(16) NOT NULL,                  -- 归一化扩展名: pdf / xlsx / xls / csv / docx / md / txt
    mime_type         VARCHAR(128) NOT NULL DEFAULT '',
    file_size_bytes   BIGINT NOT NULL DEFAULT 0,             -- 原始字节大小
    file_size         BIGINT NOT NULL DEFAULT 0,             -- 兼容 file_size 字段别名
    file_sha256       VARCHAR(64) NOT NULL DEFAULT '',       -- 文件内容 SHA-256（用于同一用户秒传去重）
    storage_path      TEXT NOT NULL DEFAULT '',              -- 磁盘存储相对路径
    parse_status      VARCHAR(32) NOT NULL DEFAULT 'ready',  -- pending / parsing / ready / error / failed
    parse_error       TEXT,                                  -- 失败原因（如加密PDF、损坏文件）
    error_message     TEXT,                                  -- 兼容 error_message 字段别名
    total_pages       INT NOT NULL DEFAULT 0,                -- PDF总页数 / Excel总Sheet数 / Word章节数
    page_count        INT NOT NULL DEFAULT 0,                -- 兼容 page_count 字段别名
    total_chars       INT NOT NULL DEFAULT 0,                -- 提取出的总字符数
    char_count        INT NOT NULL DEFAULT 0,                -- 兼容 char_count 字段别名
    table_count       INT NOT NULL DEFAULT 0,                -- 提取出的结构化表格总数
    ocr_page_count    INT NOT NULL DEFAULT 0,                -- 走 L2 视觉/OCR 兜底的页数
    inject_mode       VARCHAR(32) NOT NULL DEFAULT 'full',   -- full / paged / summary_and_tools
    toc_json          JSONB NOT NULL DEFAULT '[]'::jsonb,    -- 自动提取的目录树 [{"title":"...","page":1}]
    detected_stocks   JSONB NOT NULL DEFAULT '[]'::jsonb,    -- 识别出的关联股票 [{"code":"600519","name":"贵州茅台","market":"CN_A","mentions":25}]
    related_stocks    JSONB NOT NULL DEFAULT '[]'::jsonb,    -- 兼容 related_stocks 字段别名
    full_markdown     TEXT NOT NULL DEFAULT '',              -- 完整解析 Markdown 缓存
    summary_md        TEXT NOT NULL DEFAULT '',              -- 长文档黄金摘要包（目录+前几页核心表）或短文档全文缓存
    summary_markdown  TEXT NOT NULL DEFAULT '',              -- 兼容 summary_markdown 字段别名
    extra_meta        JSONB NOT NULL DEFAULT '{}'::jsonb,    -- 格式特定元数据（如 Excel 的各 Sheet 行列统计、策略规则草稿等）
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at        TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 days')
);

CREATE INDEX IF NOT EXISTS idx_chat_files_session ON chat_uploaded_files(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_files_user_sha ON chat_uploaded_files(user_id, file_sha256);
CREATE INDEX IF NOT EXISTS idx_chat_files_expires ON chat_uploaded_files(expires_at);

-- 分页/分片内容表（用于 >3 万字的长年报按页翻阅与关键词搜索）
CREATE TABLE IF NOT EXISTS chat_uploaded_file_pages (
    id                BIGSERIAL PRIMARY KEY,
    file_id           VARCHAR(64) NOT NULL REFERENCES chat_uploaded_files(file_id) ON DELETE CASCADE,
    page_no           INT NOT NULL,                          -- 页码（从 1 开始；Excel 则为 Sheet 序号）
    page_title        VARCHAR(256) NOT NULL DEFAULT '',      -- 页内章节标题或 Sheet 名称
    content_md        TEXT NOT NULL DEFAULT '',              -- 该页完整 Markdown（含还原后的 Markdown 表格）
    page_markdown     TEXT NOT NULL DEFAULT '',              -- 兼容 page_markdown 字段别名
    char_count        INT NOT NULL DEFAULT 0,
    table_count       INT NOT NULL DEFAULT 0,                -- 该页包含的表格数
    has_table         BOOLEAN NOT NULL DEFAULT FALSE,        -- 是否包含财务/数据表格
    used_ocr          BOOLEAN NOT NULL DEFAULT FALSE,        -- 是否使用了 OCR / 视觉提取
    is_ocr            BOOLEAN NOT NULL DEFAULT FALSE,        -- 兼容 is_ocr 字段别名
    UNIQUE(file_id, page_no)
);

CREATE INDEX IF NOT EXISTS idx_chat_file_pages_lookup ON chat_uploaded_file_pages(file_id, page_no);
