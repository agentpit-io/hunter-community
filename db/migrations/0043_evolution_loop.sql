-- ════════════════════════════════════════════════════════════════════════
-- 0043_evolution_loop.sql · 智能炒股 · 受控自进化 · 提案层（第四段 R6）
--   四张表：提案 / 冻结计划（不可变）/ 影子事件（追加）/ 状态事件（追加 · 审计权威）
--
-- 依据：`plan/R6.md` §一 · `plan/记忆系统追加规则.md` §四 / §五 第 7-10、13 条 ·
--       `00记忆新方案/03个人本地开源部署_记忆与自进化代码修改方案.md` §4-B ·
--       `00记忆新方案/01记忆系统_对照2026新方案_调整建议.md` §八 8.2（DDL 草案，**按 `03` 的六条修订落**）。
--
-- 执行：`app.migrate` 在 api 每次启动时按文件名顺序执行（`boot.sh` → `python -m app.migrate`）。
--       不挂 `docker-entrypoint-initdb.d`，不手工跑、不要改老文件。
--
-- ⚠️ 三个"只做加法"的纪律（`记忆系统追加规则.md` §四）：
--     `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` / `DROP … IF EXISTS` 再建；
--     **不带 `BEGIN;` / `COMMIT;`** —— `app/migrate.py:apply_one` 已把每个文件包在独立事务里
--     （失败整体回滚 + 不记账），文件内再写事务边界会破坏那个承诺。
--     可重复执行、不改历史行。
--
-- ── 与 `01 §8.2` 草案的三处**有意修订**（`03 §7` 的六条里第 3、4 条）────────────
--   1. `01` 只有 proposal + validation 两张表，且 proposal 一面说"只追加"一面 `UPDATE status`。
--      本文件**拆开**：不可变 `fin_evolution_plan` + 追加式 `fin_evolution_event`；
--      `proposal.status` 只是**可重建投影**，审计一律以事件表为准（表头见下）。
--   2. `01` 的"零 GRANT 使 plan 不可改"在应用以 owner/superuser 连接时不成立（本仓 api 实测
--      连的是 superuser `hunter`，见 R6 成果文档）。本文件改用**数据库触发器**拒改 +
--      事件表**哈希链**兜底审计；**不宣称**对机器所有者"绝对不可篡改"。
--   3. `01` 的 `aggregate_basis` / `base_strategy_key` 等列按 `03` 的修订替换为
--      `evidence_snapshot_id`（冻结晶证据集）+ `base_config_hash` / `candidate_config_hash`。
--
-- 类型口径沿用 `0023` / `0041`：比例类 NUMERIC(18,8)；时间一律 TIMESTAMPTZ；
--   全表**不许出现 float / double / REAL**。主键一律 TEXT（业务身份推导，非随机数）。
-- ════════════════════════════════════════════════════════════════════════


-- ── 表一：提案（`fin_evolution_proposal`）────────────────────────────────
-- 一条提案 = 「拿一批经验当依据，把配置从 base 改到 candidate」。它**本身可改的只有 status**
-- （那是事件的投影，见下），其余列写入即定。
--
-- 为什么要 `base_config_hash` **和** `candidate_config_hash` 两列：评审要能回答
-- "这条提案是基于哪一版配置提的、想改成哪一版" —— 只存 diff 答不出（diff 脱离 base 无意义）。
-- `param_diff` 是**服务端机器算出来的**（拿 base 与 candidate 逐字段比对），
-- **不采信写入方传进来的 diff**（写入口径在 `services/fin/evolution.py`）。
CREATE TABLE IF NOT EXISTS fin_evolution_proposal (
  proposal_id            TEXT PRIMARY KEY,                    -- evp_<24hex>
  project_id             TEXT NOT NULL REFERENCES fin_project(project_id),
  -- 依据（判定书：提案不许无据）。**≥1 条** `fin_experience.experience_id`；
  -- **失败经验（polarity='refute'）同样纳入** —— 自进化的燃料一半是"什么不行"。
  evidence_refs          TEXT[] NOT NULL,
  -- 冻结证据集（`fin_memory_snapshot.memory_snapshot_id`）：证据必须来自**同一份冻结晶**，
  -- 不是"此刻查得到的那批"。可空是留后路（本轮的写入口强制要求给）。
  evidence_snapshot_id   TEXT,
  base_config_hash       TEXT NOT NULL,                       -- 基线配置的规范哈希
  candidate_config_hash  TEXT NOT NULL,                       -- 候选**完整配置**的规范哈希
  param_diff             JSONB NOT NULL,                      -- [{field, old, new}]，只列变化字段
  regime_tags            TEXT[] NOT NULL DEFAULT '{}',        -- 提案依据的市场状态
  rule_version           TEXT,                                -- regime 判定器规则版本键
  proposal_algo_version  TEXT NOT NULL,                       -- 提案算法版本键（白名单版本）
  target                 TEXT NOT NULL CHECK (target IN ('strategy','risk')),
  -- **服务端算，不采信写入方**：risk 走 `risk.compare()`，strategy 走白名单每参数的方向语义。
  direction              TEXT NOT NULL CHECK (direction IN ('tighten','loosen','mixed')),
  -- ⚠️ 只是**投影**：权威是 `fin_evolution_event`。每次变更**必须同时追加一条事件**，
  --    两处不一致的行数应为 0（验收项，`evolution.projection_mismatches`）。
  status                 TEXT NOT NULL DEFAULT 'draft',
  rationale              TEXT NOT NULL,                       -- 人可读理由（LLM 只写字，不编数）
  created_by             TEXT NOT NULL,                       -- 'fin-worker' 或 'user:<uuid>'
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (status IN ('draft','validating','passed','rejected','applied','rolled_back','inconclusive')),
  CHECK (cardinality(evidence_refs) >= 1),
  -- **红线 8 的数据库层兜底**：AI 无权放宽风控。服务端在 `evolution.py` 已先拦一次，
  -- 这一条是"就算应用代码写错也进不来"的第二道锁。
  CONSTRAINT fin_evo_risk_never_loosen CHECK (target <> 'risk' OR direction <> 'loosen')
);
-- 项目维度的列表 / 审计主索引。
CREATE INDEX IF NOT EXISTS fin_evolution_proposal_by_project
  ON fin_evolution_proposal (project_id, created_at DESC);
-- 同一项目 · 同一 base_config_hash **同时最多一个待验证提案**（`draft` / `validating`）。
-- 为什么是**部分**唯一索引：历史提案（passed / rejected / …）可以堆很多条，只拦"在飞的"。
CREATE UNIQUE INDEX IF NOT EXISTS fin_evolution_proposal_one_pending
  ON fin_evolution_proposal (project_id, base_config_hash)
  WHERE status IN ('draft','validating');


-- ── 表二：冻结计划（`fin_evolution_plan` · **不可变**）─────────────────────
-- 一条提案对应**一份**验证计划（`UNIQUE (proposal_id)`）。**写入即冻结**：
-- `plan_hash` 写后不变，**没有 UPDATE / DELETE 授权路径**（见本文件末的触发器）。
--
-- ⚠️ 具体阈值（`window_days` / `min_comparable_sample` / `pass_line` / `fail_line` /
--    `rollback_line` …）**由 `evolution.py` 的实现时默认值给**，默认值与计算依据
--    写在 `docs/开发文档/R6-*.md` 与 `evolution.DEFAULT_PLAN`。这里**只保证它们能被写、
--    不能被改**（红线 10：验证口径写入即冻结；一次失败不得事后改 plan —— 改口径 = 新提案）。
CREATE TABLE IF NOT EXISTS fin_evolution_plan (
  plan_id                TEXT PRIMARY KEY,                    -- evpl_<24hex>
  proposal_id            TEXT NOT NULL REFERENCES fin_evolution_proposal(proposal_id),
  metric                 TEXT NOT NULL,                       -- 主指标（如 portfolio_net_return）
  window_days            INTEGER NOT NULL,                    -- 验证窗口（交易日）
  min_comparable_sample  INTEGER NOT NULL,                    -- 最小**可比样本**（红线 11）
  cost_model             TEXT NOT NULL,                       -- 成本模型版本键
  slippage_model         TEXT NOT NULL,                       -- 滑点模型版本键
  data_source_version    TEXT NOT NULL,                       -- 数据源版本键
  pass_line              NUMERIC(18,8),                       -- 通过线（可空 = 未定）
  fail_line              NUMERIC(18,8),                       -- 失败线（可空 = 未定）
  early_stop_condition   JSONB,                               -- 提前停止条件
  rollback_line          JSONB,                               -- 观察期回滚线（预注册）
  plan_hash              TEXT NOT NULL,                       -- 计划规范哈希（写后不变）
  frozen_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS fin_evolution_plan_by_proposal
  ON fin_evolution_plan (proposal_id);


-- ── 表三：影子事件（`fin_evolution_shadow_event` · 追加）──────────────────
-- ⚠️ **本轮（R6）只建表，写入在 R7。**
-- 这张表与 `fin_trade` **毫无关系**（`03 §4-C`）：`fin_trade` 只记真实 / 现行策略成交，
-- **绝不用它冒充候选策略的收益**。候选臂在独立模拟账本里跑，两臂同行情快照 / 同初始资金 /
-- 同费率滑点（红线 11）。`validation_id` 由 R7 生成（本轮不设外键，R7 再定它与 plan 的关系）。
--
-- 唯一键 `UNIQUE (validation_id, arm, trade_date, point, symbol)` 保证**重试不重复记账**
-- （断电 / 重启后按业务唯一键恢复未完成窗口）。
CREATE TABLE IF NOT EXISTS fin_evolution_shadow_event (
  shadow_event_id  TEXT PRIMARY KEY,                          -- evsh_<24hex>
  validation_id    TEXT NOT NULL,                             -- R7 生成；本轮不设外键
  arm              TEXT NOT NULL CHECK (arm IN ('incumbent','candidate')),
  trade_date       DATE NOT NULL,
  point            TEXT NOT NULL,                             -- 决策时点（开盘/收盘/…）
  symbol           TEXT NOT NULL,                             -- 规范标的 <MARKET>:<CODE>
  quote_as_of      TIMESTAMPTZ,                               -- 行情时间戳（两臂同快照）
  signal           TEXT,                                      -- 信号 / 意图
  filled           BOOLEAN,                                   -- 是否模拟成交
  reject_reason    TEXT,                                      -- 拒单原因
  fee              NUMERIC(18,8),
  slippage         NUMERIC(18,8),
  position_ref     TEXT,                                      -- 持仓引用
  valuation_ref    TEXT,                                      -- 估值引用
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (validation_id, arm, trade_date, point, symbol)
);


-- ── 表四：状态事件（`fin_evolution_event` · 追加 · **审计权威**）───────────
-- 状态转换 / 验证结果 / 人工确认 / 生效 / 回滚 / **闸门拒绝**都追加一行。
-- `prev_hash` / `event_hash` 构成**哈希链**（`03 §7.3` 的哈希审计）：
--   `event_hash = sha256(prev_hash | proposal_id | kind | payload 规范序 | created_at)`
-- 单行被改，链就断（`evolution.verify_chain` 逐条重算）。
--
-- ⚠️ **`proposal_id` 刻意不设外键。** 理由：`rejected_by_gate` 事件要能记在
--    **从未入库的提案**上（红线 8：放宽风控的提案"绝不入库"，但"谁试过"必须留痕；
--    白名单越界 / 证据不合规同理）。加了外键，这些拒绝事件就一条都写不进来 ——
--    审计反而瞎了。这是对 `01 §8.2`"proposal_id（FK）"的一处**有意偏离**，写在 R6 成果文档里。
CREATE TABLE IF NOT EXISTS fin_evolution_event (
  event_id     TEXT PRIMARY KEY,                              -- eve_<24hex>
  proposal_id  TEXT NOT NULL,                                 -- 见上方说明：无外键
  kind         TEXT NOT NULL CHECK (kind IN (
                 'created','validating','passed','rejected','applied',
                 'rolled_back','inconclusive','rejected_by_gate')),
  payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
  prev_hash    TEXT NOT NULL,                                 -- 上一条事件的 event_hash（首条为 ''）
  event_hash   TEXT NOT NULL,                                 -- 本条规范哈希
  actor        TEXT NOT NULL,                                 -- 'fin-worker' / 'user:<uuid>' / 'system'
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fin_evolution_event_by_proposal
  ON fin_evolution_event (proposal_id, created_at, event_id);


-- ── 不可变性：数据库触发器（`03 §7.3` 的修订）────────────────────────────
-- `fin_evolution_plan` 与 `fin_evolution_event` 各挂一个 `BEFORE UPDATE OR DELETE`
-- 触发器 → `RAISE EXCEPTION`。`fin_evolution_proposal` **不挂**（它的 status 是投影，
-- 必须能更新；一致性靠"改 status 必须同时追加事件"+ 投影校验保证）。
--
-- **能力边界（诚实结论，写在 R6 成果文档）**：
--   · 触发器**对表属主同样会触发**（PostgreSQL 触发器的语义），所以它**真的挡得住应用代码**
--     —— 本仓 api 以库属主 `hunter`（superuser）连接，零 GRANT 拦不住它，但触发器拦得住；
--   · 但**库属主 / superuser 永远能 `ALTER TABLE … DISABLE TRIGGER`** ——
--     所以**不能宣称**"对机器所有者绝对不可篡改"。绕过触发器改数据这件事，由事件表的
--     **哈希链**兜底发现（篡改后 `verify_chain` 报错）。
CREATE OR REPLACE FUNCTION fin_evolution_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
  RAISE EXCEPTION
    '% 是追加 / 不可变表：禁止 %（plan 写入即冻结、事件表是审计权威；改口径 = 新建提案）',
    TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'restrict_violation';
END;
$fn$;

DROP TRIGGER IF EXISTS fin_evolution_plan_immutable ON fin_evolution_plan;
CREATE TRIGGER fin_evolution_plan_immutable
  BEFORE UPDATE OR DELETE ON fin_evolution_plan
  FOR EACH ROW EXECUTE FUNCTION fin_evolution_immutable();

DROP TRIGGER IF EXISTS fin_evolution_event_immutable ON fin_evolution_event;
CREATE TRIGGER fin_evolution_event_immutable
  BEFORE UPDATE OR DELETE ON fin_evolution_event
  FOR EACH ROW EXECUTE FUNCTION fin_evolution_immutable();


-- ── 权限：照 `0041` —— **一条 GRANT 都不写** ──────────────────────────────
-- 与 `0041_memory_core.sql:117-134` 同口径：提案 / 计划 / 事件不是账本，
-- 运行期唯一具备 `fin_*` 权限的 `fin_paper_rw` 对这几张表**一行授权都不给**；
-- api 的读写能力来自**连接身份**（库属主 `hunter`），不是来自 GRANT。
-- "任何角色不给 DELETE"在这里由**触发器**兑现（比"不给授权"更硬 —— 连属主都拒）。
-- ════════════════════════════════════════════════════════════════════════
