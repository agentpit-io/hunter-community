-- ════════════════════════════════════════════════════════════════════════
-- 0053_collection_complete.sql · 智能炒股 · 五期 L09 · 采集补齐（新闻 / 基本面 / 情绪）
--
-- 依据：`plan/00五期方案/…五期(闭环补齐)…md` §四 `L09` / §2.1（方案 §2.1「24 小时自动采集」）；
--       `plan/00五期方案/AI_炒股智能体系统_开源组件整合与技术实施方案.md`
--       §2.1（「持续订阅与增量抓取行情、新闻、基本面、情绪……记录数据缺口与新鲜度」）/
--       §6.1（数据接入方式）/ §6.2（时间字段 + 「情绪分析也需保留模型版本、证据、生成时间
--       和质量标记」）；`plan/五期追加规则.md` §3.2 / §6.1（**缩小范围**：数据源没有的，
--       只做「表 + 登记口」，如实写「未接」）/ §五 第 15 条（未接数据源清单）；
--       `plan/L09.md` §一 / §三。
--
-- 现状（方案 §2.1 第 10 条实测 · 本仓 grep 复核）：
--   · `news`（`apps/api/app/services/database.py:46`）有 `published_at` / `fetched_at`，
--     但**没接进 fin 侧的定时采集**；
--   · `financial_metric` / `financial_raw`（`database.py` 的数据中心段 · 留档
--     `apps/api/sql/20260824_data_center.sql`）同样**没接进 fin 侧定时采集**；
--   · **情绪：全仓无表**（`grep sentiment/情绪` 只命中 online_analysis 的新闻情绪分析器，
--     与「情绪采集」不是一回事，也没落库）。
--
-- 本迁移**只做加法**，三块：
--   ① 新建 `fin_sentiment`（**只追加**）—— 标的、时刻、情绪值、**模型版本 / 证据引用 /
--      生成时间 / 质量标记**（§6.2 末句）；
--   ② `news` 补 §6.2 缺的两个时间字段 `available_at` / `revision_id`；
--   ③ `financial_metric` / `financial_raw` 同样补这两个（与 ① 一套口径）。
--
-- ⛔ **红线 5（不许编数字 / 编情绪）**：`fin_sentiment.sentiment` 可空 —— 拿不到值就 `NULL`，
--    **绝不用涨跌幅 / 常数 / 任何派生值冒充情绪**（`plan/L09.md` §一.2）。
--    `available_at` / `revision_id` 在**当前新闻 / 财数据源**下拿不到真值 → 只能 `NULL`
--    （与 `0048` 的 `fin_snapshot` 同一条口径）。**不用 `now()` / `created_at` 冒充**。
-- ⛔ **情绪数据源未接**：`fin_sentiment` 由**登记口**写入（人工 / 未来接入的程序），本迁移
--    不播种任何行。缺什么、将来接哪，写在 `docs/开发文档/L09-采集补齐.md` 的「未接数据源清单」。
-- ⛔ **不回填历史行**：老的 `news` / `financial_*` 行新列一律 `NULL` = 「当时没记录」。
-- ⛔ **不带 `BEGIN;` / `COMMIT;`**（`app/migrate.py:apply_one` 已把每个文件包在独立事务里）；
--    可重复执行。
--
-- 类型口径：时刻一律 `TIMESTAMPTZ`；情绪值用 `NUMERIC`（**不用 float / double / REAL**，
-- 与 `0023` / `0048` 同一条口径）。
-- ════════════════════════════════════════════════════════════════════════


-- ── ① fin_sentiment：情绪（只追加；模型版本 / 证据 / 生成时间 / 质量标记）─────
-- §6.2 末句：「情绪分析也需保留模型版本、证据、生成时间和质量标记」。
-- 本表的每一列都对应这句话里的一个事实，**没有数据源就不写行，写行就必须说清来路**：
--
--   · `as_of`          —— 这条情绪**针对哪一刻**（数据时刻；如某条新闻的发布时间 / 盘中某分钟）。
--   · `sentiment`      —— 情绪值本身（标量）。**拿不到 → NULL**（红线 5）；不同来源标度可能不同，
--     标度写在 `note` / `source` 里，**不在表上强加一个标度**（强加就是替来源编口径）。
--   · `sentiment_label`—— 可选离散标签（bullish / bearish / neutral…），来源给才有。
--   · `model_version`  —— **必填**：算这条情绪的模型 / 来源版本（§6.2）。人工登记写 `manual:<人 / 规则>`。
--   · `evidence_ref`   —— **证据引用**（§6.2）：指向新闻 id / URL / 文件；没有 → NULL。
--   · `generated_at`   —— **必填**：这条情绪**被生成**的时刻（§6.2）。不是 `as_of`，不是入库时间。
--   · `quality`        —— **必填**：质量标记（§6.2）。**自由文本**，不预设封闭集合
--     （常见值 `ok` / `stale` / `low_confidence` / `unverified`，由登记方如实填写）。
--   · `source`         —— **必填**：来源（如 `manual` / 将来的模型服务名）。
--
-- **只追加**：改 / 删抛异常（照 `0043` / `0047` / `0048` 的同款触发器）。
-- ⚠️ 触发器对表属主**同样会触发**；但库属主 / superuser 永远能 `ALTER TABLE … DISABLE TRIGGER`
--    —— 所以**不宣称「绝对不可篡改」**（与 `0048` 同口径）。
CREATE TABLE IF NOT EXISTS fin_sentiment (
  id              BIGSERIAL   PRIMARY KEY,
  code            TEXT        NOT NULL,          -- 标的代码（任意市场；不强加形态，避免误拒）
  market          TEXT,                          -- 归属市场（本仓三值）；判不出 → NULL
  as_of           TIMESTAMPTZ NOT NULL,          -- 这条情绪针对的时刻（数据时刻）
  sentiment       NUMERIC,                       -- 情绪值；拿不到 → NULL（⛔ 不许编）
  sentiment_label TEXT,                          -- 可选离散标签（来源给才有）
  model_version   TEXT        NOT NULL,          -- 模型 / 来源版本（§6.2 必填）
  evidence_ref    TEXT,                          -- 证据引用（§6.2）；无 → NULL
  generated_at    TIMESTAMPTZ NOT NULL,          -- 生成时间（§6.2 必填）
  quality         TEXT        NOT NULL,          -- 质量标记（§6.2 必填）
  source          TEXT        NOT NULL,          -- 来源（§6.2 必填）
  note            TEXT,                          -- 口径 / 标度 / 备注（人可读）
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE fin_sentiment DROP CONSTRAINT IF EXISTS fin_sentiment_market_check;
ALTER TABLE fin_sentiment ADD  CONSTRAINT fin_sentiment_market_check
  CHECK (market IS NULL OR market IN ('CN_A','HK','US'));

-- 查询主路径：按标的取最近的情绪。
CREATE INDEX IF NOT EXISTS fin_sentiment_code_time
  ON fin_sentiment (code, as_of DESC);

-- 只追加：改 / 删抛异常。
CREATE OR REPLACE FUNCTION fin_sentiment_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'fin_sentiment 只追加：% 被拒绝（情绪记录不可改、不可删）', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS fin_sentiment_immutable ON fin_sentiment;
CREATE TRIGGER fin_sentiment_immutable
  BEFORE UPDATE OR DELETE ON fin_sentiment
  FOR EACH ROW EXECUTE FUNCTION fin_sentiment_immutable();

-- 权限：只追加表给 `fin_paper_rw` 的 SELECT / INSERT（与 `fin_data_snapshot` 同款）；
-- BIGSERIAL 需要序列 USAGE（与 `fin_data_gap` 同款）。
GRANT SELECT, INSERT ON fin_sentiment TO fin_paper_rw;
GRANT USAGE ON SEQUENCE fin_sentiment_id_seq TO fin_paper_rw;


-- ── ② news：§6.2 的时间字段（补缺的两个）──────────────────────────────────
-- `news` 已有 `published_at`（= 来源标称发布时间，§6.2 的 `published_at`）与
-- `fetched_at`（= 入库时刻，§6.2 的 `ingested_at` 别名）。真正缺的是：
--   · `available_at` —— 当时系统**实际可获取**该信息的时间。官方新闻源不返回它，
--     我们也没测可获取延迟 → 拿不到真值 → `NULL`（红线 5）。
--   · `revision_id`  —— 数据修订版本。新闻没有「修订号」，源也不给 → `NULL`。
-- 两列都**不回填历史行**（老的 `news` 行保持 `NULL`）。
ALTER TABLE news ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ;
ALTER TABLE news ADD COLUMN IF NOT EXISTS revision_id  TEXT;


-- ── ③ financial_metric / financial_raw：同一套时间字段 ────────────────────
-- `financial_metric` 有 `report_date`（报告期 = §6.2 的 `event_time`）与 `updated_at`
-- （= 入库时刻）；`financial_raw` 有 `report_date` / `fetched_at`。两者都缺
-- `available_at`（财报**实际可获取**时间 —— akshare 不返回，也没测量 → NULL）与
-- `revision_id`（**财报重述**版本 —— 当前接口不给 → NULL）。补列，不回填。
ALTER TABLE financial_metric ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ;
ALTER TABLE financial_metric ADD COLUMN IF NOT EXISTS revision_id  TEXT;
ALTER TABLE financial_raw    ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ;
ALTER TABLE financial_raw    ADD COLUMN IF NOT EXISTS revision_id  TEXT;

-- ════════════════════════════════════════════════════════════════════════
-- 说明：`news` / `financial_metric` / `financial_raw` 三张表由
--   `apps/api/app/services/database.py:init_db()` 建；`app.migrate` 的**步骤 ① 先跑
--   `init_db()` 再跑本目录的增量迁移**（`app/migrate.py:104-116`），所以这里的 `ALTER`
--   在**全新库**上也有目标表，不会因「表不存在」失败。
-- ════════════════════════════════════════════════════════════════════════
