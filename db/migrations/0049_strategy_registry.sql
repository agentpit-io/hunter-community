-- ════════════════════════════════════════════════════════════════════════
-- 0049_strategy_registry.sql · 智能炒股 · 五期 L04 · 自有策略服务
--
-- 依据：`plan/00五期方案/…五期(闭环补齐)…md` §四 `L04` / §1.1（策略代码与版本是四大权威
--       来源之一）/ §10.1（`strategy.submit/get/cancel`）；`plan/L04.md` §三。
--
-- 现状（方案 §2.3 实测）：唯一的「策略」是写死的示例策略；策略身份是 `fin_param.strategies`
-- 这个 JSONB 列里的 `{key, name, params, version:"v1"}`；**版本不是不可变的** —— `version`
-- 是自由字符串，没有版本表、没有内容哈希、没有「改了报错」的约束。
--
-- 本迁移把「策略定义」这一层补上：
--   ① `fin_strategy_definition` —— **只追加**的策略版本登记表（身份证）：键 / 名称 /
--      代码引用 / 参数 / **内容哈希** / 创建者 / 时刻。**「版本不可改」靠内容哈希 + 触发器**
--      （照四期 `0043_evolution_loop.sql:186-194` 那个「改就抛异常」的做法），不是靠自觉。
--   ② `fin_strategy_candidate` —— **追加式**候选状态事件（照 `fin_evolution_event` 的做法）：
--      候选的「提交 / 取消 / 生效 / 回滚」是**事件**，状态列只是可重建投影，审计以事件为准。
--
-- ⛔ **本迁移不碰量化/回测域那张 `strategy` 表**（`apps/api/sql/20260817_quant_strategy.sql`）——
--    那是因子策略表，与模拟盘策略服务无关（`plan/L04.md` 开头点名的坑）。
-- ⛔ **只做加法**：`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`；
--    **不带 `BEGIN;` / `COMMIT;`**（`app/migrate.py:apply_one` 已把每个文件包在独立事务里）；
--    可重复执行；**不改历史行**。
-- ⛔ 触发器对表属主**同样会触发**（这正是它挡得住应用代码的证明）；但库属主 / superuser
--    永远能 `ALTER TABLE … DISABLE TRIGGER` —— 所以**不宣称「对机器所有者绝对不可篡改」**，
--    与 `0043` / `0047` / `0048` 的书面结论同一口径。
--
-- 类型口径沿用 `0023` / `0041` / `0047`：时间一律 `TIMESTAMPTZ`；全表**不出现 float / double / REAL**。
-- ════════════════════════════════════════════════════════════════════════

-- ── ① fin_strategy_definition：策略版本登记表（**只追加**）────────────────
--   一行 = 一个**不可变**的策略版本。`strategy_version_id` 是稳定版本键（`strv_<24hex>`），
--   由内容哈希推导（`services/fin/strategy.py:version_id_of`）—— 同一份内容重复登记得到
--   同一个键（幂等），内容一变就是**新的一行**（新键），老行一个字节不动。
--
--   为什么「内容哈希」放在表里而不是只算在内存：复盘要能证明「这个键对应的内容到底是
--   什么」——只存哈希、内容在别处，就还是要回头信一个可变的地方。本表把 content_hash
--   与定义内容（key / source_ref / params）一起钉住，`(strategy_key, content_hash)` 唯一，
--   于是「同一个 key 下同一个内容不会登记两次」。
CREATE TABLE IF NOT EXISTS fin_strategy_definition (
  strategy_version_id TEXT PRIMARY KEY,                 -- 稳定版本键 strv_<24hex>
  strategy_key        TEXT NOT NULL,                    -- 策略族键（ma_momentum / sample-fixed …）
  name                TEXT NOT NULL,                    -- 人可读名称
  source_ref          TEXT NOT NULL,                    -- 代码引用 / 公式（执行体），如 sample.py:build_decision
  params              JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 该版本的参数（键值）
  content_hash        TEXT NOT NULL,                    -- 定义内容的 sha256（规范化后）
  origin              TEXT NOT NULL DEFAULT 'builtin',  -- 来源闭集：内置 / 用户 / 自进化
  note                TEXT,                             -- 登记说明（可空）
  created_by          TEXT NOT NULL DEFAULT 'system',   -- 谁登记的
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (origin IN ('builtin','user','evolution'))
);

-- 同一 key 下同一内容只登记一次（幂等登记靠它 + `ON CONFLICT DO NOTHING`）。
CREATE UNIQUE INDEX IF NOT EXISTS fin_strategy_definition_key_hash
  ON fin_strategy_definition (strategy_key, content_hash);
-- 查询主路径：按 key 列出它的历史版本（越新越前）。
CREATE INDEX IF NOT EXISTS fin_strategy_definition_key_time
  ON fin_strategy_definition (strategy_key, created_at DESC);

COMMENT ON TABLE fin_strategy_definition IS
  '策略版本登记表（L04 · 只追加）。一行 = 一个不可变策略版本；改策略 = 登记新版本（新键）。'
  '「版本不可改」由 BEFORE UPDATE OR DELETE 触发器强制，不靠自觉。';

-- 只追加：改 / 删一律抛异常（照 `0043` 的两条同款触发器）。
CREATE OR REPLACE FUNCTION fin_strategy_definition_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'fin_strategy_definition 只追加：% 被拒绝（策略版本不可改、不可删；改策略=登记新版本）', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS fin_strategy_definition_immutable ON fin_strategy_definition;
CREATE TRIGGER fin_strategy_definition_immutable
  BEFORE UPDATE OR DELETE ON fin_strategy_definition
  FOR EACH ROW EXECUTE FUNCTION fin_strategy_definition_immutable();


-- ── ② fin_strategy_candidate：候选状态事件（**追加式**）────────────────────
--   一行 = 一次候选状态事件（`submitted` / `cancelled` / `activated` / `rejected`）。
--   候选当前状态是**从事件重建的投影**（`services/fin/strategy.py:get`），**不设 status 列**
--   —— 与四期「proposal 表一面只追加一面 UPDATE status」的教训同一决定（`记忆系统追加规则.md`
--   §3.2 第 4 条）：审计以事件为准，状态列只做可重建投影。
--
--   `candidate_id` 就是被考虑的 `strategy_version_id`（候选 = 一个待生效的版本）。
--   `project_id` 可空 = 与项目无关的登记（如内置登记）；有值 = 该候选针对这个项目提交。
CREATE TABLE IF NOT EXISTS fin_strategy_candidate (
  event_id     TEXT PRIMARY KEY,                        -- svc_<24hex>
  candidate_id TEXT NOT NULL,                           -- = fin_strategy_definition.strategy_version_id
  strategy_key TEXT NOT NULL,                           -- 冗余一份，便于按 key 查事件
  project_id   TEXT,                                    -- 针对的项目（可空）
  kind         TEXT NOT NULL,                           -- 事件类型闭集
  payload      JSONB,                                   -- 事件附加信息（谁取消的、为什么…）
  created_by   TEXT NOT NULL DEFAULT 'system',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (kind IN ('submitted','cancelled','activated','rejected'))
);

CREATE INDEX IF NOT EXISTS fin_strategy_candidate_by_candidate
  ON fin_strategy_candidate (candidate_id, created_at);
CREATE INDEX IF NOT EXISTS fin_strategy_candidate_by_project
  ON fin_strategy_candidate (project_id, created_at DESC);

COMMENT ON TABLE fin_strategy_candidate IS
  '候选状态事件（L04 · 只追加）。候选状态是从事件重建的投影，没有 status 列，审计以事件为准。';

CREATE OR REPLACE FUNCTION fin_strategy_candidate_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'fin_strategy_candidate 只追加：% 被拒绝（候选状态事件不可改、不可删）', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS fin_strategy_candidate_immutable ON fin_strategy_candidate;
CREATE TRIGGER fin_strategy_candidate_immutable
  BEFORE UPDATE OR DELETE ON fin_strategy_candidate
  FOR EACH ROW EXECUTE FUNCTION fin_strategy_candidate_immutable();


-- ── 权限：**刻意一条 GRANT 都不写**（与 `0041` / `0043` / `0047` / `0048` 同一决定）──
--   策略登记不是账本；连接身份是库属主 `hunter`，读写能力来自连接身份、不来自 GRANT。
--   「任何角色不给 DELETE」在本表上由**触发器**兑现（比「不给授权」更硬 —— 连属主都拒）。
-- ════════════════════════════════════════════════════════════════════════
