-- ════════════════════════════════════════════════════════════════════════
-- 0047_evolution_experiment.sql · 智能炒股 · 五期 L01 · 「实验」实体（样本外 / 滚动验证）
--
-- 依据：`plan/00五期方案/…五期(闭环补齐)…md` §四 `L01` · `plan/L01.md` §1.4 / §3.4。
--
-- 方案 §12 的时间线是「候选 → 基础测试 → 样本外/滚动验证 → 影子模拟 → 门禁」。
-- 现在「样本外/滚动验证」这一格**没有独立实体**（影子验证的 `validation_id` 与
-- 冻结计划 `fin_evolution_plan` 是近似物但**语义不同**）。本表把它补成一个**具体的东西**：
-- 「实验」是**提案的挂接对象** —— 一条提案可以挂**若干次**实验，每次记：
--   · 输入：数据快照版本（`data_snapshot_id`，L03 之后的 `DataSnapshot`；L03 没做完时
--     先用现有快照编号并**如实标注**）、样本区间、成本模型、执行模型版本；
--   · 方法：做了什么（文字 + 可重跑的参数）；
--   · 结果：数字（**算不出就 NULL，不许编** —— 本仓第一条铁律）；
--   · 结论：`pass` / `fail` / **`inconclusive`（合法终局，不许为了出结论凑）**。
--
-- 执行：由 `app.migrate` 在 api 启动时按文件名顺序执行。只做加法，
--       **不带 `BEGIN;` / `COMMIT;`**（`app/migrate.py:apply_one` 已把每个文件包在
--       独立事务里），可重复执行（验收项之一）。
--
-- ⚠️ **本表只追加**：挂一个 `BEFORE UPDATE OR DELETE` 触发器 → `RAISE EXCEPTION`
--    （照 `0043_evolution_loop.sql` 对 `fin_evolution_plan` / `fin_evolution_event`
--    的做法）。**不碰 `fin_evolution_plan`** —— 那是不可变冻结计划（红线 10），
--    实验与它语义不同，各自独立。
-- ⚠️ 触发器对表属主**同样会触发**（这正是它挡得住应用代码的证明）；但库属主 / superuser
--    永远能 `ALTER TABLE … DISABLE TRIGGER` —— 所以**不宣称「对机器所有者绝对不可篡改」**，
--    与 `0043` 的书面结论同一口径。
--
-- 类型口径沿用 `0023` / `0041`：时间一律 TIMESTAMPTZ；全表**不出现 float / double / REAL**。
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS fin_evolution_experiment (
  experiment_id       TEXT PRIMARY KEY,                 -- expr_<ulid>
  proposal_id         TEXT NOT NULL
                      REFERENCES fin_evolution_proposal(proposal_id) ON DELETE RESTRICT,

  -- ① 输入（**算不出的一律 NULL，不许编**）
  conclusion          TEXT CHECK (conclusion IS NULL OR
                                  conclusion IN ('pass','fail','inconclusive')),  -- NULL = 进行中
  data_snapshot_id    TEXT,                             -- L03 之后是 DataSnapshot 键；之前如实标注来源
  sample_start        DATE,
  sample_end          DATE,
  cost_model          TEXT,                             -- 成本模型版本（如 fee-model-v1）
  execution_model_version TEXT,                         -- 执行模型版本（与费率版本对齐，L03 口径）

  -- ② 方法（做了什么：文字 + 可重跑的参数）
  method_text         TEXT NOT NULL DEFAULT '',         -- 人可读方法说明（LLM 只写字，这里也接受人工）
  method_params       JSONB,                            -- 可重跑的参数（键值）

  -- ③ 结果（数字；**算不出就 NULL**）
  result              JSONB,                            -- 指标读数（键值；无数字时 NULL）
  result_note         TEXT,                             -- 结果的文字说明 / 为什么是 NULL

  -- ④ 记录人 / 时刻
  owner               TEXT NOT NULL DEFAULT 'system',   -- 谁挂的（system / 用户名 / 工作流名）
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 查询主路径：按提案列出它的实验（R9 界面 / 只读端点）。
CREATE INDEX IF NOT EXISTS fin_evolution_experiment_proposal
  ON fin_evolution_experiment (proposal_id, created_at DESC);

-- 只追加：改 / 删一律抛异常（照 `0043` 的两条同款触发器）。
CREATE OR REPLACE FUNCTION fin_evolution_experiment_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'fin_evolution_experiment 只追加：% 被拒绝（实验记录不可改、不可删）', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS fin_evolution_experiment_immutable ON fin_evolution_experiment;
CREATE TRIGGER fin_evolution_experiment_immutable
  BEFORE UPDATE OR DELETE ON fin_evolution_experiment
  FOR EACH ROW EXECUTE FUNCTION fin_evolution_experiment_immutable();

-- ── 权限：**刻意一条 GRANT 都不写**（与 `0041` / `0043` 同一决定）──────────
-- 实验不是账本；连接身份是库属主 `hunter`（`rolsuper=true`），读写能力来自连接身份、
-- 不来自 GRANT。「任何角色不给 DELETE」在本表上天然成立。
-- ════════════════════════════════════════════════════════════════════════
