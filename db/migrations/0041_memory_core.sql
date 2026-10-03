-- ════════════════════════════════════════════════════════════════════════
-- 0041_memory_core.sql · 智能炒股 · 统一经验系统核心三表
--   （经验条目 / 证据行 / 记忆快照）
--
-- 依据：`plan/记忆系统_需求分析与实现方案.md` §二（数据模型）· §三（唯一入口）；
--       `plan/记忆系统追加规则.md` §四（数据模型口径）。
-- 执行：由 `app.migrate` 在 api 启动时按文件名顺序执行（`boot.sh` → `python -m app.migrate`）。
--       不挂 `docker-entrypoint-initdb.d`，不手工跑。
--
-- 落库位置 = 应用库（`hunter`），与 `0023_fin_core.sql:9-17` 的既有决定一致：
--   `fin_*` 表建在应用库、由 api 启动时的 `app.migrate` 按 `DATABASE_URL` 执行。
--   **不新建数据库、不新建角色**（方案 §2.4 决策点 D1）—— 记忆服务跑在 api 进程内，
--   用的是 `app.migrate` / api 服务同一把连接，不为它单起一个进程、也不单建一个角色
--   （「不许新增平行权威」的同精神）。
--
-- 幂等：一律 `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`。
--   **只加表、只加索引**；不改历史行、不做回填（经验表从零开始，不猜历史）。
--   重复执行不报错（验收项之一）。
-- 类型口径沿用 `0023`：比例类字段用 NUMERIC(9,6)；时间一律 TIMESTAMPTZ；
--   全表**不许出现 float / double / REAL**。主键一律 TEXT（业务身份推导，非随机数）。
--
-- ⚠️ 本文件**不带** `BEGIN;` / `COMMIT;` —— `app/migrate.py:apply_one` 已经把每个迁移文件
--    包在**独立事务**里执行（失败整体回滚 + 记账），文件内再写事务边界会破坏那个承诺。
--    `db/migrations/` 下现存文件无一自带事务边界，保持一致（见 `0039` 文件头那条）。
--
-- ⚠️ **命名撞车已避开**：本仓 `0023_fin_core.sql:180` 已有一张 `fin_snapshot`
--    （主键 `snapshot_id`，值是 `SNAP-...`，是**行情 / 估值快照**）。
--    所以本文件的记忆快照表叫 `fin_memory_snapshot`、主键叫 `memory_snapshot_id`、
--    前缀用 **`msnap_`** —— 不复用 `snap_` / `SNAP-`。建完表实测确认无同名冲突（见验收）。
-- ════════════════════════════════════════════════════════════════════════


-- ── 表一：经验条目（唯一真值）────────────────────────────────────────────
-- 「事实 / 假设 / 验证结论」三分在**存储层**就能区分，而不是靠一句提示词。
-- `kind` 与 `status` **刻意分两列**：`09 :492` 与原型页的表头就是「类型 / 状态」两列，
-- 且 `kind='verified'` 的条目**也可能是「已推翻」** —— 合成一个枚举就表达不了。
-- 追加不删：推翻 = 追加一条 `status='已推翻'` + 把原条目 `superseded_by` 指向它（**无 DELETE**）。
CREATE TABLE IF NOT EXISTS fin_experience (
  experience_id   TEXT PRIMARY KEY,                 -- exp_...
  project_id      TEXT NOT NULL REFERENCES fin_project(project_id),
  market          TEXT CHECK (market IN ('CN_A','HK','US')),   -- 跨市场结论为 NULL
  kind            TEXT NOT NULL CHECK (kind IN ('fact','hypothesis','verified')),
  status          TEXT NOT NULL CHECK (status IN ('待验证','已确认','已推翻')),
  source          TEXT NOT NULL CHECK (source IN ('ai','human_mixed')),
  statement       TEXT NOT NULL,                    -- 一句话结论（人可读，不含裸数字）
  -- 边界（方案 §8「适用边界 / 失效重验条件」）
  applicability   TEXT,                             -- 适用：市场 / 板块 / 时点 / 标的形态
  invalidation_condition TEXT,                      -- 失效 / 重验条件（到点了必须重验）
  -- 验证三件套（仅 kind='verified' 必填；服务端硬校验，见 §三 3.2）
  method          TEXT,                             -- 怎么验的（口径，不是结论）
  sample_size     INTEGER,                          -- 样本笔数
  uncertainty     NUMERIC(9,6),                     -- 不确定度（口径待定，见 Q3，本轮允许 NULL）
  confidence      NUMERIC(9,6),                     -- 置信度（沿用 09 :492）
  -- 时间边界（回放的闸门）：`as_of` = 「这是几时的认知」
  as_of           TIMESTAMPTZ NOT NULL,
  valid_from      TIMESTAMPTZ,
  valid_until     TIMESTAMPTZ,                      -- NULL = 无期限；过期 → 不进决策结果（§三 3.3）
  -- 防评估泄露
  exposure_scope  TEXT NOT NULL DEFAULT 'searchable'
                  CHECK (exposure_scope IN ('searchable','holdout_only')),
  holdout_tainted BOOLEAN NOT NULL DEFAULT FALSE,   -- 由保底测试集数据得出
  -- 审计
  memory_snapshot_id TEXT,                          -- 形成于哪一版快照（写入时冻结）
  created_by      TEXT NOT NULL,                    -- 'ai' 或 'user:<uuid>'
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  superseded_by   TEXT REFERENCES fin_experience(experience_id),  -- 被推翻 → 加行 + 指回去，**不删**
  -- 硬校验（服务端 §三 3.2 规则的**第二道保险**，第一道在服务层）
  CHECK (kind <> 'verified' OR (method IS NOT NULL AND sample_size IS NOT NULL)),
  CHECK (kind <> 'hypothesis' OR status = '待验证')
);
-- 读路径的主索引：按「项目 × 市场 × 类型 × 状态」过滤、按 as_of 倒序（回放的闸门）。
CREATE INDEX IF NOT EXISTS fin_experience_lookup
  ON fin_experience (project_id, market, kind, status, as_of DESC);


-- ── 表二：证据行（每条经验至少一行）──────────────────────────────────────
-- 「经验必须带证据」的落地。引用目标：
--   · trade     → fin_trade.trade_id（0023:230，`trd_...`）
--   · report    → fin_report.report_id（0023:343，`rpt_...`）
--   · fact      → fin_report_fact 的 `rpt_...:metric_key`
--   · snapshot  → fin_snapshot.snapshot_id（0023:181，行情快照，`SNAP-...`）
--   · external  → 外部文档 id
-- **服务端写入时逐条验在不在**（§三 3.2 规则 3）：引用不存在的 id 直接 400，防编造引用。
-- 纯追加：无 UPDATE、无 DELETE。
CREATE TABLE IF NOT EXISTS fin_experience_evidence (
  evidence_id     TEXT PRIMARY KEY,                 -- evd_...
  experience_id   TEXT NOT NULL REFERENCES fin_experience(experience_id),
  evidence_kind   TEXT NOT NULL CHECK (evidence_kind IN ('trade','report','fact','snapshot','external')),
  ref_id          TEXT NOT NULL,                    -- trd_... / rpt_... / rpt_...:metric_key / SNAP-... / 外部文档 id
  ref_note        TEXT,
  holdout_tainted BOOLEAN NOT NULL DEFAULT FALSE,   -- 传染源（§三 3.2 规则 4：任一为真 ⇒ 经验强制 holdout_only）
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (experience_id, evidence_kind, ref_id)     -- 同一证据不许对同一条经验重复挂
);
CREATE INDEX IF NOT EXISTS fin_experience_evidence_by_exp
  ON fin_experience_evidence (experience_id);


-- ── 表三：记忆快照（`memory_snapshot_id` 的真值）─────────────────────────
-- 每份研究 / 每次决策读到的经验集合**冻结成快照**，历史回放只认快照：
-- **回放按 `experience_ids` 取，不重新查询** —— 后来才写的经验进不了这份集合，
-- 这就是「历史回放不得读后来才形成的经验」（防未来函数）的技术兑现。
-- `purpose='holdout'` 的快照是保底测试集的运行记录，**永不参与候选搜索**（§三 3.3 规则 1）。
CREATE TABLE IF NOT EXISTS fin_memory_snapshot (
  memory_snapshot_id TEXT PRIMARY KEY,              -- msnap_...（**不复用** SNAP-）
  project_id         TEXT NOT NULL REFERENCES fin_project(project_id),
  market             TEXT,
  trade_date         DATE,
  point              TEXT,
  purpose            TEXT NOT NULL CHECK (purpose IN ('decision','review','holdout')),
  experience_ids     TEXT[] NOT NULL DEFAULT '{}',  -- 冻结的就是这一批 id
  query_filter       JSONB,                         -- 当时的查询条件（可复现那次查询）
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ── 权限：**刻意一条 GRANT 都不写** ───────────────────────────────────────
-- `0023_fin_core.sql:426-440` 把「谁能写账本」收口成逐表 GRANT，运行期唯一具备
-- `fin_*` 权限的角色是 `fin_paper_rw`。本文件**不照抄那句 GRANT**，三条理由：
--
--   1. **经验不是账本。** 这三张表刻意对 `fin_paper_rw` **一行授权都不给**——
--      「唯一入口」最硬的收口就是账本角色连 SELECT 都没有；不给就是默认拒绝。
--      写进了本轮验收项（§七 第 3 条：以 `fin_paper_rw` 身份 SELECT → permission denied）。
--   2. **不新建角色**（方案 §2.4 决策点 D1）。没有「经验服务角色」这个对象，
--      也就没有第二条 GRANT 的目标。记忆服务（R2 起）跑在 api 进程内，
--      与 `app.migrate` 共用同一把连接。
--   3. **本部署下授权语义由连接身份决定。** api 连的是库属主 `hunter`
--      （`pg_roles.rolsuper = true`，实测见成果文档），对这三张表本就有全权——
--      它的读写能力来自**连接身份**，不是来自 GRANT。为凑一句 GRANT 去建新角色
--      只会多一处可出错的地方（「不许新增平行权威」的同精神）。
--
-- 「任何角色不给 DELETE」（0023 末行）在这三张表上是**天然成立**的：一行权限都没有。
-- 需要独立角色时，另行 provision + 让 api 第二条连接池用它，是另一个决定。
-- ════════════════════════════════════════════════════════════════════════
