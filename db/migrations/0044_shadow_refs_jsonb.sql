-- ════════════════════════════════════════════════════════════════════════
-- 0044_shadow_refs_jsonb.sql · 智能炒股 · 受控自进化 · 影子层（第四段 R7）
--   `fin_evolution_shadow_event.position_ref` / `valuation_ref`：TEXT → JSONB
--
-- 依据：`plan/R7.md` §一.2「持仓与估值：`position_ref` / `valuation_ref` 存**当日该臂的
--       持仓与估值快照**（JSONB），由事件逐笔重建，**不新建账本表**」。
--
-- 为什么单独一个迁移而不是回改 `0043`：
--   **迁移文件一经发布不可变**（`记忆系统追加规则.md` §四 铁律）。`0043` 是 `R6` 的已发布
--   文件，本阶段只加不改 —— 所以用「新加一个文件把两列改成 jsonb」，而不是去动 `0043` 里
--   那两行的类型。
--
-- 为什么是 jsonb 而不是继续用 TEXT：
--   影子臂**没有自己的账本表**（这是本轮的核心约束：不新建账本表、不动 `fin_trade`）。
--   持仓与估值快照只能**内联在事件行里**、由事件逐笔重建；写成 JSON 文本也能存，
--   但那等于把「结构化快照」当字符串，读回要自己 parse、也拿不到 jsonb 的校验与索引能力。
--   所以类型就是 jsonb。
--
-- 安全性：`0043` 只建表、`R6` 明确「写入在 `R7`」，所以此刻这张表**必然是空的** ——
--   `ALTER COLUMN … TYPE jsonb USING …::jsonb` 不会碰到任何退不回去的行。
--   整段包在 `TO information_schema` 判据里，**可重复执行**（第二遍两边都已是 jsonb，跳过）。
--
-- ⚠️ 纪律（`记忆系统追加规则.md` §四）：**不带 `BEGIN;` / `COMMIT;`**
--   —— `app/migrate.py:apply_one` 已把每个文件包在独立事务里。
-- ════════════════════════════════════════════════════════════════════════

DO $migration$
BEGIN
  -- 只在「表在、且这两列还不是 jsonb」时才 ALTER（幂等：第二遍条件为假，直接跳过）。
  IF EXISTS (
      SELECT 1 FROM information_schema.columns
       WHERE table_schema = current_schema()
         AND table_name = 'fin_evolution_shadow_event'
         AND column_name = 'position_ref'
         AND data_type <> 'jsonb'
  ) THEN
    -- 空表 + 允许 NULL → USING 只会在有行时逐行 ::jsonb（此刻没有行）。
    ALTER TABLE fin_evolution_shadow_event
      ALTER COLUMN position_ref  TYPE jsonb USING position_ref::jsonb,
      ALTER COLUMN valuation_ref TYPE jsonb USING valuation_ref::jsonb;

    COMMENT ON COLUMN fin_evolution_shadow_event.position_ref IS
      '该臂在该事件之后的持仓快照（JSONB）：{cash_available, cash_frozen, positions:[{code,qty,avg_cost,sellable_qty,price}]}。'
      '由事件逐笔重建（取该臂最近一条 ≤ 当日的行）；R7 起写入，R6 建表时是 TEXT。';
    COMMENT ON COLUMN fin_evolution_shadow_event.valuation_ref IS
      '该臂在该事件之后的估值快照（JSONB）：{cash_available, cash_frozen, market_value, total_assets, nav, initial_capital, as_of_price}。'
      '口径 = 可用 + 冻结 + Σ(股数 × 价)（与 paper/valuation.py 同口径），可复算。';
  END IF;
END
$migration$;

-- ════════════════════════════════════════════════════════════════════════
-- 同一迁移：`fin_evolution_event.kind` 闭集**加入 'failed'**（R7 的影子判定）
--
-- 依据：`plan/R7.md` §一.4「达到 `pass_line` → `passed`；**触及 `fail_line` → `kind='failed'`**」。
--   `0043` 的闭集里失败终局是 `rejected`（提案被驳回），而 R7 的验证失败是**另一种语义**：
--   提案本身没问题，是**跑出来不达标**。两者混用会让审计分不清「谁驳回了它」和「它跑了没通过」。
--   所以给闭集加一个 `failed`，`proposal.status` 仍投影到 `rejected`（状态列没有 failed，
--   见 `evolution.STATUS_OF_KIND`）—— 事件是权威，多一个 kind 不等于多一个状态。
--
-- 幂等：`DROP CONSTRAINT IF EXISTS` + `ADD CONSTRAINT`（重跑等价）。
-- ════════════════════════════════════════════════════════════════════════

DO $kind$
BEGIN
  ALTER TABLE fin_evolution_event
    DROP CONSTRAINT IF EXISTS fin_evolution_event_kind_check;
  ALTER TABLE fin_evolution_event
    ADD CONSTRAINT fin_evolution_event_kind_check CHECK (kind IN (
      'created','validating','passed','rejected','failed','applied',
      'rolled_back','inconclusive','rejected_by_gate'));
END
$kind$;
