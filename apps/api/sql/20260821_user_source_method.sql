-- 用户数据源:补 http_method 列 · `_24` P0.5
-- 2026-08-21
--
-- 为什么需要:`source_templates` 早就给每条接口声明了 `method`
-- (巨潮公告是 POST),但建表时没有对应的列,`/user_sources/bulk`
-- 也就无处可存 —— 取数层只能一律按 GET 打。
--
-- 表现:巨潮那条 **HTTP 500**。而 500 看起来像"上游挂了",
-- 实际是我们用错了动词(实测 GET→500 / POST→200)。
--
-- 默认 'GET' —— 已有的行全是 GET 语义,加上默认值不改变任何现有行为。
--
-- 迁移规则:只 ADD,不 DROP/RENAME/ALTER TYPE。
-- 同 DDL 在 app/services/database.py:init_db() 里有幂等副本,两份必须一致。

BEGIN;

ALTER TABLE user_data_sources
  ADD COLUMN IF NOT EXISTS http_method VARCHAR(8) NOT NULL DEFAULT 'GET';

COMMIT;
