-- ════════════════════════════════════════════════════════════════════════
-- 0024_fin_paper_sequence.sql · 智能炒股 · 补运行期角色的**序列**权限
--
-- 背景（M2 实测发现，属 0023 的遗漏）：
--   `0023_fin_core.sql` 给 `fin_paper_rw` 逐表授了 SELECT/INSERT/UPDATE，
--   但漏了这些表上 `BIGSERIAL` 主键背后的**序列**。现象是 paper 服务（M2）
--   一写入 `fin_cash_ledger` 就报：
--     permission denied for sequence fin_cash_ledger_entry_id_seq
--   —— 表权限给了、序列没给，**报错只在第一次真正 INSERT 时才出现**，建库当天的
--   验收查的是 `role_table_grants`，看不到这一层。自检（`apps/paper/app/selfcheck.py`）
--   现在把序列 USAGE 也纳入校验，以后这类遗漏会在启动时挡下。
--
-- 受影响：`fin_cash_ledger.entry_id` · `fin_recon_log.id` ·
--         `fin_param_change_log.id` · `fin_human_action_log.id`（四处 BIGSERIAL）。
--
-- 幂等：GRANT 天然幂等；用 DO 块按「fin_ 表拥有的序列」动态遍历，新加表自动覆盖。
-- 已存在的部署：本文件由 `app.migrate` 在 api 启动时执行（`db/migrations` 不是
--   initdb 挂载 —— 那目录只在数据卷第一次初始化时跑）。
-- ════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
  r record;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fin_paper_rw') THEN
    -- 角色不在（全新库上 0023 先跑，正常不会走到这里）。建一个无密码 LOGIN。
    CREATE ROLE fin_paper_rw LOGIN;
  END IF;

  FOR r IN
    SELECT c.relname AS seq
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_depend d ON d.objid = c.oid AND d.deptype IN ('a', 'i')
      JOIN pg_class t ON t.oid = d.refobjid
     WHERE c.relkind = 'S'
       AND n.nspname = 'public'
       AND t.relname LIKE 'fin\_%'
  LOOP
    EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %I TO fin_paper_rw', r.seq);
  END LOOP;
END $$;

-- 以后新加的 fin_* 表，其序列自动授给运行期角色（避免同一个坑再来一次）。
-- 只对迁移执行者（建表者）名下、public schema 里新建的序列生效。
DO $$
BEGIN
  EXECUTE format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
    'GRANT USAGE, SELECT ON SEQUENCES TO fin_paper_rw',
    current_user
  );
END $$;
