-- 能力归属分组的用户自定义 · 2026-09-07
--
-- 与 0017_user_cap_group_name.sql 是一对:那张管"组叫什么名字",
-- 这张管"某个能力归哪个组"。两个加起来才是完整的"我自己的分类方式"。
--
-- 起因:catalog.list_capabilities 的分组是写死的两条规则 ——
--   内置项 → 用它自己的 category(来自 SKILL.md 或 tool_catalog.py)
--   自装项 → **一律**塞进「自定义安装」,它自己写的 category 被无视
-- 第二条是痛点:从 GitHub 装了三个仓十几个 SKILL 全堆在一个组里,
-- 而这三个来源没有一个能由用户改(改 SKILL.md 会被 git pull 覆盖,
-- tool_catalog.py 是代码)。
--
-- 这张表存一层覆盖,分组时先查它,查不到才回落到默认规则。
-- 目标组可以是全新的名字 —— 打出来这个组就有了。
--
-- item_key 不做外键:能力不是数据库里的行,是从 SKILL.md 与
-- tool_catalog.py 现算出来的。用户卸掉一个 SKILL 后这里会留一条孤儿记录,
-- 分组时查不到对应项自然不生效 —— 无害,而且重新装回来时分类还在。
--
-- ⚠️ 注意:db/migrations/ 挂的是 postgres 的 /docker-entrypoint-initdb.d,
-- **只在数据卷第一次初始化时执行**。已经在跑的部署加了这个文件不会执行。
-- 真正生效的是 apps/api/app/services/cap_item_groups.py 里那段随代码走的
-- 幂等 DDL。这个文件是给全新安装和留档用的,两处必须保持一致。

CREATE TABLE IF NOT EXISTS user_cap_item_group (
  user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  item_key   TEXT NOT NULL,
  category   TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, item_key)
);

CREATE INDEX IF NOT EXISTS idx_user_cap_item_group_cat
  ON user_cap_item_group(user_id, category);

COMMENT ON TABLE user_cap_item_group IS
  '能力库里「某个能力归哪个组」的用户覆盖 · 查不到则回落到默认分组规则';
COMMENT ON COLUMN user_cap_item_group.item_key IS
  '能力 key(SKILL 或工具)· 故意不做外键:能力不是库里的行,是现算出来的';
COMMENT ON COLUMN user_cap_item_group.category IS
  '目标组名 · 可以是一个全新的名字,打出来这个组就存在了';
