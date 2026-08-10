import os
import psycopg2
from loguru import logger

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://hermes:Hermes2026DB!@localhost:5432/hermes")

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS stocks (
    code VARCHAR(10) PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    market VARCHAR(5) NOT NULL,
    exchange VARCHAR(5) NOT NULL
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
"""

def get_conn():
    return psycopg2.connect(DATABASE_URL)

async def init_db():
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(CREATE_TABLES)
        conn.commit()
        conn.close()
        logger.info("Database initialized")
    except Exception as e:
        logger.error("DB init failed: {}", e)
