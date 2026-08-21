-- 用户数据源:补 body_tpl 列 · `_24` P0.5
-- 2026-08-21
--
-- 为什么需要:**Tushare 是单地址 RPC**。它的日线/财报/估值/资金流四个
-- 接口共用 `https://api.tushare.pro` 一个地址,靠 POST body 里的
-- `api_name` 区分。
--
-- 只有 (market, kind, endpoint) 三元组表达不了这件事:四条记录的 endpoint
-- 完全一样,取数层发出去的请求也一样 —— 实测返回
-- `code=40101 请指定正确的接口名`。
--
-- body_tpl 存这份模板,取数时与 key 合并、并展开 {ts_code} 之类的占位符。
-- 默认 '{}' —— 已有的行都不需要 body,加上默认值不改变任何现有行为。
--
-- 迁移规则:只 ADD,不 DROP/RENAME/ALTER TYPE。
-- 同 DDL 在 app/services/database.py:init_db() 里有幂等副本。

BEGIN;

ALTER TABLE user_data_sources
  ADD COLUMN IF NOT EXISTS body_tpl JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMIT;
