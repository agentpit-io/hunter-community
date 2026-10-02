-- 智能炒股 · M7 数据面两张表（缺口记录 + 告警投递）
--
-- ⚠️ 这个文件**对已有部署不生效**（`db/migrations/` 只在数据卷第一次初始化时执行）。
--    真正生效的 DDL 随代码走：
--      · `apps/paper/app/data_gap.py`     的 `_DDL`（缺口）
--      · `apps/paper/app/recon.py`        的 `_DDL`（告警投递）
--      · `apps/paper/app/selfcheck.py`    的 `_AUX_DDL`（启动时补建，两处一致）
--    本文件的作用是**给全新安装 + 留档**，三处内容必须保持一致。

-- 数据缺口：断流 / 无数据源时刻 / 无最新价 —— 这些情况下 `fin_snapshot` 不会落行
-- （snapshot_time 只能来自数据源），于是缺口必须单独留痕，否则「今天行情断了 40 分钟」
-- 在账本里一个字都没有（01方案 §11.2、08 §七）。
-- `stale`（有价但旧）**不进这张表** —— 那种快照落在 fin_snapshot 里带 missing_flag。
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

-- 告警投递记录：对账不平 → 排一条记录（sent_at 为空），宿主脚本读它发 notify-qq。
-- **通道复用 hunter 现有的 notify-qq，没有第二套告警系统**；`fin_recon_log` 保持只追加。
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

GRANT SELECT, INSERT ON fin_data_gap TO fin_paper_rw;
GRANT USAGE ON SEQUENCE fin_data_gap_id_seq TO fin_paper_rw;
GRANT SELECT, INSERT, UPDATE ON fin_alert_log TO fin_paper_rw;
GRANT USAGE ON SEQUENCE fin_alert_log_id_seq TO fin_paper_rw;
