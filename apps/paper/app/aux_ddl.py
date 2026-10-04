"""paper 侧辅助表 DDL · **单一来源**（L02）。

`fin_data_gap`（数据缺口）与 `fin_alert_log`（告警投递）两张表的建表 / 授权语句，
原来在三个模块里**各写了一遍**：`app/selfcheck.py`（启动补建）、`app/data_gap.py`、
`app/recon.py`。三份文本一旦漂，症状是「启动自检过了、某个调用点却报缺列」——
本模块把它们收成一份，三个消费者 import 这一个。

**权威仍是迁移 `db/migrations/0028_fin_data_gap.sql`**（api 启动时自动跑）。
paper 的构建上下文只有 `apps/paper/`（看不到 `db/migrations/`），且 paper 不跑迁移，
所以这份代码常量是 paper 的**启动兜底**，与 0028 的列集由守护用例比对
（`apps/api/tests/test_l02_single_source.py::test_paper_aux_ddl_single_source`）。
"""

from __future__ import annotations

FIN_DATA_GAP_DDL = """
CREATE TABLE IF NOT EXISTS fin_data_gap (
  id          BIGSERIAL PRIMARY KEY,
  code        TEXT NOT NULL,
  market      TEXT,
  source      TEXT,
  kind        TEXT NOT NULL CHECK (kind IN ('no_data','no_timestamp','no_price')),
  detail      TEXT,
  event_time  TIMESTAMPTZ,
  observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fin_data_gap_code_time ON fin_data_gap (code, observed_at DESC);
"""

FIN_DATA_GAP_GRANT = (
    "GRANT SELECT, INSERT ON fin_data_gap TO fin_paper_rw;"
    " GRANT USAGE ON SEQUENCE fin_data_gap_id_seq TO fin_paper_rw;"
)

FIN_ALERT_LOG_DDL = """
CREATE TABLE IF NOT EXISTS fin_alert_log (
  id         BIGSERIAL PRIMARY KEY,
  kind       TEXT NOT NULL,
  ref_id     BIGINT,
  channel    TEXT NOT NULL,
  project_id TEXT,
  subject    TEXT,
  body       TEXT,
  sent_at    TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS fin_alert_log_ref ON fin_alert_log (kind, ref_id);
CREATE INDEX IF NOT EXISTS fin_alert_log_unsent ON fin_alert_log (id) WHERE sent_at IS NULL;
"""

FIN_ALERT_LOG_GRANT = (
    "GRANT SELECT, INSERT, UPDATE ON fin_alert_log TO fin_paper_rw;"
    " GRANT USAGE ON SEQUENCE fin_alert_log_id_seq TO fin_paper_rw;"
)

# 启动自检一次性补建两张表用（两个 DDL + 两条 GRANT 拼起来）。
AUX_DDL = (FIN_DATA_GAP_DDL + FIN_DATA_GAP_GRANT
           + FIN_ALERT_LOG_DDL + FIN_ALERT_LOG_GRANT)
