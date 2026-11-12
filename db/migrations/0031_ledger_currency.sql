-- ════════════════════════════════════════════════════════════════════════
-- 0031_ledger_currency.sql · 智能炒股二期 · 账本加币种（只加列，不改历史行）
--
-- 依据：`plan/ref/11-美股港股模拟交易需求分析与技术实施方案.md` §4.1（迁移 `0031`）
--       / §3.1 A 方案（分市场独立账本、各自本币记账、账本内永不折算）
--       + `plan/拍板-2026-10-03-港美股必须可交易.md` §三/§四
--
-- 五张账本表各加 `currency TEXT`：
--   fin_order / fin_trade / fin_cash_ledger / fin_position / fin_valuation
--
-- 历史行**回填 'CNY'** —— 改动前实测这五张表全部是 A 股账本
-- （fin_order 437 / fin_trade 260 / fin_cash_ledger 1182 / fin_position 221 /
--  fin_valuation 30 行，来源均为一期 A 股模拟盘）→「回填 CNY」是**事实，不是猜**
-- （红线 §六.1）。
--
-- **不加 `fx_rate` 到账本**：汇率只在「跨市场合计」那一处请求时现取并标注来源与
-- 时刻，账本内永不换算（设计文档 §3.1 A 方案）。
--
-- 为什么给默认值 `'CNY'` 而不是留空：现有 A 股写入方（`paper` 的账本 / 估值模块）
-- 的 INSERT 不带 currency，留空会让新行写进 NULL；`SET DEFAULT 'CNY'` 让 A 股路径
-- **一行都不变**（旧行为 = 人民币）。港美股写入方（N4 改造）必须显式写各自币种。
--
-- 幂等：`ADD COLUMN IF NOT EXISTS` + 回填只动 NULL 行 + `SET DEFAULT` / `SET NOT NULL`
--       重复执行都是空操作。
-- ════════════════════════════════════════════════════════════════════════

-- ── fin_order ────────────────────────────────────────────────────────────
ALTER TABLE fin_order ADD COLUMN IF NOT EXISTS currency TEXT;
UPDATE fin_order SET currency = 'CNY' WHERE currency IS NULL;
ALTER TABLE fin_order ALTER COLUMN currency SET DEFAULT 'CNY';
ALTER TABLE fin_order ALTER COLUMN currency SET NOT NULL;
ALTER TABLE fin_order DROP CONSTRAINT IF EXISTS fin_order_currency_check;
ALTER TABLE fin_order ADD  CONSTRAINT fin_order_currency_check
  CHECK (currency IN ('CNY','HKD','USD'));

-- ── fin_trade ────────────────────────────────────────────────────────────
ALTER TABLE fin_trade ADD COLUMN IF NOT EXISTS currency TEXT;
UPDATE fin_trade SET currency = 'CNY' WHERE currency IS NULL;
ALTER TABLE fin_trade ALTER COLUMN currency SET DEFAULT 'CNY';
ALTER TABLE fin_trade ALTER COLUMN currency SET NOT NULL;
ALTER TABLE fin_trade DROP CONSTRAINT IF EXISTS fin_trade_currency_check;
ALTER TABLE fin_trade ADD  CONSTRAINT fin_trade_currency_check
  CHECK (currency IN ('CNY','HKD','USD'));

-- ── fin_cash_ledger ──────────────────────────────────────────────────────
ALTER TABLE fin_cash_ledger ADD COLUMN IF NOT EXISTS currency TEXT;
UPDATE fin_cash_ledger SET currency = 'CNY' WHERE currency IS NULL;
ALTER TABLE fin_cash_ledger ALTER COLUMN currency SET DEFAULT 'CNY';
ALTER TABLE fin_cash_ledger ALTER COLUMN currency SET NOT NULL;
ALTER TABLE fin_cash_ledger DROP CONSTRAINT IF EXISTS fin_cash_ledger_currency_check;
ALTER TABLE fin_cash_ledger ADD  CONSTRAINT fin_cash_ledger_currency_check
  CHECK (currency IN ('CNY','HKD','USD'));

-- ── fin_position ─────────────────────────────────────────────────────────
ALTER TABLE fin_position ADD COLUMN IF NOT EXISTS currency TEXT;
UPDATE fin_position SET currency = 'CNY' WHERE currency IS NULL;
ALTER TABLE fin_position ALTER COLUMN currency SET DEFAULT 'CNY';
ALTER TABLE fin_position ALTER COLUMN currency SET NOT NULL;
ALTER TABLE fin_position DROP CONSTRAINT IF EXISTS fin_position_currency_check;
ALTER TABLE fin_position ADD  CONSTRAINT fin_position_currency_check
  CHECK (currency IN ('CNY','HKD','USD'));

-- ── fin_valuation ────────────────────────────────────────────────────────
ALTER TABLE fin_valuation ADD COLUMN IF NOT EXISTS currency TEXT;
UPDATE fin_valuation SET currency = 'CNY' WHERE currency IS NULL;
ALTER TABLE fin_valuation ALTER COLUMN currency SET DEFAULT 'CNY';
ALTER TABLE fin_valuation ALTER COLUMN currency SET NOT NULL;
ALTER TABLE fin_valuation DROP CONSTRAINT IF EXISTS fin_valuation_currency_check;
ALTER TABLE fin_valuation ADD  CONSTRAINT fin_valuation_currency_check
  CHECK (currency IN ('CNY','HKD','USD'));
