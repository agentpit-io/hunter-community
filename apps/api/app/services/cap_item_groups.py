"""能力**归属哪个分组**的用户自定义 · per-user 一层薄映射。

和 `cap_group_names.py` 是一对:那个管"组叫什么名字",这个管"某个能力归哪个组"。
两个加起来才是完整的"我自己的分类方式"。

## 为什么需要

`catalog.list_capabilities` 的分组是写死的两条规则:

    内置项  → 用它自己的 category(来自 SKILL.md 的 hunter.category
              或 tool_catalog.py 里的字面量)
    自装项  → **一律**塞进「自定义安装」,它自己 SKILL.md 里写的 category 被无视

第二条是用户的痛点:从 GitHub 装了三个仓十几个 SKILL,全堆在一个组里,
按用途分类的能力完全用不上。而这三个来源没有一个能由用户改
(改 SKILL.md 会被 git pull 覆盖,tool_catalog.py 是代码)。

## 做法

存一张 `user_id + item_key → category` 的覆盖表,分组时**先查它**,
查不到才回落到上面那两条默认规则。

- 内置项和自装项**都能移** —— 机制完全一样,只让自装项能移是没道理的限制
- 目标组可以是现有的组,也可以是一个全新的名字(打出来就有了这个组)
- `category` 传空串 = 撤销覆盖,回到默认分组

## 移出「自定义安装」会不会丢失"哪些是我加的"

不会。每一项本来就带 `builtin` 字段,卡片上有「你加的」标记,
detail 面板也显示来源仓库(`origin`)。分组只是分组,不承担身份标识。
"""
from loguru import logger

from app.services.database import get_conn

# 目标组名长度上限 · 与 cap_group_names.MAX_LEN 对齐(同一处侧栏,同样的宽度约束)
MAX_LEN = 40

# 幂等 DDL · 与 db/migrations/0018_user_cap_item_group.sql 一致。
#
# 为什么这里要再写一遍:`db/migrations/` 挂的是 postgres 的
# `/docker-entrypoint-initdb.d`,**只在数据卷第一次初始化时执行**。
# 已经在跑的部署加了新 .sql 也不会执行。同 cap_group_names.py。
#
# item_key 不做外键:能力不是数据库里的行,它是从 SKILL.md 与
# tool_catalog.py 现算出来的。用户卸掉一个 SKILL 后这里会留一条孤儿记录,
# 分组时查不到对应项自然就不生效 —— 无害,而且他重新装回来时分类还在。
_DDL = """
CREATE TABLE IF NOT EXISTS user_cap_item_group (
  user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  item_key   TEXT NOT NULL,
  category   TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, item_key)
);
CREATE INDEX IF NOT EXISTS idx_user_cap_item_group_cat
  ON user_cap_item_group(user_id, category);
"""

_ddl_applied = False


def _ensure_table() -> None:
    global _ddl_applied
    if _ddl_applied:
        return
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_DDL)
        conn.commit()
        _ddl_applied = True
    finally:
        conn.close()


def get_all(user_id: str | None) -> dict[str, str]:
    """取这个用户的全部「条目 → 分组」覆盖。匿名或出错都返回空 dict。

    **出错不抛**:和 cap_group_names.get_all 一个道理 —— 这是个人化的
    锦上添花,取不到时该按默认分组正常显示整页,而不是把能力库打成 500。
    """
    if not user_id:
        return {}
    try:
        _ensure_table()
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT item_key, category FROM user_cap_item_group WHERE user_id = %s",
                (user_id,),
            )
            return {r[0]: r[1] for r in cur.fetchall()}
        finally:
            conn.close()
    except Exception as e:
        logger.warning("[cap_item_groups] get_all failed for {}: {} · 回落到默认分组", user_id, e)
        return {}


def set_group(user_id: str, item_key: str, category: str) -> None:
    """把一个能力移到 `category` 组。`category` 传空串 = 撤销覆盖、回到默认分组。"""
    _ensure_table()
    conn = get_conn()
    try:
        cur = conn.cursor()
        if category:
            cur.execute(
                "INSERT INTO user_cap_item_group (user_id, item_key, category) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (user_id, item_key) DO UPDATE "
                "SET category = EXCLUDED.category, updated_at = NOW()",
                (user_id, item_key, category),
            )
        else:
            cur.execute(
                "DELETE FROM user_cap_item_group WHERE user_id = %s AND item_key = %s",
                (user_id, item_key),
            )
        conn.commit()
    finally:
        conn.close()


def known_item_keys() -> set[str]:
    """当前这套部署认得的能力 key 全集 —— 给迁移接口做存在性校验。

    没有这层校验的话,前端页面开着没刷新、期间某个 SKILL 被卸掉,
    用户点"移动"会成功返回但什么都不会发生(存了一条孤儿记录)。
    明说"这个能力不在了,刷新一下"比假装成功强。
    """
    from app.services import skill_files, tool_catalog

    return ({s["key"] for s in skill_files.load_all()}
            | {t.key for t in tool_catalog.pickable()})


def user_categories(user_id: str | None) -> set[str]:
    """这个用户**自己造出来的**分组名。

    用户把一个能力移进一个全新的名字,那个组就存在了 —— 但它不在任何
    内置来源里。改名接口做存在性校验时必须认它,否则会出现
    "自己建的组反而改不了名"。
    """
    return {c for c in get_all(user_id).values() if c}
