"""paper 侧辅助表 DDL · **单一来源**（L02）。

`fin_data_gap`（数据缺口）与 `fin_alert_log`（告警投递）两张表的建表 / 授权语句，
原来在三个模块里**各写了一遍**：`app/selfcheck.py`（启动补建）、`app/data_gap.py`、
`app/recon.py`。三份文本一旦漂，症状是「启动自检过了、某个调用点却报缺列」——
本模块把它们收成一份，三个消费者 import 这一个。

L06 起多一份 `fin_exec_allowance`（执行允许名单）—— 同理：paper 启动时兜底补建。

**权威仍是迁移**（`db/migrations/0028_fin_data_gap.sql` · `…/0051_exec_allowance.sql`，
api 启动时自动跑）。paper 的构建上下文只有 `apps/paper/`（看不到 `db/migrations/`），
且 paper 不跑迁移，所以这份代码常量是 paper 的**启动兜底**，与迁移的列集由守护用例
比对（`apps/api/tests/test_l02_single_source.py::test_paper_aux_ddl_single_source`）。
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

# ── L06 · 执行允许名单（`db/migrations/0051_exec_allowance.sql` 的运行期副本）──
#   与上面两张表同一个道理：paper 的构建上下文只有 `apps/paper/`，看不到迁移目录、
#   也不跑迁移 —— 这份常量是 paper 的**启动兜底**；权威仍是 0051（api 启动时自动跑）。
#   只追加表 → 授 SELECT / INSERT，**不授 UPDATE / DELETE**（撤销 = 再追加一条 revoked）。
EXEC_ALLOWANCE_DDL = """
CREATE TABLE IF NOT EXISTS fin_exec_allowance (
  allowance_id BIGSERIAL PRIMARY KEY,
  scope        TEXT NOT NULL CHECK (scope IN ('project', 'instrument', 'tool')),
  subject      TEXT NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
  granted_by   TEXT NOT NULL,
  granted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  note         TEXT
);
CREATE INDEX IF NOT EXISTS fin_exec_allowance_scope_subject
  ON fin_exec_allowance (scope, subject, allowance_id DESC);
CREATE OR REPLACE FUNCTION fin_exec_allowance_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'fin_exec_allowance 只追加：% 被拒绝（允许名单不可改、不可删；撤销=再追加一条 status=revoked）', TG_OP;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS fin_exec_allowance_immutable ON fin_exec_allowance;
CREATE TRIGGER fin_exec_allowance_immutable
  BEFORE UPDATE OR DELETE ON fin_exec_allowance
  FOR EACH ROW EXECUTE FUNCTION fin_exec_allowance_immutable();
"""

EXEC_ALLOWANCE_GRANT = (
    "GRANT SELECT, INSERT ON fin_exec_allowance TO fin_paper_rw;"
    " GRANT USAGE ON SEQUENCE fin_exec_allowance_allowance_id_seq TO fin_paper_rw;"
)

# 启动自检一次性补建这几张表用（DDL + GRANT 拼起来）。
AUX_DDL = (FIN_DATA_GAP_DDL + FIN_DATA_GAP_GRANT
           + FIN_ALERT_LOG_DDL + FIN_ALERT_LOG_GRANT
           + EXEC_ALLOWANCE_DDL + EXEC_ALLOWANCE_GRANT)
