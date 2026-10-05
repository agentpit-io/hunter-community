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
--   2. **本迁移**：给**升级前就已经存在、且从未在名单里登记过**的项目补登记 ——
--      它们按老口径会被默认拒绝。补一条 `granted_by='auto:upgrade'` 的 active 行。
--      **已登记过的项目（无论现行状态是 active 还是人工 revoked）一行都不动** ——
--      尊重已经发生的人工决定，绝不把人工的撤销悄悄救回来。
--
-- **不动判据**：放行只能靠表里真的有那条 active 行（`apps/paper/app/allowlist.py` 一字不改，
--   仍是「空表 = 谁都不许」）。**人工想收紧仍收得紧** —— 登记一条 `status='revoked'`
--   就能停掉某个项目（现成能力，只追加、留痕）。
--
-- 口径（对照 §B）：
--   · **只做加法**（纯 INSERT ... SELECT），不建表、不删、不改；
--   · **不带 `BEGIN;` / `COMMIT;`**（`app/migrate.py` 已把每个文件包在独立事务里）；
--   · **可重复执行**：判据是「该 `(scope='project', subject)` **从来没有过任何一行**」——
--     第一次跑只补齐「从没登记过」的项目，**第二遍 0 条要执行**。已经登记过的项目
--     （无论现行状态是 active 还是人工 revoked）**一行都不动，不重复登记**。
--     ⚠ 判据是「从来没登记过」，**不是**「最新一行不是 active」。后者会把**人工撤销**
--     （登记过 `revoked`）的项目再补一条 active 悄悄救回来 —— 与「人工想收紧就收紧」的
--     产品口径正相反。真实场景：客户在 `2.0.0` 上撤销了某项目 → 升级到含本迁移的版本 →
--     无脑按「最新一行不是 active」补，撤销就被悄悄恢复。**`0054` 未随任何 tag 发布，
--     故就地修正，不另加 `0055`** —— 这张表只追加（触发器挡 UPDATE/DELETE），
--     追加式的新迁移救不回已经插进去的行，唯一正确的修法就是改本文件。
--   · 表是**只追加**的（触发器挡 UPDATE / DELETE），所以这里是「再追加一条 active」，
--     不是改写任何既有行。
--
-- 权限：本迁移不新增对象、不改 GRANT。写入正文由两个角色之一完成 ——
--   `fin_paper_rw`（迁移 0051 已授 SELECT, INSERT）与库属主 `hunter`（api 用，属主天然可写）。
-- ════════════════════════════════════════════════════════════════════════


-- ── 给「从未在名单里登记过」的已有项目补一条放行 ─────────────────────────
--   判据 = 该 (scope='project', subject=<project_id>) **一行都没有**（NOT EXISTS）。
--   只要登记过（现行 active，或已被人工 revoked），就一行都不动。
INSERT INTO fin_exec_allowance (scope, subject, status, granted_by, note)
SELECT 'project', p.project_id, 'active', 'auto:upgrade',
       'L11 升级补齐：从未登记过的历史项目自动放行（要停：登记 revoked）'
  FROM fin_project p
 WHERE NOT EXISTS (
         SELECT 1
           FROM fin_exec_allowance a
          WHERE a.scope = 'project' AND a.subject = p.project_id
       );
