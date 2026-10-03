-- ════════════════════════════════════════════════════════════════════════
-- 0042_memory_layer.sql · 智能炒股 · 经验层收口（结构化标签 / 市场状态 / 标的规范身份）
--
-- 依据：`plan/R5.md` §一.1 · `plan/00记忆新方案/01…调整建议.md` §八 8.1（DDL 草案）
--       · `plan/00记忆新方案/03…代码修改方案.md` §4-A · `plan/记忆系统追加规则.md` §四。
-- 执行：由 `app.migrate` 在 api 启动时按文件名顺序执行（`boot.sh` → `python -m app.migrate`）。
--       不挂 `docker-entrypoint-initdb.d`，不手工跑。
--
-- 只做加法：`ADD COLUMN IF NOT EXISTS` / `DROP CONSTRAINT IF EXISTS` + `ADD CONSTRAINT`
--   / `CREATE INDEX IF NOT EXISTS`。**不带 `BEGIN;` / `COMMIT;`** —— `app/migrate.py:apply_one`
--   已把每个迁移文件包在独立事务里（失败整体回滚 + 不记账），文件内再写事务边界会破坏那个承诺。
--   重复执行不报错（验收项之一）。
--
-- ⚠️ **旧行一律保持 NULL，不凭文本猜测。**（`03 §4-A`：不凭文本猜标的或市场状态）
--   本文件**不含任何 UPDATE / INSERT** —— 不从 `applicability` 文本里抠标的、
--   不从 `statement` 推 regime、不给 `importance` 编一个默认值。存量行加完列后
--   `symbols IS NOT NULL` 的计数与迁移前**逐位一致**（迁移前是 0）。
--   依据是：文本猜出来的标的 / 市场状态看起来像真值，但用户与聚合器都无从分辨
--   —— 那是本仓第一条铁律「空的比假的好」要挡的东西。
--
-- ⚠️ 新列的**语义边界**（写下来免得后来人重猜，代码侧同样有注释）：
--   · `memory_layer` 是**闭集**（`CHECK`），不许自由文本 —— 自由文本无法确定性聚合。
--   · `polarity='refute'`（失败经验）是**自进化的燃料**：它必须保留、
--     并参与后续提案筛选（`R6`）；展示可置前，但不得改变统计权重。
--   · `symbols` 是**规范化标的**（`<MARKET>:<CODE>`，如 `HK:00700`）——
--     存在的全部理由就是让 `HK:00700` 与 `US:0700` **判为不同标的**。
--     写入口径在 `app/services/fin/symbols.py`，本文件只负责列的形态。
--   · `strategy_keys` 用**稳定版本键**（不是显示名）。
--   · `regime_source` 是产生 `regime_tags` 的**来源键**：判定器写它的
--     `rule_version`（如 `regime-v1`），真人手写时为 `human`。它与
--     `regime.py` 的 `rule_version` **对齐**（`R5.md` §一.2）。
--   · `importance` **只能影响展示排序，不得改变统计权重** —— 提案聚合（`R6`）
--     不许按 `importance` 加权（`03 §4-A`）。
--   · `duplicate_of` **不删**：判重只指向另一条，与 `superseded_by` 同精神
--     （追加不删，`记忆系统追加规则.md` §五 第 4 条）。
--
-- 类型口径沿用 `0023` / `0041`：比例 / 权重类用 NUMERIC(9,6)；时间一律 TIMESTAMPTZ；
--   全表**不许出现 float / double / REAL**。
-- ════════════════════════════════════════════════════════════════════════


-- ① 记忆分层（M1）· 闭集，可空（旧行不猜）
--    取值口径取 `02` 的「多层记忆」一节（Episodic / Semantic / Procedural），
--    外加 `strategy`（策略记忆）—— `01 §8.3` 规则 9 要求
--    `memory_layer='strategy'` 必须来自回测 / 实盘真值（`kind='verified'` + `strategy_keys`）。
ALTER TABLE fin_experience ADD COLUMN IF NOT EXISTS memory_layer TEXT;
ALTER TABLE fin_experience DROP CONSTRAINT IF EXISTS fin_experience_memory_layer_chk;
ALTER TABLE fin_experience ADD CONSTRAINT fin_experience_memory_layer_chk
  CHECK (memory_layer IS NULL OR memory_layer IN ('episodic','semantic','procedural','strategy'));


-- ② 支持 / 推翻（M1 失败强化）· 可空（旧行不猜，不把 NULL 当 neutral）
ALTER TABLE fin_experience ADD COLUMN IF NOT EXISTS polarity TEXT;
ALTER TABLE fin_experience DROP CONSTRAINT IF EXISTS fin_experience_polarity_chk;
ALTER TABLE fin_experience ADD CONSTRAINT fin_experience_polarity_chk
  CHECK (polarity IS NULL OR polarity IN ('support','refute','neutral'));


-- ③ 命中的标的 / 策略键（M2）· 精确匹配取代 applicability 文本匹配
--    `symbols` 元素形态 `<MARKET>:<CODE>`（市场先规范化，`HK:00700` ≠ `US:0700`）。
ALTER TABLE fin_experience ADD COLUMN IF NOT EXISTS symbols TEXT[];
ALTER TABLE fin_experience ADD COLUMN IF NOT EXISTS strategy_keys TEXT[];


-- ④ 市场状态标签（承重一）· 可空
--    `regime_source` = 产生这些标签的来源键（判定器的 `rule_version`，或 `human`）。
ALTER TABLE fin_experience ADD COLUMN IF NOT EXISTS regime_tags TEXT[];
ALTER TABLE fin_experience ADD COLUMN IF NOT EXISTS regime_source TEXT;


-- ⑤ 重要度（M5）· 展示排序权重（**不参与统计加权**）
ALTER TABLE fin_experience ADD COLUMN IF NOT EXISTS importance NUMERIC(9,6);


-- ⑥ 上次重验时刻
ALTER TABLE fin_experience ADD COLUMN IF NOT EXISTS last_validated_at TIMESTAMPTZ;


-- ⑦ 去重 / 冲突互指（M3）· 不删
ALTER TABLE fin_experience ADD COLUMN IF NOT EXISTS duplicate_of TEXT
  REFERENCES fin_experience(experience_id);


-- ⑧ 索引：数组包含匹配走 GIN；分层聚合走组合索引
CREATE INDEX IF NOT EXISTS fin_experience_symbols_gin
  ON fin_experience USING GIN (symbols);
CREATE INDEX IF NOT EXISTS fin_experience_strategy_keys_gin
  ON fin_experience USING GIN (strategy_keys);
CREATE INDEX IF NOT EXISTS fin_experience_regime_gin
  ON fin_experience USING GIN (regime_tags);
CREATE INDEX IF NOT EXISTS fin_experience_layers
  ON fin_experience (project_id, memory_layer, polarity);


-- ── 权限：**刻意一条 GRANT 都不写**（与 `0041` 同一决定）──────────────────
-- 经验不是账本；新列沿用同表既有授权语义（连接身份 `hunter`，`rolsuper=true`）。
-- 为凑一句 GRANT 去建新角色只会多一处可出错的地方（「不许新增平行权威」）。
--
-- ⚠️ `01 §8.1` ⑧ 把 `fin_experience_evidence.evidence_kind` 拓宽到含
--    `'experience'` / `'evolution'`（周度巩固 / 回滚经验的证据类型）——
--    那属于 `M4 周度巩固` 与 `E5 回滚经验`（`R6` 及以后），**本文件不含**：
--    `R5.md` §一.1 只要求 `fin_experience` 上的九列，`§二` 明确不做提案层。
--    需要时另起一个迁移文件（迁移一经发布不可变）。
-- ════════════════════════════════════════════════════════════════════════
