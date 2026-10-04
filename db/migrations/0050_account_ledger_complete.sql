-- ════════════════════════════════════════════════════════════════════════
-- 0050_account_ledger_complete.sql · 智能炒股 · 五期 L05 · 把账本补完整
--
-- 依据：`plan/00五期方案/…五期(闭环补齐)…md` §二 第 6 条 / §2.5 / §四 `L05`；
--       `plan/00五期方案/AI_炒股智能体系统_开源组件整合与技术实施方案.md` §7.3
--       （账本与规则要求：市场规则含「价格精度」、成本模型含「成交量约束」、
--        公司行为含「分红、拆合股等适用事件及账务处理」）；`plan/L05.md` §一。
--
-- 现状（方案 §2.5 实测）：
--   · 停牌：无表无列（`fin_instrument.is_active` / `is_st` 存在但风控不读）；
--   · 公司行为：全仓无 `dividend`/`split`/`corporate_action` 的表或列；
--   · 逐市场 tick：`fin_execution_model.tick_size` 是**全局单值**（默认 0.01），
--     `fin_market_rule` 里没有 tick 列。
--
-- 本迁移**只做加法**（`ADD COLUMN IF NOT EXISTS` / `CREATE TABLE IF NOT EXISTS` /
-- `UPDATE ... WHERE 列为空` 的种子）；**不带 `BEGIN;` / `COMMIT;`**（`app/migrate.py`
-- 的 `apply_one` 已把每个文件包在独立事务里）；可重复执行；**不改历史行**。
--
-- ⛔ 红线 5（不许编数字）：**停牌 / 公司行为没有数据源**，本迁移只建**状态位 + 只追加
--    事件表**，置位一律走**人工登记入口**（`paper` 的 `POST /api/v1/instruments/{code}/halt`
--    与 `POST /api/v1/corporate-actions`）；`halted_source` / `source` 列如实写 `'manual'`。
--    **绝不在迁移里编造任何停牌 / 分红 / 拆股数据。**
-- ⛔ 逐市场 tick：港股逐价位区间表来自 HKEX 公布的证券价差表（见下）；A 股 / 美股
--    为固定值 / 分档，来源逐行写进 `tick_spec.source`。
--
-- 类型口径沿用 `0023`：金额 NUMERIC(18,4) · 价格 NUMERIC(12,4) · 比例 NUMERIC(9,6) ·
--   时间 TIMESTAMPTZ；新增 tick 列用 NUMERIC(12,6)（港股最小价差 0.001，四位不够表达
--   更细的档；美股 sub-penny 0.0001 也放得下）。全表**不出现 float / double / REAL**。
-- ════════════════════════════════════════════════════════════════════════


-- ── ① 停牌状态位（`fin_instrument`）─────────────────────────────────────
--   一行标的的停牌事实。**数据源未接** → 由人工登记口置位（`halted_source='manual'`）。
--   风控引擎读 `halted`：为真即拒单（理由写清「停牌」），见 `app/risk/engine.py`。
ALTER TABLE fin_instrument ADD COLUMN IF NOT EXISTS halted BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE fin_instrument ADD COLUMN IF NOT EXISTS halted_reason TEXT;    -- 为什么停（人工填）
ALTER TABLE fin_instrument ADD COLUMN IF NOT EXISTS halted_at TIMESTAMPTZ; -- 何时登记停牌
ALTER TABLE fin_instrument ADD COLUMN IF NOT EXISTS halted_source TEXT;    -- 'manual'（本部署恒此值）

COMMENT ON COLUMN fin_instrument.halted IS
  '停牌状态位（L05）。**数据源未接** → 由人工登记口置位，halted_source 如实写 manual。'
  '风控读它：halted=true 即拒单。';


-- ── ② 逐市场 tick（最小变动价位）────────────────────────────────────────
--   两级：标的级覆盖（`fin_instrument.tick_size`，可空）+ 市场级价差表
--   （`fin_market_rule.tick_spec`，JSONB）。解析顺序（见 `app/tick.py`）：
--     标的 tick_size → 市场 tick_spec（fixed / bands）→ 全局 fin_execution_model.tick_size（回落）。
--   老数据（两级都没配）**回落全局值**并如实标注，行为不变。
ALTER TABLE fin_instrument ADD COLUMN IF NOT EXISTS tick_size NUMERIC(12,6);
ALTER TABLE fin_market_rule ADD COLUMN IF NOT EXISTS tick_spec JSONB;

COMMENT ON COLUMN fin_market_rule.tick_spec IS
  '逐市场最小变动价位（L05）。形状：{"mode":"fixed","tick":0.01} 或 '
  '{"mode":"bands","bands":[{"lt":0.25,"tick":0.001},…,{"tick":5}]}（bands 按价格升序，'
  '第一条 price < lt 命中，末条无 lt = 上无界）。解析见 app/tick.py，回落全局 execution model。';

-- 种子（幂等：只填空行，不覆盖已落地的行 —— 迁移文件一经发布不可变）。
-- ── CN_A：固定 0.01 元（沪深交易所报价单位；与 0025 的全局默认同一事实）──
UPDATE fin_market_rule
   SET tick_spec = '{"mode":"fixed","tick":0.01,"source":"上交所/深交所交易规则：A 股申报价格最小变动单位 0.01 元人民币"}'::jsonb
 WHERE market = 'CN_A' AND tick_spec IS NULL;

-- ── HK：HKEX 公布的最小价差表（证券，逐价位区间）────────────────────────
--   来源：香港交易所《交易所规则》附表 —— 证券（除债券/ETP 另有规定）的最小价差。
--   ⚠ 本表按 HKEX 公布口径逐档落表；**上线前须联网复核一次**（见成果文档「未接数据源清单」）。
UPDATE fin_market_rule
   SET tick_spec = '{"mode":"bands","source":"HKEX《交易所规则》证券最小价差表（逐价位区间）","bands":[
        {"lt":0.25,"tick":0.001},{"lt":0.5,"tick":0.005},{"lt":10,"tick":0.01},
        {"lt":20,"tick":0.02},{"lt":100,"tick":0.05},{"lt":200,"tick":0.1},
        {"lt":500,"tick":0.2},{"lt":1000,"tick":0.5},{"lt":2000,"tick":1.0},
        {"lt":5000,"tick":2.0},{"tick":5.0}]}'::jsonb
 WHERE market = 'HK' AND tick_spec IS NULL;

-- ── US：SEC Rule 612（sub-penny rule）—— 价格 < $1.00 用 $0.0001，≥ $1.00 用 $0.01 ──
UPDATE fin_market_rule
   SET tick_spec = '{"mode":"bands","source":"SEC Rule 612 (sub-penny)：≥$1.00 最小报价单位 $0.01；<$1.00 允许 $0.0001","bands":[
        {"lt":1.0,"tick":0.0001},{"tick":0.01}]}'::jsonb
 WHERE market = 'US' AND tick_spec IS NULL;


-- ── ③ 公司行为事件表（**只追加**）───────────────────────────────────────
--   一行 = 一次公司行为（分红 / 拆合股 / 送股）及其**实际应用**的账务效果。
--   **数据源未接** → 由人工登记口写入（`source='manual'`），登记即应用（`applied_at`）。
--   `qty_delta` / `cash_delta` 记下这次登记对**账本**的净影响（股数 / 现金），
--   对账的「持仓 = 成交净额」据此把公司行为算进去（否则一次拆股就会让对账不平）。
--
--   为什么事件表也存效果值（qty_delta / cash_delta）：账本效果要**可复算** ——
--   只看 (type, ratio, cash_per_share) 得重放全序列才能推当时的持仓，存下实际应用值
--   让对账与审计都能一眼核。事件表**只追加**（触发器挡 UPDATE / DELETE）。
CREATE TABLE IF NOT EXISTS fin_corporate_action (
  action_id      TEXT PRIMARY KEY,               -- ca_<24hex>
  project_id     TEXT NOT NULL REFERENCES fin_project(project_id),  -- 应用在哪个项目
  code           TEXT NOT NULL,                  -- 标的
  market         TEXT NOT NULL CHECK (market IN ('CN_A','HK','US')),
  currency       TEXT NOT NULL CHECK (currency IN ('CNY','HKD','USD')),
  action_type    TEXT NOT NULL CHECK (action_type IN ('dividend','split','bonus')),
  ex_date        DATE NOT NULL,                  -- 除权除息日（该市场当地日）
  cash_per_share NUMERIC(18,6),                  -- 分红：每股现金（本币，税前）；其余 NULL
  ratio          NUMERIC(18,8),                  -- 拆合股/送股：比例；分红 NULL
  qty_delta      INTEGER NOT NULL DEFAULT 0,     -- 本次对**该项目持仓**股数的净影响（拆/送股有值）
  cash_delta     NUMERIC(18,4) NOT NULL DEFAULT 0,  -- 本次对**该项目现金**的净影响（分红有值）
  source         TEXT NOT NULL,                  -- 来源（本部署恒 'manual' —— 数据源未接）
  registered_by  TEXT NOT NULL,                  -- 谁登记的
  registered_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  applied_at     TIMESTAMPTZ,                    -- 账务实际生效时刻（登记即应用则为登记时刻）
  memo           TEXT
);
CREATE INDEX IF NOT EXISTS fin_corporate_action_proj_code
  ON fin_corporate_action (project_id, code, ex_date DESC);
CREATE INDEX IF NOT EXISTS fin_corporate_action_market_time
  ON fin_corporate_action (market, registered_at DESC);

COMMENT ON TABLE fin_corporate_action IS
  '公司行为事件表（L05 · 只追加）。一行 = 一次公司行为**在某个项目上**的登记与应用'
  '（qty_delta/cash_delta 是项目级的实际账本效果）。**数据源未接** → 人工登记口写入'
  '（source=manual），登记即应用。对账据此把公司行为算进持仓净额。';

-- 只追加：改 / 删一律抛异常（照 `0043` / `0049` 的同款触发器）。
CREATE OR REPLACE FUNCTION fin_corporate_action_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'fin_corporate_action 只追加：% 被拒绝（公司行为事件不可改、不可删；纠正=再登一条反向事件）', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS fin_corporate_action_immutable ON fin_corporate_action;
CREATE TRIGGER fin_corporate_action_immutable
  BEFORE UPDATE OR DELETE ON fin_corporate_action
  FOR EACH ROW EXECUTE FUNCTION fin_corporate_action_immutable();


-- ── 权限（照 `0023` §一：新表必须显式授权，不给默认权限）─────────────────
--   `fin_corporate_action` 是**账本级的追加事件表** → 只 SELECT / INSERT（不给 UPDATE / DELETE）。
--   `fin_instrument` / `fin_market_rule` 是参考数据（状态会变）→ 早已有 UPDATE 授权（0023/0030），
--   新增列随表级 GRANT 自动覆盖，无需再授。
GRANT SELECT, INSERT ON fin_corporate_action TO fin_paper_rw;
