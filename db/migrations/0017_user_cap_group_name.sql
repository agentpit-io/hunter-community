-- 能力分组的用户自定义显示名 · 2026-09-07
--
-- 侧栏那些组名(综合分析 / 投研报告 / 自定义安装 …)原来是**算出来的**:
-- catalog.list_capabilities 把每个 SKILL 的 hunter.category 和每个工具的
-- category 聚合起来,组名就是那个字符串本身。所以"改组名"没有落脚点 ——
-- 工具的 category 是 tool_catalog.py 里的 Python 字面量(改名要改代码),
-- 内置 SKILL 的写在 skills/*/SKILL.md 里(改了下次 git pull 就被覆盖)。
--
-- 这张表是加在上面的一层显示名映射:原组名 category 仍然是稳定的 key
-- (排序、URL 的 ?group= 参数、前端筛选都还用它),只有显示出来的字换掉。
-- 内置文件一个字都不用改,升级不冲突,每个用户一套互不影响。
--
-- ⚠️ 注意:db/migrations/ 是挂到 postgres 的 /docker-entrypoint-initdb.d 的,
-- **只在数据卷第一次初始化时执行**。已经在跑的部署加了这个文件也不会执行。
-- 真正生效的是 apps/api/app/services/cap_group_names.py 里那段随代码走的
-- 幂等 DDL。这个文件是给全新安装和留档用的,两处必须保持一致。

CREATE TABLE IF NOT EXISTS user_cap_group_name (
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  category     TEXT NOT NULL,
  display_name TEXT NOT NULL,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, category)
);

COMMENT ON TABLE user_cap_group_name IS
  '能力库侧栏分组的用户自定义显示名 · category 是原始组名(稳定 key)· display_name 只影响显示';
COMMENT ON COLUMN user_cap_group_name.category IS
  '后端算出来的原始组名 · 排序与 ?group= 参数仍用它 · 不随改名变化';
