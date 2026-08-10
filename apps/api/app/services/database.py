import os
import psycopg2
from loguru import logger

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://hermes:Hermes2026DB!@localhost:5432/hermes")

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS stocks (
    code VARCHAR(10) PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    market VARCHAR(5) NOT NULL,
    exchange VARCHAR(5) NOT NULL,
    asset_type VARCHAR(8) NOT NULL DEFAULT 'stock',  -- stock / etf / fund
    enabled BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS quotes (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(10) NOT NULL,
    price NUMERIC(12,3),
    change_pct NUMERIC(8,4),
    change_amt NUMERIC(12,3),
    volume BIGINT,
    amount NUMERIC(20,2),
    high NUMERIC(12,3),
    low NUMERIC(12,3),
    open NUMERIC(12,3),
    prev_close NUMERIC(12,3),
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_quotes_code_ts ON quotes(code, ts DESC);

CREATE TABLE IF NOT EXISTS klines (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(10) NOT NULL,
    period VARCHAR(10) NOT NULL,
    open NUMERIC(12,3),
    high NUMERIC(12,3),
    low NUMERIC(12,3),
    close NUMERIC(12,3),
    volume BIGINT,
    ts DATE NOT NULL,
    UNIQUE(code, period, ts)
);

CREATE TABLE IF NOT EXISTS news (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(10),
    title TEXT NOT NULL,
    source VARCHAR(50),
    url TEXT,
    content TEXT,
    published_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_news_code ON news(code, fetched_at DESC);

CREATE TABLE IF NOT EXISTS push_tasks (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    template_id VARCHAR(40) NOT NULL,
    schedule_time VARCHAR(5) NOT NULL,
    content_type VARCHAR(40) NOT NULL,
    custom_content TEXT DEFAULT '',
    target_chat VARCHAR(100) NOT NULL DEFAULT '',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    last_run_date DATE,
    last_status VARCHAR(20),
    last_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_push_tasks_enabled ON push_tasks(enabled, schedule_time);

-- 持仓哨兵：买入逻辑卡片（用户为每只股票记录"为什么买"的原因，给异动归因用）
-- 一只股票一条 thesis；持仓字段全部选填，填了才进入持仓哨兵监控
CREATE TABLE IF NOT EXISTS price_alerts (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(50) NOT NULL,
    code VARCHAR(10) NOT NULL,
    label VARCHAR(100) NOT NULL DEFAULT '',
    condition_type VARCHAR(20) NOT NULL,
    threshold NUMERIC(12,3) NOT NULL,
    threshold2 NUMERIC(12,3),
    cooldown_minutes INTEGER NOT NULL DEFAULT 60,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    last_triggered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_price_alerts_user_code ON price_alerts(user_id, code, enabled);

CREATE TABLE IF NOT EXISTS position_thesis (
    code           VARCHAR(10) PRIMARY KEY REFERENCES stocks(code) ON DELETE CASCADE,
    shares         INTEGER,                                 -- 持仓数量（选填）
    cost_price     NUMERIC(12,3),                           -- 买入均价（选填）
    buy_date       DATE,                                    -- 买入日期（选填）
    thesis_text    TEXT NOT NULL DEFAULT '',                -- 买入逻辑文本（MVP 阶段不结构化）
    thesis_structured JSONB,                                -- V1 才填，MVP 留空
    status         VARCHAR(16) NOT NULL DEFAULT 'active',   -- active / archived / sold
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);

-- 在线分析（2026-05-23）：每次用户点「开始分析」生成一条快照，4 列 UI 内容固化
CREATE TABLE IF NOT EXISTS online_analysis_report (
    id              BIGSERIAL    PRIMARY KEY,
    user_id         INTEGER      NOT NULL DEFAULT 1,
    stock_code      VARCHAR(10)  NOT NULL,
    stock_name      VARCHAR(50),
    thesis_text     TEXT         NOT NULL,
    kill_conditions JSONB        NOT NULL,
    news_items          JSONB,
    naive_pipeline      JSONB,
    defense_pipeline    JSONB,
    final_conclusion    JSONB,
    thesis_status   VARCHAR(16),
    confidence      NUMERIC(3,2),
    poison_rate     NUMERIC(3,2),
    llm_tokens      INTEGER,
    cost_cny        NUMERIC(8,4),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    duration_ms     INTEGER,
    CHECK (thesis_status IN ('INTACT', 'WEAKENING', 'BROKEN', 'INSUFFICIENT', 'BUY', 'HOLD', 'SELL'))
);
CREATE INDEX IF NOT EXISTS idx_oar_user_time  ON online_analysis_report (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_oar_stock_time ON online_analysis_report (stock_code, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_oar_thesis_status ON online_analysis_report (thesis_status, created_at DESC);

CREATE TABLE IF NOT EXISTS feishu_bindings (
    user_id  VARCHAR(50) PRIMARY KEY,
    open_id  VARCHAR(100) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_feishu_bindings_open_id ON feishu_bindings(open_id);

CREATE TABLE IF NOT EXISTS market_signals (
    id              BIGSERIAL    PRIMARY KEY,
    signal_type     VARCHAR(30)  NOT NULL,
    scenario        VARCHAR(50),
    triggered_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    data            JSONB,
    portfolio_impact TEXT        DEFAULT '',
    push_sent       BOOLEAN      DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_ms_type_time ON market_signals(signal_type, triggered_at DESC);

CREATE TABLE IF NOT EXISTS signal_subscriptions (
    user_id     VARCHAR(100) NOT NULL,
    signal_type VARCHAR(30)  NOT NULL,
    enabled     BOOLEAN      NOT NULL DEFAULT TRUE,
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, signal_type)
);

-- 用户股票操作记忆（2026-06-29）：记录用户每次分析/预测/事件的行为信号，每只股票最多保留5条
CREATE TABLE IF NOT EXISTS user_stock_memory (
    id          BIGSERIAL    PRIMARY KEY,
    user_id     TEXT         NOT NULL,
    stock_code  TEXT         NOT NULL,
    stock_name  TEXT,
    event_type  TEXT         NOT NULL DEFAULT 'analysis',  -- analysis / kpred / event_analysis
    conclusion  TEXT,                                       -- BUY / HOLD / SELL (仅 analysis 有)
    confidence  FLOAT,
    extra_text  TEXT,                                       -- event_analysis 的事件描述
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_usm_user_stock ON user_stock_memory(user_id, stock_code, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usm_user_time  ON user_stock_memory(user_id, created_at DESC);

-- 用户偏好记忆（2026-06-29）：每用户一行，记录投资风格/风险偏好/关注板块等显式设置
CREATE TABLE IF NOT EXISTS user_preference (
    user_id          TEXT        PRIMARY KEY,
    investment_style TEXT        NOT NULL DEFAULT '',
    risk_tolerance   TEXT        NOT NULL DEFAULT '',
    holding_period   TEXT        NOT NULL DEFAULT '',
    focus_sectors    TEXT[]      NOT NULL DEFAULT '{}',
    market_scope     TEXT        NOT NULL DEFAULT 'A股',
    push_focus       TEXT        NOT NULL DEFAULT '',
    extra            JSONB       NOT NULL DEFAULT '{}',
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS signal_reports (
    id           BIGSERIAL    PRIMARY KEY,
    signal_id    BIGINT       NOT NULL REFERENCES market_signals(id),
    user_id      VARCHAR(100) NOT NULL,
    html_content TEXT         NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sr_signal_user ON signal_reports(signal_id, user_id);

-- AdventureX 展位活动（2026-07-17）：每个微信 openid 一行，两级奖励状态机
-- 状态流转：注册(registered_at) → 一级完成(level1_done_at) → 一级核销(level1_redeemed_at)
--                              → 二级完成(level2_done_at) → 二级核销(level2_redeemed_at)
CREATE TABLE IF NOT EXISTS ax_event (
    openid             TEXT        PRIMARY KEY,
    user_id            TEXT        NOT NULL DEFAULT '',
    email              TEXT        NOT NULL DEFAULT '',
    nickname           TEXT        NOT NULL DEFAULT '',
    registered_at      TIMESTAMPTZ,
    level1_stock_code  TEXT        NOT NULL DEFAULT '',
    level1_stock_name  TEXT        NOT NULL DEFAULT '',
    level1_report_id   BIGINT,
    level1_done_at     TIMESTAMPTZ,
    level1_code        TEXT        NOT NULL DEFAULT '',   -- 体验礼核销码 AX-XXXX
    level1_redeemed_at TIMESTAMPTZ,
    level1_redeemed_by TEXT        NOT NULL DEFAULT '',   -- 核销操作人 email
    level2_positions   JSONB       NOT NULL DEFAULT '[]', -- [{code,name,cost_price,shares}]
    level2_done_at     TIMESTAMPTZ,
    level2_code        TEXT        NOT NULL DEFAULT '',   -- 股民礼核销码 AX-XXXX
    level2_redeemed_at TIMESTAMPTZ,
    level2_redeemed_by TEXT        NOT NULL DEFAULT '',
    member_months      INT         NOT NULL DEFAULT 0,    -- 会员月数：1/3 取高不叠加
    member_expires_at  TIMESTAMPTZ,
    -- V4 (2026-07-22)：二级解锁改为"完成 2 项基础 + 2 项随机 = 4 项功能体验"（按 openid hash 稳定分配）
    features_used        JSONB       NOT NULL DEFAULT '[]',  -- 已体验 feature_id 集合
    features_updated_at  TIMESTAMPTZ,
    level1_email_sent_at TIMESTAMPTZ,                        -- 分析报告邮件送达时间
    unlock_track         TEXT        NOT NULL DEFAULT 'features',  -- features(新)/positions(旧)
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ax_event_user  ON ax_event(user_id);
CREATE INDEX IF NOT EXISTS idx_ax_event_email ON ax_event(email);
CREATE INDEX IF NOT EXISTS idx_ax_event_unlock_track ON ax_event(unlock_track);

-- 投研助手 · LLM 对话（P1-P3 · 2026-07-24）
CREATE TABLE IF NOT EXISTS assistant_sessions (
    id                TEXT PRIMARY KEY,          -- sess_xxx (uuid hex prefix)
    user_id           TEXT NOT NULL,
    focus_stock_code  TEXT NOT NULL DEFAULT '',  -- 会话聚焦的股票（切股会新建 session）
    focus_stock_name  TEXT NOT NULL DEFAULT '',
    message_count     INT  NOT NULL DEFAULT 0,
    last_message_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- V2: 多轮上下文压缩（超 16 条消息触发）
    summary                    TEXT   NOT NULL DEFAULT '',
    summary_until_message_id   BIGINT NOT NULL DEFAULT 0,  -- 已压缩到哪条消息 id
    chat_mode_count            INT    NOT NULL DEFAULT 0,  -- 通用对话次数统计
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_assistant_sessions_user_time
    ON assistant_sessions(user_id, last_message_at DESC);

CREATE TABLE IF NOT EXISTS assistant_messages (
    id             BIGSERIAL PRIMARY KEY,
    session_id     TEXT NOT NULL REFERENCES assistant_sessions(id) ON DELETE CASCADE,
    role           TEXT NOT NULL,       -- 'user' | 'assistant'
    content        TEXT NOT NULL,
    intent         TEXT,                -- 仅 assistant 消息填
    suggested_mode TEXT,                -- 仅 assistant 消息填
    extra          JSONB,               -- 完整 LLM 输出 JSON
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_assistant_messages_session_time
    ON assistant_messages(session_id, created_at);
"""

def get_conn():
    return psycopg2.connect(DATABASE_URL)

_SEED_TASKS = """
INSERT INTO push_tasks (name, template_id, schedule_time, content_type, target_chat, enabled)
VALUES
  ('自选股早报',     'watchlist_morning', '08:00', 'watchlist_morning', '', TRUE),
  ('自选股异动追踪', 'fundflow_alert',    '14:30', 'fundflow_topn',     '', TRUE),
  ('自选股收盘总结', 'close_review',      '15:15', 'close_review',      '', TRUE)
ON CONFLICT DO NOTHING;
"""

# 客户实例默认 stocks 表为空，用户通过 UI"添加自选股"自助加。
# 现网历史 6 只演示股已经在 stocks 表里（INSERT ... ON CONFLICT DO NOTHING 不会删），影响仅限新部署。
# 如需恢复默认演示数据，环境变量 HERMES_SEED_DEFAULT_STOCKS=1 启用 fallback。
_DEFAULT_DEMO_STOCKS = [
    ("002595", "豪迈科技", "A",  "SZ"),
    ("000933", "神火股份", "A",  "SZ"),
    ("601899", "紫金矿业", "A",  "SH"),
    ("002001", "新和成",   "A",  "SZ"),
    ("02333",  "长城汽车", "HK", "HK"),
    ("01378",  "中国宏桥", "HK", "HK"),
]


async def init_db():
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(CREATE_TABLES)
        # 兼容旧表：补 enabled / asset_type 列
        cur.execute("ALTER TABLE stocks ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE")
        cur.execute("ALTER TABLE stocks ADD COLUMN IF NOT EXISTS asset_type VARCHAR(8) NOT NULL DEFAULT 'stock'")
        # 兼容旧 position_thesis 表（如果是从 V0 升级，可能缺新字段）
        cur.execute("ALTER TABLE position_thesis ADD COLUMN IF NOT EXISTS thesis_structured JSONB")
        cur.execute("ALTER TABLE position_thesis ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'active'")
        # 组合级建议 · 目标权重（0-100） · 空 = 用等权重
        cur.execute("ALTER TABLE position_thesis ADD COLUMN IF NOT EXISTS target_weight_pct NUMERIC(5,2)")
        # 用户风险画像 & 现金余额（2026-08 · 持仓建议 Sprint 1）
        # DDL 同 sql/20260814_user_risk_profile.sql
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_risk_profile (
              user_id         TEXT         PRIMARY KEY,
              cash_balance    NUMERIC(14,2) NOT NULL DEFAULT 0,
              risk_tolerance  TEXT         NOT NULL DEFAULT 'medium',
              max_position    NUMERIC(4,3) NOT NULL DEFAULT 0.25,
              max_hk_ratio    NUMERIC(4,3) NOT NULL DEFAULT 0.40,
              max_sector      NUMERIC(4,3) NOT NULL DEFAULT 0.40,
              extra           JSONB        NOT NULL DEFAULT '{}',
              updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute(_SEED_TASKS)
        if os.environ.get("HERMES_SEED_DEFAULT_STOCKS") == "1":
            for code, name, market, exchange in _DEFAULT_DEMO_STOCKS:
                cur.execute(
                    "INSERT INTO stocks (code, name, market, exchange) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (code, name, market, exchange),
                )
        cur.execute("ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS label VARCHAR(100) NOT NULL DEFAULT ''")
        # user_preference 兼容旧实例（老表缺列时补齐）
        cur.execute("ALTER TABLE user_preference ADD COLUMN IF NOT EXISTS investment_style TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE user_preference ADD COLUMN IF NOT EXISTS risk_tolerance TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE user_preference ADD COLUMN IF NOT EXISTS holding_period TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE user_preference ADD COLUMN IF NOT EXISTS focus_sectors TEXT[] NOT NULL DEFAULT '{}'")
        cur.execute("ALTER TABLE user_preference ADD COLUMN IF NOT EXISTS market_scope TEXT NOT NULL DEFAULT 'A股'")
        cur.execute("ALTER TABLE user_preference ADD COLUMN IF NOT EXISTS push_focus TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE user_preference ADD COLUMN IF NOT EXISTS extra JSONB NOT NULL DEFAULT '{}'")
        # V2 assistant_sessions 多轮压缩字段（兼容旧表）
        cur.execute("ALTER TABLE assistant_sessions ADD COLUMN IF NOT EXISTS summary TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE assistant_sessions ADD COLUMN IF NOT EXISTS summary_until_message_id BIGINT NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE assistant_sessions ADD COLUMN IF NOT EXISTS chat_mode_count INT NOT NULL DEFAULT 0")
        # stocks_catalog: 股票搜索字典(全 A 股 code-name 映射), 与用户自选表 stocks 无关
        # 冷启动优先从此表读, 避免依赖 akshare(GCP 有时被墙)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS stocks_catalog (
              code        TEXT PRIMARY KEY,
              name        TEXT NOT NULL,
              exchange    TEXT NOT NULL,
              market      TEXT NOT NULL DEFAULT 'A',
              symbol      TEXT NOT NULL,
              enabled     BOOLEAN NOT NULL DEFAULT TRUE,
              updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_stocks_catalog_name ON stocks_catalog (name)")
        # chat_session_owner: /chat 会话归属(用户隔离)
        # opencode 的 session 没有"用户"概念, GET /session 谁调都返回全部,
        # 归属关系放我们这层维护, BFF 据此过滤与鉴权。DDL 同 sql/20260806_chat_session_owner.sql
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_session_owner (
              session_id   TEXT        PRIMARY KEY,
              user_id      TEXT        NOT NULL,
              title        TEXT        NOT NULL DEFAULT '',
              created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              last_used_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              archived     BOOLEAN     NOT NULL DEFAULT FALSE
            )
        """)
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_cso_user
                       ON chat_session_owner (user_id, last_used_at DESC)
                       WHERE NOT archived""")
        # chat_user_skill: /chat 能力面板(用户自建能力 + 对内置能力的覆盖)
        # DDL 同 sql/20260806_chat_user_skill.sql
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_user_skill (
              id          BIGSERIAL   PRIMARY KEY,
              user_id     TEXT        NOT NULL,
              name        TEXT        NOT NULL,
              icon        TEXT        NOT NULL DEFAULT '⭐',
              prompt_tpl  TEXT        NOT NULL,
              enabled     BOOLEAN     NOT NULL DEFAULT TRUE,
              sort_order  INT         NOT NULL DEFAULT 0,
              builtin_key TEXT        NOT NULL DEFAULT '',
              created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_cus_user ON chat_user_skill (user_id, sort_order, id)")
        cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_cus_builtin
                       ON chat_user_skill (user_id, builtin_key) WHERE builtin_key <> ''""")
        # 用户自定义 MCP 组件 · 3 表 · DDL 同 sql/20260808_user_mcp.sql
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_mcp_registrations (
              id            BIGSERIAL   PRIMARY KEY,
              user_id       TEXT        NOT NULL,
              name          TEXT        NOT NULL,
              slug          VARCHAR(32) NOT NULL,
              transport     VARCHAR(16) NOT NULL,
              endpoint      TEXT        NOT NULL,
              headers       JSONB       NOT NULL DEFAULT '{}'::jsonb,
              api_key_enc   TEXT,
              api_key_hint  VARCHAR(12) NOT NULL DEFAULT '',
              enabled       BOOLEAN     NOT NULL DEFAULT TRUE,
              timeout_ms    INT         NOT NULL DEFAULT 15000,
              last_ok_at    TIMESTAMPTZ,
              last_err      TEXT,
              call_count    BIGINT      NOT NULL DEFAULT 0,
              error_count   BIGINT      NOT NULL DEFAULT 0,
              created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_umr_user_slug ON user_mcp_registrations(user_id, slug)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_umr_user_enabled ON user_mcp_registrations(user_id) WHERE enabled")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_mcp_tools_cache (
              mcp_id     BIGINT      PRIMARY KEY REFERENCES user_mcp_registrations(id) ON DELETE CASCADE,
              tools      JSONB       NOT NULL,
              fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_mcp_call_log (
              id           BIGSERIAL   PRIMARY KEY,
              user_id      TEXT        NOT NULL,
              mcp_id       BIGINT      NOT NULL,
              tool_name    VARCHAR(64) NOT NULL,
              status       VARCHAR(8)  NOT NULL,
              duration_ms  INT,
              error_code   VARCHAR(32),
              ts           TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_umcl_user_ts ON user_mcp_call_log(user_id, ts DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_umcl_mcp_ts  ON user_mcp_call_log(mcp_id, ts DESC)")
        # 用户记忆体:画像(结构化,可统计) + 浓缩记忆(JSONB,结构会演进) + 变更日志
        # DDL 同 sql/20260806_user_memory.sql
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_profile (
              user_id      TEXT        PRIMARY KEY,
              risk_style   TEXT        NOT NULL DEFAULT '',
              max_drawdown INT,
              horizon      TEXT        NOT NULL DEFAULT '',
              markets      TEXT[]      NOT NULL DEFAULT '{}',
              sectors      TEXT[]      NOT NULL DEFAULT '{}',
              cap_pref     TEXT        NOT NULL DEFAULT '',
              weight_order TEXT[]      NOT NULL DEFAULT '{}',
              verbosity    TEXT        NOT NULL DEFAULT '',
              taboos       TEXT[]      NOT NULL DEFAULT '{}',
              onboarded    BOOLEAN     NOT NULL DEFAULT FALSE,
              updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_memory (
              user_id       TEXT        PRIMARY KEY,
              memory        JSONB       NOT NULL DEFAULT '{}',
              session_count INT         NOT NULL DEFAULT 0,
              updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_memory_log (
              id         BIGSERIAL   PRIMARY KEY,
              user_id    TEXT        NOT NULL,
              session_id TEXT        NOT NULL DEFAULT '',
              before     JSONB,
              after      JSONB,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_uml_user ON user_memory_log (user_id, created_at DESC)")
        conn.commit()
        conn.close()
        logger.info("Database initialized")
    except Exception as e:
        logger.error("DB init failed: {}", e)


def get_stocks() -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT code, name, market, exchange, COALESCE(asset_type,'stock') FROM stocks WHERE enabled = TRUE ORDER BY code")
    rows = cur.fetchall()
    conn.close()
    return [{"code": r[0], "name": r[1], "market": r[2], "exchange": r[3], "asset_type": r[4]} for r in rows]


def add_stock(code: str, name: str, market: str, exchange: str, asset_type: str = "stock") -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO stocks (code, name, market, exchange, asset_type) VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (code) DO NOTHING RETURNING code",
        (code, name, market, exchange, asset_type),
    )
    result = cur.fetchone()
    conn.commit()
    conn.close()
    return result is not None


def remove_stock(code: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE stocks SET enabled = FALSE WHERE code = %s", (code,))
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────
# 持仓哨兵 · 买入逻辑卡片 CRUD
# ─────────────────────────────────────────────────────────────────────

def get_thesis(code: str) -> dict | None:
    """返回某只股票的 thesis 卡片，没填 thesis 返回 None。"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT code, shares, cost_price, buy_date, thesis_text, status, "
        "       created_at, updated_at "
        "FROM position_thesis WHERE code = %s",
        (code,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "code":       row[0],
        "shares":     row[1],
        "cost_price": float(row[2]) if row[2] is not None else None,
        "buy_date":   row[3].isoformat() if row[3] else None,
        "thesis_text": row[4] or "",
        "status":     row[5],
        "created_at": row[6].isoformat() if row[6] else None,
        "updated_at": row[7].isoformat() if row[7] else None,
    }


def list_stocks_with_thesis() -> list[dict]:
    """自选股管理页用：返回 enabled 自选股列表 + 各自的 thesis（没填的 thesis 字段为空）。"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT s.code, s.name, s.market, s.exchange, COALESCE(s.asset_type,'stock'),
               t.shares, t.cost_price, t.buy_date, COALESCE(t.thesis_text, ''), COALESCE(t.status, '')
        FROM stocks s
        LEFT JOIN position_thesis t ON t.code = s.code
        WHERE s.enabled = TRUE
        ORDER BY s.code
    """)
    rows = cur.fetchall()
    conn.close()
    return [{
        "code":         r[0],
        "name":         r[1],
        "market":       r[2],
        "exchange":     r[3],
        "asset_type":   r[4],
        "shares":       r[5],
        "cost_price":   float(r[6]) if r[6] is not None else None,
        "buy_date":     r[7].isoformat() if r[7] else None,
        "thesis_text":  r[8] or "",
        "has_thesis":   bool(r[8]),
        "status":       r[9],
    } for r in rows]


def upsert_thesis(code: str, thesis_text: str,
                  shares: int | None = None,
                  cost_price: float | None = None,
                  buy_date: str | None = None) -> bool:
    """新建 / 更新 thesis 卡片。code 必须已在 stocks 表。"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO position_thesis (code, shares, cost_price, buy_date, thesis_text, updated_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        ON CONFLICT (code) DO UPDATE SET
            shares      = EXCLUDED.shares,
            cost_price  = EXCLUDED.cost_price,
            buy_date    = EXCLUDED.buy_date,
            thesis_text = EXCLUDED.thesis_text,
            updated_at  = NOW()
        RETURNING code
        """,
        (code, shares, cost_price, buy_date, thesis_text),
    )
    ok = cur.fetchone() is not None
    conn.commit()
    conn.close()
    return ok


def delete_thesis(code: str) -> None:
    """清空某只股票的 thesis（保留 stocks 行）。"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM position_thesis WHERE code = %s", (code,))
    conn.commit()
    conn.close()


def hard_remove_stock(code: str) -> None:
    """彻底删除自选股（也会级联删 thesis，因为外键 ON DELETE CASCADE）。
    跟 remove_stock 的差别：remove_stock 只 enabled=FALSE 软删，UI 上还能看到；
    hard_remove_stock 直接物理删除。
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM stocks WHERE code = %s", (code,))
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────
# 多租户版 CRUD（按 user_id 隔离，2026-06）
# ─────────────────────────────────────────────────────────────────────

def get_stocks_by_user(user_id: str) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT code, name, market, exchange, COALESCE(asset_type,'stock') "
        "FROM stocks WHERE enabled = TRUE AND user_id = %s "
        "AND (market IS NULL OR market NOT IN ('US','HK')) "  # gm端(美港股)自选与A股端隔离
        "ORDER BY code",
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [{"code": r[0], "name": r[1], "market": r[2], "exchange": r[3], "asset_type": r[4]} for r in rows]


def get_all_stocks_by_user(user_id: str) -> list[dict]:
    """全市场自选（含 A / HK / US）· 供 watchlist_digest 等『组合级视图』使用。

    与 get_stocks_by_user 的区别：不过滤 market·返回用户全部自选（无论 A 股端还是
    美港股端加入）。原 get_stocks_by_user 用于 A 股专用界面（/watchlist），美港股
    有独立端 /api/gm/watchlist。但组合级 tool（自选股日报 / 组合建议）应包含所有
    市场，否则港股/美股仓位会被遗漏（详见 doc/codex/自定义MCP/04-watchlist_digest-覆盖港股修复.md）。
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT code, name, market, exchange, COALESCE(asset_type,'stock') "
        "FROM stocks WHERE enabled = TRUE AND user_id = %s "
        "ORDER BY market, code",
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [{"code": r[0], "name": r[1], "market": r[2], "exchange": r[3], "asset_type": r[4]} for r in rows]


def add_stock_by_user(code: str, name: str, market: str, exchange: str,
                      asset_type: str, user_id: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO stocks (code, name, market, exchange, asset_type, user_id) "
        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (code, user_id) DO NOTHING RETURNING code",
        (code, name, market, exchange, asset_type, user_id),
    )
    result = cur.fetchone()
    conn.commit()
    conn.close()
    return result is not None


def remove_stock_by_user(code: str, user_id: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE stocks SET enabled = FALSE WHERE code = %s AND user_id = %s", (code, user_id))
    conn.commit()
    conn.close()


def hard_remove_stock_by_user(code: str, user_id: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM stocks WHERE code = %s AND user_id = %s", (code, user_id))
    conn.commit()
    conn.close()


def get_thesis_by_user(code: str, user_id: str) -> dict | None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT code, shares, cost_price, buy_date, thesis_text, status, created_at, updated_at "
        "FROM position_thesis WHERE code = %s AND user_id = %s",
        (code, user_id),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "code":        row[0],
        "shares":      row[1],
        "cost_price":  float(row[2]) if row[2] is not None else None,
        "buy_date":    row[3].isoformat() if row[3] else None,
        "thesis_text": row[4] or "",
        "status":      row[5],
        "created_at":  row[6].isoformat() if row[6] else None,
        "updated_at":  row[7].isoformat() if row[7] else None,
    }


def list_stocks_with_thesis_by_user(user_id: str) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT s.code, s.name, s.market, s.exchange, COALESCE(s.asset_type,'stock'),
               t.shares, t.cost_price, t.buy_date, COALESCE(t.thesis_text,''), COALESCE(t.status,''),
               t.target_weight_pct
        FROM stocks s
        LEFT JOIN position_thesis t ON t.code = s.code AND t.user_id = s.user_id
        WHERE s.enabled = TRUE AND s.user_id = %s
          AND (s.market IS NULL OR s.market NOT IN ('US','HK'))
        ORDER BY s.code
    """, (user_id,))
    rows = cur.fetchall()
    conn.close()
    return [{
        "code":        r[0], "name": r[1], "market": r[2], "exchange": r[3], "asset_type": r[4],
        "shares":      r[5], "cost_price": float(r[6]) if r[6] else None,
        "buy_date":    r[7].isoformat() if r[7] else None,
        "thesis_text": r[8] or "", "has_thesis": bool(r[8]), "status": r[9],
        "target_weight_pct": float(r[10]) if r[10] is not None else None,
    } for r in rows]


def upsert_thesis_by_user(code: str, user_id: str, thesis_text: str,
                          shares: int | None = None, cost_price: float | None = None,
                          buy_date: str | None = None) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO position_thesis (code, user_id, shares, cost_price, buy_date, thesis_text, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (code, user_id) DO UPDATE SET
            shares      = EXCLUDED.shares,
            cost_price  = EXCLUDED.cost_price,
            buy_date    = EXCLUDED.buy_date,
            thesis_text = EXCLUDED.thesis_text,
            updated_at  = NOW()
        RETURNING code
    """, (code, user_id, shares, cost_price, buy_date, thesis_text))
    ok = cur.fetchone() is not None
    conn.commit()
    conn.close()
    return ok


def delete_thesis_by_user(code: str, user_id: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM position_thesis WHERE code = %s AND user_id = %s", (code, user_id))
    conn.commit()
    conn.close()


# ─── 飞书配置（多租户）───────────────────────────────────────────────

def get_feishu_config(user_id: str) -> dict | None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT app_id, app_secret, home_channel, enabled FROM user_feishu_config WHERE user_id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {"app_id": row[0], "app_secret": row[1], "home_channel": row[2], "enabled": row[3]}


def upsert_feishu_config(user_id: str, app_id: str, app_secret: str,
                         home_channel: str, enabled: bool = True):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO user_feishu_config (user_id, app_id, app_secret, home_channel, enabled, updated_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        ON CONFLICT (user_id) DO UPDATE SET
            app_id       = EXCLUDED.app_id,
            app_secret   = EXCLUDED.app_secret,
            home_channel = EXCLUDED.home_channel,
            enabled      = EXCLUDED.enabled,
            updated_at   = NOW()
    """, (user_id, app_id, app_secret, home_channel, enabled))
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────
# 在线分析（2026-05-23）· 报告 CRUD
# ─────────────────────────────────────────────────────────────────────

import json as _json


def save_analysis_report(report: dict) -> int:
    """保存一份完整在线分析报告，返回 report_id"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO online_analysis_report
            (user_id, stock_code, stock_name, thesis_text, kill_conditions,
             news_items, naive_pipeline, defense_pipeline, final_conclusion,
             thesis_status, confidence, poison_rate, llm_tokens, cost_cny, duration_ms)
        VALUES (%s, %s, %s, %s, %s::jsonb,
                %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            report.get("user_id", 1),
            report.get("stock_code"),
            report.get("stock_name"),
            report.get("thesis_text"),
            _json.dumps(report.get("kill_conditions") or [], ensure_ascii=False),
            _json.dumps(report.get("news_items") or {},     ensure_ascii=False),
            _json.dumps(report.get("naive_pipeline") or {}, ensure_ascii=False),
            _json.dumps(report.get("defense_pipeline") or {}, ensure_ascii=False),
            _json.dumps(report.get("final_conclusion") or {}, ensure_ascii=False),
            report.get("thesis_status"),
            report.get("confidence"),
            report.get("poison_rate"),
            report.get("llm_tokens"),
            report.get("cost_cny"),
            report.get("duration_ms"),
        ),
    )
    row = cur.fetchone()
    conn.commit()
    conn.close()
    return row[0]


def list_analysis_reports(user_id: int = 1, limit: int = 20, offset: int = 0,
                           stock_code: str | None = None) -> list[dict]:
    """列出历史分析报告（不含大字段，仅元数据）"""
    conn = get_conn()
    cur = conn.cursor()
    if stock_code:
        cur.execute(
            """
            SELECT id, stock_code, stock_name, thesis_status, confidence,
                   poison_rate, llm_tokens, cost_cny, created_at, duration_ms
            FROM online_analysis_report
            WHERE user_id = %s AND stock_code = %s
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (user_id, stock_code, limit, offset),
        )
    else:
        cur.execute(
            """
            SELECT id, stock_code, stock_name, thesis_status, confidence,
                   poison_rate, llm_tokens, cost_cny, created_at, duration_ms
            FROM online_analysis_report
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (user_id, limit, offset),
        )
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "id":            r[0],
            "stock_code":    r[1],
            "stock_name":    r[2],
            "thesis_status": r[3],
            "confidence":    float(r[4]) if r[4] is not None else None,
            "poison_rate":   float(r[5]) if r[5] is not None else None,
            "llm_tokens":    r[6],
            "cost_cny":      float(r[7]) if r[7] is not None else None,
            "created_at":    r[8].isoformat() if r[8] else None,
            "duration_ms":   r[9],
        }
        for r in rows
    ]


def get_analysis_report(report_id: int) -> dict | None:
    """读单份报告完整内容"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, user_id, stock_code, stock_name, thesis_text, kill_conditions,
               news_items, naive_pipeline, defense_pipeline, final_conclusion,
               thesis_status, confidence, poison_rate, llm_tokens, cost_cny,
               created_at, duration_ms
        FROM online_analysis_report
        WHERE id = %s
        """,
        (report_id,),
    )
    r = cur.fetchone()
    conn.close()
    if not r:
        return None
    return {
        "id":               r[0],
        "user_id":          r[1],
        "stock_code":       r[2],
        "stock_name":       r[3],
        "thesis_text":      r[4],
        "kill_conditions":  r[5],
        "news_items":       r[6],
        "naive_pipeline":   r[7],
        "defense_pipeline": r[8],
        "final_conclusion": r[9],
        "thesis_status":    r[10],
        "confidence":       float(r[11]) if r[11] is not None else None,
        "poison_rate":      float(r[12]) if r[12] is not None else None,
        "llm_tokens":       r[13],
        "cost_cny":         float(r[14]) if r[14] is not None else None,
        "created_at":       r[15].isoformat() if r[15] else None,
        "duration_ms":      r[16],
    }


# ─────────────────────────────────────────────────────────────────────
# 价格提醒 price_alerts（多租户）
# ─────────────────────────────────────────────────────────────────────

def list_price_alerts(user_id: str, code: str | None = None) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    if code:
        cur.execute(
            "SELECT id, code, label, condition_type, threshold, threshold2, cooldown_minutes, "
            "enabled, last_triggered_at, created_at FROM price_alerts "
            "WHERE user_id = %s AND code = %s ORDER BY created_at DESC",
            (user_id, code),
        )
    else:
        cur.execute(
            "SELECT id, code, label, condition_type, threshold, threshold2, cooldown_minutes, "
            "enabled, last_triggered_at, created_at FROM price_alerts "
            "WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,),
        )
    rows = cur.fetchall()
    conn.close()
    return [{
        "id": r[0], "code": r[1], "label": r[2], "condition_type": r[3],
        "threshold": float(r[4]), "threshold2": float(r[5]) if r[5] is not None else None,
        "cooldown_minutes": r[6], "enabled": r[7],
        "last_triggered_at": r[8].isoformat() if r[8] else None,
        "created_at": r[9].isoformat() if r[9] else None,
    } for r in rows]


def add_price_alert(user_id: str, code: str, label: str, condition_type: str,
                    threshold: float, threshold2: float | None = None,
                    cooldown_minutes: int = 60) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO price_alerts (user_id, code, label, condition_type, threshold, threshold2, cooldown_minutes) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (user_id, code, label, condition_type, threshold, threshold2, cooldown_minutes),
    )
    alert_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return alert_id


def delete_price_alert(alert_id: int, user_id: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM price_alerts WHERE id = %s AND user_id = %s RETURNING id", (alert_id, user_id))
    ok = cur.fetchone() is not None
    conn.commit()
    conn.close()
    return ok


def list_all_enabled_price_alerts() -> list[dict]:
    """调度器用：返回所有用户所有启用的提醒"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, user_id, code, label, condition_type, threshold, threshold2, "
        "cooldown_minutes, last_triggered_at FROM price_alerts WHERE enabled = TRUE"
    )
    rows = cur.fetchall()
    conn.close()
    return [{
        "id": r[0], "user_id": r[1], "code": r[2], "label": r[3],
        "condition_type": r[4], "threshold": float(r[5]),
        "threshold2": float(r[6]) if r[6] is not None else None,
        "cooldown_minutes": r[7],
        "last_triggered_at": r[8],
    } for r in rows]


def update_alert_triggered(alert_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE price_alerts SET last_triggered_at = NOW() WHERE id = %s", (alert_id,))
    conn.commit()
    conn.close()


def get_thesis_for_stock(code: str) -> dict | None:
    """返回某只股票的 thesis 信息，供价格预警多智能体分析使用"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT thesis_text, shares, cost_price FROM position_thesis WHERE code = %s",
        (code,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "thesis_text": row[0] or "",
        "shares":      row[1],
        "cost_price":  float(row[2]) if row[2] is not None else None,
    }


def get_latest_kill_conditions_for_stock(code: str) -> list:
    """返回最近一次在线分析时用户配置的 kill_conditions（JSONB 列表）"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT kill_conditions FROM online_analysis_report "
        "WHERE stock_code = %s ORDER BY created_at DESC LIMIT 1",
        (code,),
    )
    row = cur.fetchone()
    conn.close()
    if not row or not row[0]:
        return []
    kcs = row[0]
    if isinstance(kcs, str):
        import json
        try:
            kcs = json.loads(kcs)
        except Exception:
            return []
    return kcs if isinstance(kcs, list) else []


# ─────────────────────────────────────────────────────────────────────
# 飞书企业 Bot 绑定（open_id ↔ user_id）
# ─────────────────────────────────────────────────────────────────────

def get_feishu_binding_by_open_id(open_id: str) -> dict | None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, open_id, created_at FROM feishu_bindings WHERE open_id = %s",
        (open_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {"user_id": row[0], "open_id": row[1], "created_at": row[2]}


def get_feishu_binding_by_user_id(user_id: str) -> dict | None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, open_id, created_at FROM feishu_bindings WHERE user_id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {"user_id": row[0], "open_id": row[1], "created_at": row[2]}


def save_feishu_binding(user_id: str, open_id: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO feishu_bindings (user_id, open_id, created_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (user_id) DO UPDATE SET open_id = EXCLUDED.open_id, created_at = NOW()
        """,
        (user_id, open_id),
    )
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────
# 市场信号 market_signals
# ─────────────────────────────────────────────────────────────────────

def save_market_signal(signal_type: str, scenario: str, data: dict, portfolio_impact: str = "") -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO market_signals (signal_type, scenario, data, portfolio_impact) "
        "VALUES (%s, %s, %s::jsonb, %s) RETURNING id",
        (signal_type, scenario, _json.dumps(data, ensure_ascii=False), portfolio_impact),
    )
    row = cur.fetchone()
    conn.commit()
    conn.close()
    return row[0] if row else 0


def signal_triggered_today(signal_type: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM market_signals WHERE signal_type=%s AND triggered_at::date = CURRENT_DATE LIMIT 1",
        (signal_type,),
    )
    found = cur.fetchone() is not None
    conn.close()
    return found


def get_latest_signals() -> list[dict]:
    """每种 signal_type 取最新一条"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ON (signal_type)
            id, signal_type, scenario, triggered_at, data, portfolio_impact
        FROM market_signals
        ORDER BY signal_type, triggered_at DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return [{
        "id": r[0], "signal_type": r[1], "scenario": r[2],
        "triggered_at": r[3].isoformat() if r[3] else None,
        "data": r[4], "portfolio_impact": r[5] or "",
    } for r in rows]


def get_all_user_ids_with_wx() -> list[str]:
    """返回所有绑定了微信的 user_id（从 Redis wx_bound_users 读取）"""
    import redis as _redis
    r = _redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    members = r.smembers("wx_bound_users")
    return [m.decode() if isinstance(m, bytes) else m for m in members]


def get_signal_subscriptions(user_id: str) -> dict[str, bool]:
    """返回该用户各信号的订阅状态；未设置的默认 True（opt-out 模式）"""
    all_types = ["cpi", "oil", "fomc", "spacex", "northbound"]
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT signal_type, enabled FROM signal_subscriptions WHERE user_id=%s", (user_id,))
    rows = {r[0]: r[1] for r in cur.fetchall()}
    conn.close()
    return {t: rows.get(t, True) for t in all_types}


def set_signal_subscription(user_id: str, signal_type: str, enabled: bool):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO signal_subscriptions (user_id, signal_type, enabled, updated_at)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT (user_id, signal_type) DO UPDATE SET enabled=EXCLUDED.enabled, updated_at=NOW()
    """, (user_id, signal_type, enabled))
    conn.commit()
    conn.close()


def get_users_subscribed_to(signal_type: str) -> list[str]:
    """返回订阅了该信号的所有 user_id（未明确关闭 = 默认订阅）"""
    all_users = get_all_user_ids_with_wx()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM signal_subscriptions WHERE signal_type=%s AND enabled=FALSE", (signal_type,))
    opted_out = {r[0] for r in cur.fetchall()}
    conn.close()
    return [u for u in all_users if u not in opted_out]


def list_market_signals(limit: int = 30) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, signal_type, scenario, triggered_at, data, portfolio_impact "
        "FROM market_signals ORDER BY triggered_at DESC LIMIT %s",
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return [{
        "id": r[0], "signal_type": r[1], "scenario": r[2],
        "triggered_at": r[3].isoformat() if r[3] else None,
        "data": r[4], "portfolio_impact": r[5] or "",
    } for r in rows]


# ─────────────────────────────────────────────────────────────────────
# 信号报告 signal_reports
# ─────────────────────────────────────────────────────────────────────

def save_signal_report(signal_id: int, user_id: str, html_content: str) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO signal_reports (signal_id, user_id, html_content) VALUES (%s, %s, %s) RETURNING id",
        (signal_id, user_id, html_content),
    )
    report_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return report_id


def get_signal_report_html(report_id: int) -> str | None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT html_content FROM signal_reports WHERE id = %s", (report_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def list_signal_fires_with_reports(limit: int = 20) -> list[dict]:
    """返回最近的信号触发记录，附带每次触发产生的报告列表。"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, signal_type, scenario, triggered_at FROM market_signals "
        "ORDER BY triggered_at DESC LIMIT %s",
        (limit,),
    )
    signals = cur.fetchall()
    if not signals:
        conn.close()
        return []
    sig_ids = [r[0] for r in signals]
    placeholders = ",".join(["%s"] * len(sig_ids))
    cur.execute(
        f"SELECT id, signal_id, user_id FROM signal_reports "
        f"WHERE signal_id IN ({placeholders}) ORDER BY id",
        sig_ids,
    )
    reports_by_sig: dict[int, list[dict]] = {}
    for rid, sid, uid in cur.fetchall():
        reports_by_sig.setdefault(sid, []).append({"report_id": rid, "user_id": uid})
    conn.close()
    return [{
        "sig_id":       r[0],
        "signal_type":  r[1],
        "scenario":     r[2],
        "triggered_at": r[3].isoformat() if r[3] else None,
        "reports":      reports_by_sig.get(r[0], []),
    } for r in signals]


# ── 事件分析历史 ──────────────────────────────────────────

def save_event_analysis(user_id: str, event_desc: str, html_content: str) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO event_analysis_history (user_id, event_desc, html_content) "
        "VALUES (%s, %s, %s) RETURNING id",
        (user_id, event_desc, html_content),
    )
    row_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return row_id


def list_event_analyses(user_id: str, limit: int = 30) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, event_desc, created_at FROM event_analysis_history "
        "WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
        (user_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return [{"id": r[0], "event_desc": r[1], "created_at": r[2].isoformat()} for r in rows]


def get_event_analysis_html(row_id: int, user_id: str) -> str | None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT html_content FROM event_analysis_history WHERE id = %s AND user_id = %s",
        (row_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


# ─────────────────────────────────────────────────────────────────────
# 用户偏好记忆（2026-06-29）
# ─────────────────────────────────────────────────────────────────────

def get_user_preference(user_id: str) -> dict:
    """读取用户偏好，不存在时返回默认空偏好。"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT investment_style, risk_tolerance, holding_period, "
        "       focus_sectors, market_scope, push_focus, extra, updated_at "
        "FROM user_preference WHERE user_id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return {
            "user_id":          user_id,
            "investment_style": "",
            "risk_tolerance":   "",
            "holding_period":   "",
            "focus_sectors":    [],
            "market_scope":     "A股",
            "push_focus":       "",
            "extra":            {},
            "updated_at":       None,
        }
    return {
        "user_id":          user_id,
        "investment_style": row[0] or "",
        "risk_tolerance":   row[1] or "",
        "holding_period":   row[2] or "",
        "focus_sectors":    list(row[3]) if row[3] else [],
        "market_scope":     row[4] or "A股",
        "push_focus":       row[5] or "",
        "extra":            row[6] if row[6] else {},
        "updated_at":       row[7].isoformat() if row[7] else None,
    }


def upsert_user_preference(user_id: str, investment_style: str = "",
                           risk_tolerance: str = "", holding_period: str = "",
                           focus_sectors: list | None = None,
                           market_scope: str = "A股",
                           push_focus: str = "",
                           extra: dict | None = None) -> dict:
    """新建或更新用户偏好，返回更新后的完整偏好。"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO user_preference
            (user_id, investment_style, risk_tolerance, holding_period,
             focus_sectors, market_scope, push_focus, extra, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
        ON CONFLICT (user_id) DO UPDATE SET
            investment_style = EXCLUDED.investment_style,
            risk_tolerance   = EXCLUDED.risk_tolerance,
            holding_period   = EXCLUDED.holding_period,
            focus_sectors    = EXCLUDED.focus_sectors,
            market_scope     = EXCLUDED.market_scope,
            push_focus       = EXCLUDED.push_focus,
            extra            = EXCLUDED.extra,
            updated_at       = NOW()
        """,
        (
            user_id,
            investment_style,
            risk_tolerance,
            holding_period,
            focus_sectors or [],
            market_scope,
            push_focus,
            _json.dumps(extra or {}, ensure_ascii=False),
        ),
    )
    conn.commit()
    conn.close()
    return get_user_preference(user_id)


# ─────────────────────────────────────────────────────────────────────
# 用户行为记忆 + 自动画像（2026-06-29）
# ─────────────────────────────────────────────────────────────────────

_MAX_MEMORY_PER_STOCK = 5  # 每只股票最多保留 N 条记录，超出自动删最旧


def save_stock_memory(user_id: str, stock_code: str, stock_name: str,
                      event_type: str,
                      conclusion: str | None = None,
                      confidence: float | None = None,
                      extra_text: str | None = None) -> None:
    """记录一次用户行为（分析/预测/事件），并保持每只股票最多 _MAX_MEMORY_PER_STOCK 条。"""
    conn = get_conn()
    cur = conn.cursor()
    # 插入新记录
    cur.execute(
        """
        INSERT INTO user_stock_memory
            (user_id, stock_code, stock_name, event_type, conclusion, confidence, extra_text)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (user_id, stock_code, stock_name or "", event_type,
         conclusion, confidence, extra_text),
    )
    # 删除超出上限的旧记录（保留最新 N 条）
    cur.execute(
        """
        DELETE FROM user_stock_memory
        WHERE id IN (
            SELECT id FROM user_stock_memory
            WHERE user_id = %s AND stock_code = %s
            ORDER BY created_at DESC
            OFFSET %s
        )
        """,
        (user_id, stock_code, _MAX_MEMORY_PER_STOCK),
    )
    conn.commit()
    conn.close()


def update_user_portrait(user_id: str) -> None:
    """从 user_stock_memory 聚合用户画像，写入 user_preference.extra.auto_portrait。

    画像内容：
      - top_stocks: 最常分析的股票（按频次排序）
      - tendency:   偏多 / 均衡 / 偏空（根据 BUY/SELL 比例）
      - buy/hold/sell_count: 各结论次数
      - analysis_count: 总分析次数
      - last_updated
    """
    conn = get_conn()
    cur = conn.cursor()

    # 读取近 90 天的行为记录
    cur.execute(
        """
        SELECT stock_code, stock_name, event_type, conclusion, created_at
        FROM user_stock_memory
        WHERE user_id = %s
          AND created_at > NOW() - INTERVAL '90 days'
        ORDER BY created_at DESC
        """,
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return

    # 统计股票频次
    stock_counter: dict[str, dict] = {}
    buy_count = hold_count = sell_count = 0

    for code, name, etype, conclusion, _ in rows:
        if code not in stock_counter:
            stock_counter[code] = {"name": name or code, "count": 0}
        stock_counter[code]["count"] += 1

        if conclusion == "BUY":
            buy_count += 1
        elif conclusion == "SELL":
            sell_count += 1
        elif conclusion in ("HOLD", "WEAKENING", "INTACT"):
            hold_count += 1

    # 计算倾向
    total_conclusive = buy_count + sell_count
    if total_conclusive == 0:
        tendency = "均衡"
    elif buy_count >= sell_count * 2:
        tendency = "偏多"
    elif sell_count >= buy_count * 2:
        tendency = "偏空"
    else:
        tendency = "均衡"

    # Top 5 最常分析的股票
    top_stocks = sorted(stock_counter.items(), key=lambda x: -x[1]["count"])[:5]
    top_stocks_list = [
        {"code": code, "name": info["name"], "count": info["count"]}
        for code, info in top_stocks
    ]

    portrait = {
        "top_stocks":     top_stocks_list,
        "tendency":       tendency,
        "buy_count":      buy_count,
        "hold_count":     hold_count,
        "sell_count":     sell_count,
        "analysis_count": len(rows),
        "last_updated":   None,  # 由 DB NOW() 填充
    }

    # 写入 user_preference.extra.auto_portrait（不覆盖其他 extra 字段）
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO user_preference (user_id, extra, updated_at)
        VALUES (%s, jsonb_build_object('auto_portrait', %s::jsonb), NOW())
        ON CONFLICT (user_id) DO UPDATE SET
            extra      = user_preference.extra || jsonb_build_object('auto_portrait', EXCLUDED.extra->'auto_portrait'),
            updated_at = NOW()
        """,
        (user_id, _json.dumps(portrait, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()


def get_user_portrait(user_id: str) -> dict:
    """返回用户的自动画像（auto_portrait 部分）。"""
    pref = get_user_preference(user_id)
    return pref.get("extra", {}).get("auto_portrait", {})


# ─────────────────────────────────────────────────────────────────────
# 用户风险画像 & 现金余额（2026-08 · 持仓建议 Sprint 1）
# 供 portfolio_rebalance / portfolio_stress tool 读取应用约束
# ─────────────────────────────────────────────────────────────────────

def get_risk_profile(user_id: str) -> dict:
    """读取用户风险画像 · 不存在返回默认值（medium / 单票 25% / HK 40%）。"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT cash_balance, risk_tolerance, max_position, max_hk_ratio, "
        "       max_sector, extra, updated_at "
        "FROM user_risk_profile WHERE user_id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return {
            "user_id":        user_id,
            "cash_balance":   0.0,
            "risk_tolerance": "medium",
            "max_position":   0.25,
            "max_hk_ratio":   0.40,
            "max_sector":     0.40,
            "extra":          {},
            "updated_at":     None,
            "is_default":     True,
        }
    return {
        "user_id":        user_id,
        "cash_balance":   float(row[0] or 0),
        "risk_tolerance": row[1] or "medium",
        "max_position":   float(row[2] or 0.25),
        "max_hk_ratio":   float(row[3] or 0.40),
        "max_sector":     float(row[4] or 0.40),
        "extra":          row[5] if row[5] else {},
        "updated_at":     row[6].isoformat() if row[6] else None,
        "is_default":     False,
    }


def upsert_risk_profile(user_id: str,
                        cash_balance: float | None = None,
                        risk_tolerance: str | None = None,
                        max_position: float | None = None,
                        max_hk_ratio: float | None = None,
                        max_sector: float | None = None,
                        extra_patch: dict | None = None) -> dict:
    """新建或更新风险画像 · None 字段保留旧值 · extra_patch 合并到 extra。

    返回更新后的完整 profile。
    """
    current = get_risk_profile(user_id)
    merged_extra = {**(current.get("extra") or {}), **(extra_patch or {})}
    payload = {
        "cash_balance":   cash_balance   if cash_balance   is not None else current["cash_balance"],
        "risk_tolerance": risk_tolerance if risk_tolerance is not None else current["risk_tolerance"],
        "max_position":   max_position   if max_position   is not None else current["max_position"],
        "max_hk_ratio":   max_hk_ratio   if max_hk_ratio   is not None else current["max_hk_ratio"],
        "max_sector":     max_sector     if max_sector     is not None else current["max_sector"],
    }
    # 边界校验
    if payload["risk_tolerance"] not in ("low", "medium", "high"):
        raise ValueError("risk_tolerance 必须是 low / medium / high")
    for k in ("max_position", "max_hk_ratio", "max_sector"):
        v = float(payload[k])
        if v <= 0 or v > 1:
            raise ValueError(f"{k} 必须在 (0, 1] · 收到 {v}")
    if float(payload["cash_balance"]) < 0:
        raise ValueError("cash_balance 不能为负")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO user_risk_profile
            (user_id, cash_balance, risk_tolerance, max_position,
             max_hk_ratio, max_sector, extra, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
        ON CONFLICT (user_id) DO UPDATE SET
            cash_balance   = EXCLUDED.cash_balance,
            risk_tolerance = EXCLUDED.risk_tolerance,
            max_position   = EXCLUDED.max_position,
            max_hk_ratio   = EXCLUDED.max_hk_ratio,
            max_sector     = EXCLUDED.max_sector,
            extra          = EXCLUDED.extra,
            updated_at     = NOW()
        """,
        (
            user_id,
            payload["cash_balance"],
            payload["risk_tolerance"],
            payload["max_position"],
            payload["max_hk_ratio"],
            payload["max_sector"],
            _json.dumps(merged_extra, ensure_ascii=False),
        ),
    )
    conn.commit()
    conn.close()
    return get_risk_profile(user_id)
