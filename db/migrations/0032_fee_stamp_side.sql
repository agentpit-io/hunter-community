-- ════════════════════════════════════════════════════════════════════════
-- 0032_fee_stamp_side.sql · 智能炒股二期 · 印花税**方向**落表（N2）
--
-- 依据：`plan/ref/11-美股港股模拟交易需求分析与技术实施方案.md` §3.3 第 1 条
--       「所有费率与税率一律落表，禁止写死在代码里」
--       + `plan/二期追加规则.md` §三（费率一律落表 + 标来源与生效日）
--
-- 现状 `apps/paper/app/risk/fee.py:47` 把「印花税仅卖出」写死在代码里
-- （`... if side == "sell" else 0`）。港股印花税是**买卖双边**，美股**无印花税** ——
-- 所以「哪个方向收」必须由表决定：
--
--   stamp_side ∈ {buy, sell, both, none}
--     · sell —— A 股（一期口径：仅卖出；`0023`/`0025` 已成事实）
--     · both —— 港股（买卖双边）
--     · none —— 美股（无印花税）
--     · buy  —— 保留取值（某些市场可能买方课税）
--
-- **只加列 + 回填现状**，A 股行语义一字不变（回填 'sell' 是事实，不是猜）。
--
-- ⚠️ 已有部署：`db/migrations/*.sql` 不再由 postgres 的 initdb 目录执行，
--    改由 **api 启动时** `python -m app.migrate` 跑（见 `CLAUDE.md` 铁律）。
--    本文件仍需写，作用是「全新安装 + 留档」。
--
-- 幂等：全部 `ADD COLUMN IF NOT EXISTS` / `DROP CONSTRAINT IF EXISTS` + `ADD`。
-- ════════════════════════════════════════════════════════════════════════

ALTER TABLE fin_fee_model ADD COLUMN IF NOT EXISTS stamp_side TEXT;
UPDATE fin_fee_model SET stamp_side = 'sell' WHERE stamp_side IS NULL;   -- 历史行 = A 股（事实）
ALTER TABLE fin_fee_model ALTER COLUMN stamp_side SET DEFAULT 'sell';
ALTER TABLE fin_fee_model ALTER COLUMN stamp_side SET NOT NULL;

ALTER TABLE fin_fee_model DROP CONSTRAINT IF EXISTS fin_fee_model_stamp_side_check;
ALTER TABLE fin_fee_model ADD  CONSTRAINT fin_fee_model_stamp_side_check
  CHECK (stamp_side IN ('buy','sell','both','none'));
