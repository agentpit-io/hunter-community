-- 用户数据源:补 alt_urls 列 · `_24` P0.5
-- 2026-08-21
--
-- 为什么需要:**东财的 push2his 分片会轮换**。实测同一个地址同一分钟内
-- 82. 是 5/5,十分钟后 0/4,而同时 7. 变 4/4;更糟的时候所有分片一起
-- 不通,只剩 push2delay(它只给当日一行,但 100% 可达)。
--
-- 写死任何一个分片都会时灵时不灵,而用户会归咎于我们 —— 地址是我们
-- 预填的。alt_urls 存备用地址,主地址连不上时依次试。
--
-- 默认 '[]' —— 已有的行都没有备用地址,加上默认值不改变任何现有行为。
--
-- 迁移规则:只 ADD,不 DROP/RENAME/ALTER TYPE。
-- 同 DDL 在 app/services/database.py:init_db() 里有幂等副本。

BEGIN;

ALTER TABLE user_data_sources
  ADD COLUMN IF NOT EXISTS alt_urls JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMIT;
