-- ════════════════════════════════════════════════════════════════════════
-- 0054_exec_allowance_backfill.sql · 智能炒股 · 补丁 L11 · 升级补齐已有项目放行
--
-- 背景（L11 人话目标）：产品定位是「AI 智能体自己选股、自己交易；人工只在想干预时
--   介入」。但 L06 落地时，执行允许名单 `fin_exec_allowance` **默认拒绝**（空表 = 谁都
--   不许），而且**只有一个人工接口**能写、**建项目不会顺带放行** —— 于是新客户装好、
--   建了项目，AI 一张单也发不出去，得自己去登记。这跟「AI 自己跑」是拧着的。
--
-- 补丁做了两件事（本迁移是第 2 件；第 1 件在应用层）：
--   1. `apps/api/app/services/fin/store.py::_insert_project()` 建项目时**同一事务**里
--      自动追加一条 `(scope='project', subject=<新 project_id>, status='active')` 登记
--      （`granted_by='auto:project-create'`）—— 从此**新项目开箱即可交易**。
--   2. **本迁移**：给**升级前就已经存在**的项目补登记 —— 它们在名单里没有现行 active 行，
--      升级后按老口径会被默认拒绝。补一条 `granted_by='auto:upgrade'` 的 active 行。
--
-- **不动判据**：放行只能靠表里真的有那条 active 行（`apps/paper/app/allowlist.py` 一字不改，
--   仍是「空表 = 谁都不许」）。**人工想收紧仍收得紧** —— 登记一条 `status='revoked'`
--   就能停掉某个项目（现成能力，只追加、留痕）。
--
-- 口径（对照 §B）：
--   · **只做加法**（纯 INSERT ... SELECT），不建表、不删、不改；
--   · **不带 `BEGIN;` / `COMMIT;`**（`app/migrate.py` 已把每个文件包在独立事务里）；
--   · **可重复执行**：判据是「该 `(scope='project', subject)` 的最新一行不是 active」——
--     第一次跑补齐所有项目，**第二遍 0 条要执行**。已在名单里现行 active 的项目
--     （如 L10 演示站逐条登记的 16 个）**跳过，不重复登记**。
--   · 表是**只追加**的（触发器挡 UPDATE / DELETE），所以这里是「再追加一条 active」，
--     不是改写任何既有行。
--
-- 权限：本迁移不新增对象、不改 GRANT。写入正文由两个角色之一完成 ——
--   `fin_paper_rw`（迁移 0051 已授 SELECT, INSERT）与库属主 `hunter`（api 用，属主天然可写）。
-- ════════════════════════════════════════════════════════════════════════


-- ── 给「没有现行 active 行」的已有项目补一条放行 ─────────────────────────
--   判据 = 该 (scope='project', subject=<project_id>) 里 allowance_id 最大的那行的 status
--   不是 'active'（表里一行都没有时也成立 —— COALESCE 成空串，同样 != 'active'）。
INSERT INTO fin_exec_allowance (scope, subject, status, granted_by, note)
SELECT 'project', p.project_id, 'active', 'auto:upgrade',
       'L11 升级补齐：历史项目自动放行（列表为空/已被撤销的补一条。要停：登记 revoked）'
  FROM fin_project p
 WHERE COALESCE((
         SELECT a.status
           FROM fin_exec_allowance a
          WHERE a.scope = 'project' AND a.subject = p.project_id
          ORDER BY a.allowance_id DESC
          LIMIT 1
       ), '') <> 'active';
