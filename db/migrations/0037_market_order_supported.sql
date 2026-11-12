-- ════════════════════════════════════════════════════════════════════════
-- 0037_market_order_supported.sql · 智能炒股三期 · 市价单能力落到市场规则表
--
-- 依据：`plan/拍板-2026-10-03-港美股必须可交易.md` §四「三市场成交口径（本期定稿）」
--
--   | 市场   | 限价单     | 市价单     |
--   | CN_A   | ✅ 沿用一期 | ✅ 沿用一期 |
--   | HK     | ✅ 必做     | 本期不做    |
--   | US     | ✅ 必做     | 本期不做    |
--
-- 「哪几个市场接市价单」是一张**按市场的表**，不是散在代码里的 `market != "CN_A"`
-- （P2 任务书 §一.4：策略 / 执行里任何一处按 A 股写死，都要改成按市场取参数）。
-- 这张表就是那个参数的家（`fin_market_rule` 已经是「每个市场一行」的规则表）。
--
-- 语义：
--   · `true`  = 该市场有对手价盘口，市价单按「对手价 ± 滑点」撮合（`matching/pricing.py`）；
--   · `false` = 该市场数据源没有盘口（快照 `quote_quality='last_only'`），市价单**拒绝**
--               并留痕（`matching/engine.py`），请改用限价单 —— 不拿最新价冒充买一/卖一。
--
-- ⚠️ 本文件对**已有部署不生效**（`docker-entrypoint-initdb.d` 只在数据卷首次初始化时执行）；
--    真正生效的是 `apps/api/app/migrate.py` 的增量迁移（api 启动时跑 `db/migrations`）。
--    本文件同时是**留档 + 全新安装用**，两处口径必须一致。
--
-- 幂等：`ADD COLUMN IF NOT EXISTS` + `UPDATE`（只把 CN_A 置 true，不改其它行）。
-- ════════════════════════════════════════════════════════════════════════

ALTER TABLE fin_market_rule
  ADD COLUMN IF NOT EXISTS market_order_supported BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN fin_market_rule.market_order_supported IS
  '该市场是否支持市价单（拍板 §四）：CN_A=true（有对手价盘口）；'
  'HK/US=false（数据源无盘口 quote_quality=last_only，市价单拒绝并留痕）。'
  '参数化「market != ''CN_A''」这条 A 股写死，改这一列即可，不改代码。';

-- CN_A 沿用一期（有盘口）。HK / US 保持默认 false —— 与拍板 §四逐项一致。
-- 幂等：重复执行只是把已经是 true 的行再置一次 true。
UPDATE fin_market_rule SET market_order_supported = true WHERE market = 'CN_A';
