-- ════════════════════════════════════════════════════════════════════════
-- 0048_decision_provenance.sql · 智能炒股 · 五期 L03 · 决策「出身证」
--
-- 依据：`plan/00五期方案/…五期(闭环补齐)…md` §四 `L03` · §2.4；
--       技术方案基线 §6.2（七个时间字段）/ §10.2（`DataSnapshot` 对象）/ §10.3（决策上下文）；
--       `plan/L03.md` §一 / §三。
--
-- 出事要复盘「当时它到底看到了什么、用什么模型算的、是不是模拟」—— 现在的字段不够答。
-- 本迁移把缺的补上，**只做加法**，三块：
--   ① `fin_snapshot`（行情快照）：补 §6.2 缺的两个时间字段 —— `available_at` / `revision_id`；
--   ② `fin_trade`（成交）：补 §10.3 决策上下文 —— `execution_model_version` / `mode` /
--      `portfolio_version`（对齐已有的 `fee_model_version`，两者口径从此一致）；
--   ③ 新建 `fin_data_snapshot`（§10.2 的 `DataSnapshot` **对象**，只追加）——
--      数据截止时间 + 来源 + 版本 + 质量 + 产物引用 + 可获取时间。
--
-- ⛔ **红线 5（不许编时间 / 不许编数字）**：`available_at` 与 `revision_id` 在**当前数据源**
--    （腾讯 `qt.gtimg.cn` / hunter 网关）下**拿不到真值**（数据源不返回它们，我们也没测
--    网络可获取延迟）——历史行与新行都只能 `NULL`。**绝不用 `now()` / `created_at` /
--    自增号冒充**。「为什么只能 NULL」逐条写在 `docs/开发文档/L03-决策出身证.md`。
-- ⛔ **不回填历史行**：老行的新列一律 `NULL` = 「当时没记录」；填任何值都是编。
-- ⛔ **不带 `BEGIN;` / `COMMIT;`**（`app.migrate.apply_one` 已把每个文件包在独立事务里）。
-- ⛔ 只做加法、可重复执行（验收项之一）。
--
-- 类型口径沿用 `0023` / `0047`：时间一律 `TIMESTAMPTZ`；全表**不出现 float / double / REAL**。
-- ════════════════════════════════════════════════════════════════════════

-- ── ① fin_snapshot：§6.2 的时间字段（补缺的两个）─────────────────────────
-- §6.2 的七个时间字段里，本表已经有 `snapshot_time`（= `event_time`，数据源回报的时刻，
-- 也是这张快照的「数据截止时间」）、`created_at`（≈ `ingested_at`，入库时刻）、
-- `snapshot_id`（不可变快照编号）、`raw_ref`（≈ 产物引用）。真正缺的是两个：
--
--   · `available_at` —— 「当时系统**实际可获取**该信息的时间」。腾讯 / hunter 网关
--     **不返回它**，而网络可获取延迟我们**没有测量**（无从证明）。拿不到真值 → `NULL`
--     （红线 5）。将来接有 `available_at` 的数据源（如带 availableFrom 的行情源）时再填。
--   · `revision_id`  —— 「数据修订版本」。行情没有“修订号”这种东西（一价即一快照），
--     数据源也不给 → `NULL`。**不编自增号假装是修订号**（红线 5）。
ALTER TABLE fin_snapshot ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ;
ALTER TABLE fin_snapshot ADD COLUMN IF NOT EXISTS revision_id  TEXT;


-- ── ② fin_trade：§10.3 决策上下文（补三个）───────────────────────────────
--   · `execution_model_version` —— 撮合时**用的**执行模型版本（`fin_execution_model.version`）。
--     撮合那一刻已知（`apps/paper/app/matching/model.py:load_execution_model`）。以前**不落成交
--     记录**，与已经落库的 `fee_model_version` 口径**不一致**；本列把它对齐。
--   · `mode`                    —— **显式**模式。本服务恒 `PAPER`（`apps/paper/app/config.py`
--     的 `PAPER_MODE_REQUIRED`），但**不是靠「没配置就是 PAPER」**：撮合时显式写出来。
--   · `portfolio_version`       —— 账户（组合）版本 = 本笔成交**应用在**的那一版账本
--     （`fin_project.version` 在撮合前的取值）。§10.3 的正式名是 `portfolio_version`；
--     `fin_project` 上那一列历史名叫 `version`，**不改历史列名**（同义不同名 → 声明正式名）。
--
-- 三列**可空**：历史行（本迁移之前）没记录这些，保持 `NULL`；新行由 paper 写入真值。
ALTER TABLE fin_trade ADD COLUMN IF NOT EXISTS execution_model_version TEXT;
ALTER TABLE fin_trade ADD COLUMN IF NOT EXISTS mode                    TEXT;
ALTER TABLE fin_trade ADD COLUMN IF NOT EXISTS portfolio_version       BIGINT;

-- `mode` 只允许 'PAPER'（本服务是模拟盘）。NULL 放行 = 历史行 / 尚未写入的行。
-- 将来要加 REAL 时：先改这条 CHECK，再新加一个迁移 —— **不要在本文件上改**（发布后不可变）。
ALTER TABLE fin_trade DROP CONSTRAINT IF EXISTS fin_trade_mode_check;
ALTER TABLE fin_trade ADD  CONSTRAINT fin_trade_mode_check
  CHECK (mode IS NULL OR mode IN ('PAPER'));


-- ── ③ fin_data_snapshot：§10.2 的 `DataSnapshot` 对象（只追加）─────────────
-- §10.2 要的「数据截止时间 + 来源 + 版本 + 质量 + 产物引用」五合一对象**不存在**：
-- 最接近的是 `fin_data.py` 那个**临时 dict**（不落库）与 `fin_snapshot`（缺版本 / 缺显式
-- 截止时间列 / 没有独立的对象键）。本表把它补成一个**真的东西** ——
-- **不推倒 `fin_snapshot`**（撮合在依赖它），而是**另加一张只追加的表**：
-- 一段「被冻结、可被决策绑定」的数据快照，由 `data.snapshot_create` 落库、
-- `data.snapshot_get` 读回（§10.1 的两个工具）。
--
-- 口径（**算不出的一律 NULL，不许编** —— 本仓第一条铁律）：
--   · `data_cutoff_at` 必填 —— 数据截止到哪一刻（本对象的核心；没有它就不是快照）；
--   · `source` 必填、`quality` 必填 —— 来源与质量事实；
--   · `revision_id` 可空 —— 数据源不给修订号 → NULL；
--   · `artifact_ref` 可空 —— 产物引用（不可变文件 / 对象）；没有落盘产物 → NULL；
--   · `available_at` 可空 —— 当时实际可获取时间；拿不到 → NULL（红线 5）。
CREATE TABLE IF NOT EXISTS fin_data_snapshot (
  data_snapshot_id TEXT PRIMARY KEY,             -- DSNAP-<hex>；决策绑定的稳定键（L04 用它）
  data_cutoff_at   TIMESTAMPTZ NOT NULL,         -- 数据截止时间（§10.2）
  source           TEXT NOT NULL,                -- 来源
  revision_id      TEXT,                         -- 版本（数据修订号）；数据源不给 → NULL
  quality          TEXT NOT NULL,                -- 质量
  artifact_ref     TEXT,                         -- 产物引用（不可变文件 / 对象路径）
  available_at     TIMESTAMPTZ,                  -- 当时系统实际可获取的时间；拿不到 → NULL
  market           TEXT,                         -- 归属市场（可选，本仓三值）
  code             TEXT,                         -- 归属标的（可选）
  note             TEXT,                         -- 为什么 / 口径说明（人可读）
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE fin_data_snapshot DROP CONSTRAINT IF EXISTS fin_data_snapshot_market_check;
ALTER TABLE fin_data_snapshot ADD  CONSTRAINT fin_data_snapshot_market_check
  CHECK (market IS NULL OR market IN ('CN_A','HK','US'));

-- 查询主路径：按截止时间倒序列出「最近冻了哪些数据快照」。
CREATE INDEX IF NOT EXISTS fin_data_snapshot_cutoff
  ON fin_data_snapshot (data_cutoff_at DESC);

-- 只追加：改 / 删抛异常（照 `0043` / `0047` 的同款触发器）。
-- ⚠️ 触发器对表属主**同样会触发**（这正是它挡得住应用代码的证明）；但库属主 / superuser
--    永远能 `ALTER TABLE … DISABLE TRIGGER` —— 所以**不宣称「绝对不可篡改」**（与 0043 同口径）。
CREATE OR REPLACE FUNCTION fin_data_snapshot_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'fin_data_snapshot 只追加：% 被拒绝（数据快照不可改、不可删）', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS fin_data_snapshot_immutable ON fin_data_snapshot;
CREATE TRIGGER fin_data_snapshot_immutable
  BEFORE UPDATE OR DELETE ON fin_data_snapshot
  FOR EACH ROW EXECUTE FUNCTION fin_data_snapshot_immutable();

-- ── 权限：只追加表给 `fin_paper_rw` 的 SELECT / INSERT（与 `fin_snapshot` 同款）──
-- 与 `0023` 的追加式授权同一口径：**不给 UPDATE / DELETE**。
GRANT SELECT, INSERT ON fin_data_snapshot TO fin_paper_rw;
-- ════════════════════════════════════════════════════════════════════════
