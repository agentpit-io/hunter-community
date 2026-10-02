-- ════════════════════════════════════════════════════════════════════════
-- 0034_schedule_ledger_market.sql · 智能炒股二期 · 账本按市场分子账户 + 港美股费率行
--
-- 依据：`plan/N4.md` §一.2/§一.3（本币记账、子账户隔离）
--       + `plan/ref/11-美股港股模拟交易需求分析与技术实施方案.md` §3.1 A 方案
--         （分市场独立账本、各自本币记账、账本内永不折算）/ §3.3（费率一律落表 + 标来源）
--       + `plan/拍板-2026-10-03-港美股必须可交易.md` §四（三市场成交口径）
--
-- **只做加法，不删不改历史数据。** 三条都照本仓迁移规范：
--   · 一律 `ADD COLUMN IF NOT EXISTS`；新增列先建（可空）→ 回填现状 → `SET DEFAULT`
--     + `SET NOT NULL`，重复执行是空操作；
--   · 主键变更用 DO 块判当前主键形状，换过之后重复执行是空操作；
--   · 只放松 / 补约束，收紧的只有「新列 NOT NULL」这一处，且回填后必然通过。
--
-- ⚠️ **已有部署不会执行本文件**（compose 只在数据卷首次初始化时跑 `db/migrations`）。
--   真正随代码生效的是 `apps/api` 启动时的 `app.migrate`（读同一目录）——
--   本机 dev 库需手工执行一次（见 N4 成果报告）。
--
-- 现状（改动前实测 · 2026-10-03）：账本五表全部 A 股（`fin_order` 437 /
--   `fin_trade` 260 / `fin_cash_ledger` 1182 / `fin_position` 221 / `fin_valuation` 30 行，
--   来源均为一期 A 股模拟盘）→ 回填 `'CN_A'` 是**事实，不是猜**（红线 §六.1）。
-- ════════════════════════════════════════════════════════════════════════


-- ── fin_order / fin_trade：加市场（账本按市场分子账户）────────────────────
ALTER TABLE fin_order ADD COLUMN IF NOT EXISTS market TEXT;
UPDATE fin_order SET market = 'CN_A' WHERE market IS NULL;
ALTER TABLE fin_order ALTER COLUMN market SET DEFAULT 'CN_A';
ALTER TABLE fin_order ALTER COLUMN market SET NOT NULL;
ALTER TABLE fin_order DROP CONSTRAINT IF EXISTS fin_order_market_check;
ALTER TABLE fin_order ADD  CONSTRAINT fin_order_market_check
  CHECK (market IN ('CN_A','HK','US'));

ALTER TABLE fin_trade ADD COLUMN IF NOT EXISTS market TEXT;
UPDATE fin_trade SET market = 'CN_A' WHERE market IS NULL;
ALTER TABLE fin_trade ALTER COLUMN market SET DEFAULT 'CN_A';
ALTER TABLE fin_trade ALTER COLUMN market SET NOT NULL;
ALTER TABLE fin_trade DROP CONSTRAINT IF EXISTS fin_trade_market_check;
ALTER TABLE fin_trade ADD  CONSTRAINT fin_trade_market_check
  CHECK (market IN ('CN_A','HK','US'));


-- ── fin_cash_ledger：加市场 + 索引按 (project, market) ───────────────────
-- 子账户 = `(project_id, market)`：可用 / 冻结是**该子账户**的流水最后一行的 *_after，
-- 于是「A 股子账户买入」只动 CN_A 那本流水，港美股子账户的可用资金一分不变。
ALTER TABLE fin_cash_ledger ADD COLUMN IF NOT EXISTS market TEXT;
UPDATE fin_cash_ledger SET market = 'CN_A' WHERE market IS NULL;
ALTER TABLE fin_cash_ledger ALTER COLUMN market SET DEFAULT 'CN_A';
ALTER TABLE fin_cash_ledger ALTER COLUMN market SET NOT NULL;
ALTER TABLE fin_cash_ledger DROP CONSTRAINT IF EXISTS fin_cash_ledger_market_check;
ALTER TABLE fin_cash_ledger ADD  CONSTRAINT fin_cash_ledger_market_check
  CHECK (market IN ('CN_A','HK','US'));

-- 子账户余额查询的支撑索引（原索引 (project_id, entry_id) 保留，老查询照走）
CREATE INDEX IF NOT EXISTS fin_cash_proj_market_time
  ON fin_cash_ledger (project_id, market, entry_id);


-- ── fin_position：加市场（code 本身已隐含市场，加列是为了显式按市场查 / 报表）──
ALTER TABLE fin_position ADD COLUMN IF NOT EXISTS market TEXT;
UPDATE fin_position SET market = 'CN_A' WHERE market IS NULL;
ALTER TABLE fin_position ALTER COLUMN market SET DEFAULT 'CN_A';
ALTER TABLE fin_position ALTER COLUMN market SET NOT NULL;
ALTER TABLE fin_position DROP CONSTRAINT IF EXISTS fin_position_market_check;
ALTER TABLE fin_position ADD  CONSTRAINT fin_position_market_check
  CHECK (market IN ('CN_A','HK','US'));


-- ── fin_valuation：加市场，主键 (project_id, as_of) → (project_id, market, as_of) ──
-- 估值是**子账户级**的：`total_assets = 可用 + 冻结 + 市值` 只在该市场子账户内部成立
-- （设计 §3.1 A 方案：账本内永不折算）。一个项目三个市场各一行估值，同日共存。
ALTER TABLE fin_valuation ADD COLUMN IF NOT EXISTS market TEXT;
UPDATE fin_valuation SET market = 'CN_A' WHERE market IS NULL;
ALTER TABLE fin_valuation ALTER COLUMN market SET DEFAULT 'CN_A';
ALTER TABLE fin_valuation ALTER COLUMN market SET NOT NULL;
ALTER TABLE fin_valuation DROP CONSTRAINT IF EXISTS fin_valuation_market_check;
ALTER TABLE fin_valuation ADD  CONSTRAINT fin_valuation_market_check
  CHECK (market IN ('CN_A','HK','US'));

DO $$
DECLARE
  pk_cols int;
BEGIN
  SELECT array_length(conkey, 1) INTO pk_cols
    FROM pg_constraint
   WHERE conrelid = 'fin_valuation'::regclass AND contype = 'p';
  IF pk_cols = 2 THEN
    ALTER TABLE fin_valuation DROP CONSTRAINT fin_valuation_pkey;
    ALTER TABLE fin_valuation
      ADD CONSTRAINT fin_valuation_pkey PRIMARY KEY (project_id, market, as_of);
  END IF;
END $$;


-- ── fin_fee_model：transfer 方向 + 来源/备注列 ───────────────────────────
-- 港美股的费用项方向各不相同：美股 SEC 规费 / FINRA TAF **仅在卖出时收**；
-- 港股三项征费买卖都收。原来 `compute_fee` 无条件按买卖两侧收 transfer，
-- 这里把方向参数化（缺省 'both' = 一期 A 股过户费口径，行为逐字不变）。
ALTER TABLE fin_fee_model ADD COLUMN IF NOT EXISTS transfer_fee_side TEXT;
UPDATE fin_fee_model SET transfer_fee_side = 'both' WHERE transfer_fee_side IS NULL;
ALTER TABLE fin_fee_model ALTER COLUMN transfer_fee_side SET DEFAULT 'both';
ALTER TABLE fin_fee_model ALTER COLUMN transfer_fee_side SET NOT NULL;
ALTER TABLE fin_fee_model DROP CONSTRAINT IF EXISTS fin_fee_model_transfer_side_check;
ALTER TABLE fin_fee_model ADD  CONSTRAINT fin_fee_model_transfer_side_check
  CHECK (transfer_fee_side IN ('buy','sell','both','none'));

-- 费率来源（设计 §3.3：费率一律落表 + 标来源 + 标生效日；报告里要能说清用的是哪一版）
ALTER TABLE fin_fee_model ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE fin_fee_model ADD COLUMN IF NOT EXISTS note   TEXT;

-- A 股费率行的来源补上（一期口径的事实陈述，不是猜测）
UPDATE fin_fee_model
   SET source = COALESCE(source,
         '一期 A 股费率口径 fee-cn-a-v1（0023/0025 落表）：佣金万2.5+最低5元 · 印花税千1(卖出) · 过户费万0.1')
 WHERE market = 'CN_A' AND source IS NULL;


-- ── 港美股费率行（拍板 §四：三市场都要能成交 → 必须有可用费用模型）───────
--
-- ⚠️ **来源与口径如实标注**（红线 §六.1「不许编数字」）：
--   本仓 `apps/api/app/services/quant/broker/defaults.py`（2026-08-29 落盘的
--   broker 默认参数）已给出港美股现行成本口径 —— 港股「佣金万3 · 印花税千1(双向)
--   · 证监会+结算+交易 三项合计约万0.6」、美股「佣金约 1bps · SEC+FINRA+TAF 卖出
--   规费约 0.2bps」。本批费率行**取自该文件**（仓内既有、有日期的口径），
--   **不是另编**；生效日取该文件的落盘日 2026-08-29。
--
-- ⚠️ **已知口径局限（如实记录、不静默）**：该文件是**回测级近似**（三项征费合成
--   一个 bps），没有逐项列出交易所 / 监管公布的分项费率，也没有港股结算费的
--   min/max、美股 SEC 逐年费率。设计 §3.3 要求的「按交易所/监管公布口径分项落行」
--   是一个**独立立项**（N2 §五-1 / N3 §五-1 已记）。本行先让港美股**能成交**，
--   报告与回执按 `source` / `note` 如实标注这些局限。
INSERT INTO fin_fee_model
  (version, commission_pct, commission_min, stamp_tax_pct, transfer_fee_pct,
   market, currency, stamp_side, transfer_fee_side, effective_from, source, note)
VALUES
-- ── HK · 港股 ────────────────────────────────────────────────────────────
(
  'fee-hk-v1', 0.000300, 0.0000, 0.001000, 0.000060,
  'HK', 'HKD', 'both', 'both', TIMESTAMPTZ '2026-08-29T00:00:00Z',
  'apps/api/app/services/quant/broker/defaults.py HK_DEFAULT（2026-08-29）',
  '港股回测级近似：佣金万3 · 印花税千1(买卖双边) · 证监会交易征费+联交所交易费+结算费三项合计约万0.6(买卖双边)。'
  '局限：三项征费合并为一个费率，未列分项公布费率，未含结算费 min(HK$2)/max(HK$100)。'
  '正式分项模型待单独立项（N2 §五-1）。'
),
-- ── US · 美股 ────────────────────────────────────────────────────────────
(
  'fee-us-v1', 0.000100, 0.0000, 0.000000, 0.000020,
  'US', 'USD', 'none', 'sell', TIMESTAMPTZ '2026-08-29T00:00:00Z',
  'apps/api/app/services/quant/broker/defaults.py US_DEFAULT（2026-08-29）',
  '美股回测级近似：佣金约 1bps · SEC 规费+FINRA 交易活动费（仅卖出）约 0.2bps。'
  '局限：SEC 费率逐年调整，本行未按 effective_from 分年落行；交易活动费未按「每股 + 上限」表达。'
  '正式分项模型待单独立项（N2 §五-1）。'
)
ON CONFLICT (version) DO NOTHING;


-- ── 港股交易时段补上**收市竞价（CAS 16:00–16:10）** ──────────────────────
--
-- 依据：香港交易所「收市竞价交易时段」（Closing Auction Session）——
--   持续交易时段 09:30–12:00 / 13:00–16:00 之后，16:00–16:10 为收市竞价
--   （16:08–16:10 随机收市，收盘价在此产生）。`fin_market_rule.sessions` 原来
--   只写了**持续交易时段**（0030 种子），于是数据源给的港股最新价 ——
--   **它本来就是收市竞价的成交打印**（实测腾讯 `00700.HK` `event_time` =
--   `2026-10-02T16:08:10+08:00`）—— 永远落在时段之外，港股**一笔都成交不了**
--   （N3 §五-5 记录、N4 复核：`不在交易时段内（16:08 香港时间…）`）。
--
-- **N4 的决定**（无人值守默认口径：最保守、最可核对 —— 见 N4 成果报告）：
--   把 CAS 计入港股可交易时段。这不是放宽风控：CAS 是港交所官方交易日的
--   组成部分，且券商模拟盘按收市价撮合是常规近似。**只动 HK**，A 股 / 美股不变。
--   ⚠️ 若用户不认这条口径，回滚只需把 HK 的 sessions 写回 0030 的种子值。
--
-- 幂等：`UPDATE ... WHERE market='HK'` 重复执行结果相同（幂等 UPDATE）。
UPDATE fin_market_rule
   SET sessions = '[{"open":"09:30","close":"12:00"},{"open":"13:00","close":"16:00"},{"open":"16:00","close":"16:10"}]'::jsonb
 WHERE market = 'HK'
   AND sessions = '[{"open":"09:30","close":"12:00"},{"open":"13:00","close":"16:00"}]'::jsonb;

-- 已有的 HK 日历行（由 0030 种子 / 港股日历同步写入）同步补上 CAS，
-- 否则「市场规则说能交易、日历说不能」两处打架。幂等同上。
UPDATE fin_market_calendar
   SET sessions = sessions || '[{"open":"16:00","close":"16:10"}]'::jsonb
 WHERE market = 'HK'
   AND is_trading
   AND NOT (sessions @> '[{"close":"16:10","open":"16:00"}]'::jsonb);


-- ── 港美股市场规则行：把费用模型指到本批落下的费率行 ─────────────────────
--
-- `0030` 落 HK / US 市场规则行时，费率行还没落数，所以两行是
-- `fee_model_version = NULL`、`source` 里写着「费用模型=unavailable」。
-- N4 在本文件前面插了 `fee-hk-v1` / `fee-us-v1`，这里把指针接上，
-- 并在 `source` 末尾追加一条**带日期的说明**（不覆写原始来源，保留溯源）。
UPDATE fin_market_rule
   SET fee_model_version = 'fee-hk-v1',
       source = source || ' ｜N4(2026-10-03) 更正：费用模型已落表 fee-hk-v1（回测级近似，来源与局限见 fin_fee_model.source/note）；'
                         || '交易时段补上收市竞价 16:00–16:10（HKEX CAS，见本迁移末段）。'
 WHERE market = 'HK' AND (fee_model_version IS NULL OR fee_model_version <> 'fee-hk-v1');

UPDATE fin_market_rule
   SET fee_model_version = 'fee-us-v1',
       source = source || ' ｜N4(2026-10-03) 更正：费用模型已落表 fee-us-v1（回测级近似，来源与局限见 fin_fee_model.source/note）。'
 WHERE market = 'US' AND (fee_model_version IS NULL OR fee_model_version <> 'fee-us-v1');
