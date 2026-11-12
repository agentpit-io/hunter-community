-- ════════════════════════════════════════════════════════════════════════
-- 0025_fin_matching.sql · 智能炒股 · M3（快照与撮合）所需的三处增量
--
-- 只加不减（`09 §七`：不 DROP、不改列类型、不重命名）：
--   ① `fin_execution_model.tick_size` —— 市价单滑点的计价单位。滑点是
--      「N 个最小变动价位」，没有 tick_size 的话 N 就是个悬空的数
--      （`09 §十-3` 待拍板项写的是 1 个最小变动价位）。
--   ② `fin_idempotency.response` —— 原回执本体。「同一键、同一内容 → 返回原结果」
--      需要一个地方把原结果存下来；`response_ref` 只够放一个引用（trade_id）。
--   ③ `fin_order.frozen_amount` —— 这笔委托在受理时冻了多少资金。挂单撤销时
--      要按它解冻，成交时按它把多冻的部分退回可用（限价高于快照价时必然有多冻）。
--      不存它就得靠「重算一遍费用模型」去推，费用模型改过版本就对不上。
--   ④ 执行模型默认行 `paper-model-v1`（滑点 1 个最小变动价位、整笔成交）。
--      值取自 `总控规则 §八` 默认决策，不是随手编的数。
--
-- 已有部署：本文件由 `app.migrate` 在 api 启动时按文件名顺序执行
--   （`db/migrations` 不是 initdb 挂载 —— 那目录只在数据卷第一次初始化时跑）。
--   全部语句幂等，重复执行不报错。
--
-- 权限：`fin_execution_model` / `fin_idempotency` / `fin_order` 三张表在 0023 里
--   已 `GRANT SELECT, INSERT, UPDATE TO fin_paper_rw`；表级 GRANT 覆盖新增列，
--   不需要再授。0024 的 `ALTER DEFAULT PRIVILEGES` 只针对**新表的新序列**，
--   本文件没有新建序列。
-- ════════════════════════════════════════════════════════════════════════

-- ① 滑点的计价单位（A 股最小变动价位 = 0.01 元；按品种可配）
ALTER TABLE fin_execution_model
  ADD COLUMN IF NOT EXISTS tick_size NUMERIC(12,4) NOT NULL DEFAULT 0.01;

-- ② 原回执本体（「同一键、同一内容 → 返回原结果」的落点）
ALTER TABLE fin_idempotency
  ADD COLUMN IF NOT EXISTS response JSONB;

-- ③ 委托受理时冻结的资金额（挂单解冻 / 成交退回多冻部分的依据）
ALTER TABLE fin_order
  ADD COLUMN IF NOT EXISTS frozen_amount NUMERIC(18,4) NOT NULL DEFAULT 0;

-- ④ 一期默认执行模型
INSERT INTO fin_execution_model (version, slippage_ticks, tick_size, part_fill, note)
VALUES ('paper-model-v1', 1, 0.01, false,
        '一期默认：市价单滑点 = 1 个最小变动价位（0.01 元）；整笔成交，不做部分成交')
ON CONFLICT (version) DO UPDATE SET
  slippage_ticks = EXCLUDED.slippage_ticks,
  tick_size      = EXCLUDED.tick_size,
  part_fill      = EXCLUDED.part_fill;
