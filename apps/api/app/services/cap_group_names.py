"""能力分组的**用户自定义显示名** · per-user 一层薄映射。

## 为什么需要这一层

侧栏那些组名(综合分析 / 投研报告 / 自定义安装 …)原来是**算出来的**,
不是存下来的:`catalog.list_capabilities` 把每个 SKILL 的 `hunter.category`
和每个工具的 `category` 聚合起来,组名就是那个字符串本身。

所以"改组名"这件事原本没有落脚点 —— 三个来源没有一个改得动:

  · 工具的 category 是 `tool_catalog.py` 里的 Python 字面量 → 改名要改代码重新 build
  · 内置 SKILL 的写在 `skills/*/SKILL.md` → 改了下次 `git pull` 就被覆盖
  · 用户自装的 SKILL 有自己的 category,但 `list_capabilities` 一律
    把非内置项塞进「自定义安装」,它自己写的组名根本没被用上

这里的做法是**不动上面任何一处**,只加一层显示名映射:

  原组名(category)  →  永远是稳定的 key,排序、URL 的 ?group= 参数、
                       前端筛选全都继续用它
  display_name      →  只影响显示出来的那几个字

好处:内置文件一个字都不用改,升级不冲突;每个用户一套互不影响;
用户改完名之后收藏的链接照样能打开(URL 里是原 key)。

## 边界

**只管改名,不管移组。**"把这个能力挪到别的组"是另一件事,需要按条目
存映射,不在这次范围内 —— 没有需求就不先建表。
"""
from loguru import logger

from app.services.database import get_conn

# 用户自装的能力统一归到这一组(见 catalog.list_capabilities)。
# 放在这里是因为改名校验也要认它,不然用户改不了这一组的名字。
USER_GROUP = "自定义安装"

# 显示名长度上限。侧栏宽 240px,再长也是省略号,
# 但不设上限的话库里会躺进整段文字,detail 页和日志都会被撑变形。
MAX_LEN = 40

# 幂等 DDL · 与 db/migrations/0017_user_cap_group_name.sql 一致。
#
# 为什么这里要再写一遍:`db/migrations/` 是挂到 postgres 的
# `/docker-entrypoint-initdb.d` 的,**只在数据卷第一次初始化时执行**。
# 已经在跑的部署(比如 fin-r1 上那套已经跑了三周的库)加了新 .sql 也不会执行。
# 所以真正生效的是这段随代码走的幂等 DDL,.sql 文件是给全新安装和留档用的。
# 同 settings.py 的 _DDL 写法。
_DDL = """
CREATE TABLE IF NOT EXISTS user_cap_group_name (
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  category     TEXT NOT NULL,
  display_name TEXT NOT NULL,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, category)
);
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
    """取这个用户改过的全部组名。匿名或出错都返回空 dict。

    **出错不抛**:这个映射是锦上添花,取不到时整页应该正常显示默认组名,
    而不是因为一张附属表连不上就把能力库整页打成 500。
    """
    if not user_id:
        return {}
    try:
        _ensure_table()
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT category, display_name FROM user_cap_group_name WHERE user_id = %s",
                (user_id,),
            )
            return {r[0]: r[1] for r in cur.fetchall()}
        finally:
            conn.close()
    except Exception as e:
        logger.warning("[cap_group_names] get_all failed for {}: {} · 回落到默认组名", user_id, e)
        return {}


def set_name(user_id: str, category: str, display_name: str) -> None:
    """改名。`display_name` 传空串 = 删掉这条覆盖、恢复默认组名。"""
    _ensure_table()
    conn = get_conn()
    try:
        cur = conn.cursor()
        if display_name:
            cur.execute(
                "INSERT INTO user_cap_group_name (user_id, category, display_name) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (user_id, category) DO UPDATE "
                "SET display_name = EXCLUDED.display_name, updated_at = NOW()",
                (user_id, category, display_name),
            )
        else:
            cur.execute(
                "DELETE FROM user_cap_group_name WHERE user_id = %s AND category = %s",
                (user_id, category),
            )
        conn.commit()
    finally:
        conn.close()


def current_categories() -> set[str]:
    """当前这套部署会出现的**原始**组名全集 —— 给改名接口做存在性与重名校验。

    组名是算出来的,没有一张"组表"可以查,所以只能按
    `catalog.list_capabilities` 同样的口径再算一遍:
    内置项用自己的 category,非内置项一律进 USER_GROUP。

    两边口径一旦漂了,表现是"改一个明明看得见的组,接口说它不存在" ——
    所以 USER_GROUP 用的是本模块这一份常量,catalog 那边也 import 它,
    不各写各的字符串。
    """
    from app.services import skill_files, tool_catalog

    cats = {s["category"] for s in skill_files.load_all() if s.get("builtin", True)}
    cats |= {t.category or "接入与自查" for t in tool_catalog.pickable()
             if t.origin is not tool_catalog.ToolOrigin.USER}
    cats.add(USER_GROUP)
    return {c for c in cats if c}
