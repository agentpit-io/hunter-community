-- 下载失败记录 · 区分「暂时拿不到」和「根本拿不到」
-- 2026-08-27 · 见 doc/开源hunter-community/01详细工作目录/11量化策略/
--                24_20260827_全量下载限流问题与退避方案.md §3.5
--
-- 只记「孤立失败」(前一只成功、这只失败)。限流时是成片失败,
-- 那不是某只票的问题,记进来会把几千只票误判成永久拿不到。

CREATE TABLE IF NOT EXISTS data_failure (
  code       VARCHAR(10)  NOT NULL,
  data_type  VARCHAR(16)  NOT NULL,
  fail_count INT          NOT NULL DEFAULT 1,
  last_tried TIMESTAMPTZ  NOT NULL DEFAULT now(),
  PRIMARY KEY (code, data_type)
);

CREATE INDEX IF NOT EXISTS idx_df_lookup
  ON data_failure (data_type, fail_count DESC, last_tried DESC);
