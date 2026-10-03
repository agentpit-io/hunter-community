-- ════════════════════════════════════════════════════════════════════════
-- 0045_evolution_apply.sql · 智能炒股 · 受控自进化 · 生效与回滚（第四段 R8）
--   1. `fin_evolution_event.kind` 闭集**加入 'alert'**（观察期「普通性能不达标」的告警事件）；
--   2. 新表 `fin_evolution_reinject_task` —— 回滚失败经验的**回灌重试队列**。
--
-- 依据：`plan/R8.md` §一.3/§一.4 与出口标准 §10
--   「回滚时一并写入…一条有证据的失败经验…**经验写入失败不许吞**：失败时把任务留在
--     重试队列里，并在只读接口里给一个「**回灌待完成**」的状态位」。
--
-- 为什么加 `'alert'` 而不是复用既有 kind：
--   观察期「普通性能不达标」是**生效之后**发生的第三种终局语义 —— 它既不是 `failed`
--   （那是影子验证跑出来不达标、提案被驳回），也不是 `rejected_by_gate`（那是放行闸门拒绝）。
--   复用任一个都会让审计分不清「什么时候失败、失败在哪个阶段」。所以加一个 `alert`。
--   `STATUS_OF_KIND['alert'] = 'applied'`：告警**不改变**提案的投影状态（生效状态持续到
--   观察期结束或回滚），见 `services/fin/evolution.py`。
--
-- 为什么回灌要落库、不留在内存：
--   「回滚了但没学到」是**必须被看见**的失败（`plan/R8.md` §一.4）。若重试任务只活在
--   进程内存里，一次部署 / 重启就把它弄丢了 —— 与 `screen_quota` 那条「存内存的话每次部署
--   都白送」同一个教训。落库才能让只读接口如实报「回灌待完成」。
--
-- ⚠️ 纪律（`记忆系统追加规则.md` §四）：**只做加法**（`CREATE TABLE IF NOT EXISTS` /
--   `DROP CONSTRAINT IF EXISTS` + `ADD CONSTRAINT`）；**不带 `BEGIN;` / `COMMIT;`**
--   —— `app/migrate.py:apply_one` 已把每个文件包在独立事务里。可重复执行、不改历史行。
-- ════════════════════════════════════════════════════════════════════════

-- ── 1 · 事件闭集加入 'alert'（幂等：先 DROP IF EXISTS 再 ADD）────────────────
DO $kind$
BEGIN
  ALTER TABLE fin_evolution_event
    DROP CONSTRAINT IF EXISTS fin_evolution_event_kind_check;
  ALTER TABLE fin_evolution_event
    ADD CONSTRAINT fin_evolution_event_kind_check CHECK (kind IN (
      'created','validating','passed','rejected','failed','applied',
      'rolled_back','inconclusive','rejected_by_gate','alert'));
END
$kind$;

-- ── 2 · 回灌重试队列 ─────────────────────────────────────────────────────
--   一行 = 一次「回滚时要写的失败经验」的投递任务。回滚事务里先落一行 `pending`，
--   随后立即尝试 `memory.append_evidence`：成功 → `done`；失败 → 保留 `pending` +
--   记 `attempts` / `last_error`，由只读接口报「回灌待完成」，重试入口再试。
--
--   `payload` 存**追加那条经验所需的完整字段**（kind/statement/evidence/polarity…），
--   所以重试不需要回到「回滚那一刻」的上下文 —— 与 `R6` 的「计划写后冻结」同一思路：
--   任务落库那一刻，它要写什么就已经定了，重试不重新解释。
--
--   ⚠️ 这张表**不是账本、不是审计权威**：它只回答「那条失败经验到底写进去了没有」。
--      审计仍以 `fin_evolution_event`（`rolled_back` 事件）为准。所以它**可更新**
--      （status / attempts / last_error），与 `0043` 的两张不可变表不是一类。
CREATE TABLE IF NOT EXISTS fin_evolution_reinject_task (
  task_id     TEXT PRIMARY KEY,                 -- evri_<24hex>
  proposal_id TEXT NOT NULL,                    -- 归属提案（无外键，同 0043 事件表口径）
  project_id  TEXT NOT NULL REFERENCES fin_project(project_id),
  reason      TEXT NOT NULL,                    -- 为什么回滚（rollback_line / manual …）
  payload     JSONB NOT NULL,                   -- 追加失败经验所需的完整字段
  status      TEXT NOT NULL DEFAULT 'pending',
  attempts    INTEGER NOT NULL DEFAULT 0,
  last_error  TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (status IN ('pending','done','failed'))
);
CREATE INDEX IF NOT EXISTS fin_evolution_reinject_by_proposal
  ON fin_evolution_reinject_task (proposal_id, created_at DESC);
CREATE INDEX IF NOT EXISTS fin_evolution_reinject_pending
  ON fin_evolution_reinject_task (status, created_at) WHERE status = 'pending';

COMMENT ON TABLE fin_evolution_reinject_task IS
  '回滚经验回灌的重试队列（R8）。pending = 回灌待完成；只读接口据此报状态位。'
  '审计权威仍是 fin_evolution_event（rolled_back）；本表可更新（status/attempts/last_error）。';
