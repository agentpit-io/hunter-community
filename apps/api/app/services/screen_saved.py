"""用户保存的扫描策略 · per-user 一张表。

## 一份「扫描策略」存什么

脚本 + 市场 + 排序。**脚本必须带着开关的停用状态**,这点很容易写错:

  前端草稿用的是 `buildScript(true)` —— `allEnabled=true` 会把**所有**条件都写进
  `plot`,等于把用户关掉的开关又打开了。草稿这样做无所谓(刷新后全开是可以接受的),
  但保存策略这样做就是 bug:用户关掉三条再保存,加载回来三条全亮着。

  所以保存用 `buildScript(false)`:停用的条件保留 `def`、只是不进 `plot`。
  加载时走 `/screener/parse` 重新解析,`decompose` 按 `plot_refs` 恢复开关
  (见前端 applyParsed 的注释:「谁在 plot_refs 里谁就是启用的」),往返一致。

## 同名 = 覆盖

`(user_id, name)` 唯一。存一个已有的名字就是更新它 —— 这是「改完再存一次」
最自然的路径,不需要单独的「更新」按钮。前端覆盖前会先让用户确认。

## 为什么存后端不存 localStorage

扫描策略是用户攒下来的资产,换台电脑就没了会很伤。仓里同类的东西
(我的策略、能力库自定义分组)都是后端 per-user 存的,这里保持一致。
"""
import re

from loguru import logger

from app.services.database import get_conn

# 名字要显示在预置按钮那一排里,太长会把整行撑变形
MAX_NAME = 30
# 真实脚本 1~2KB;不设上限这张表会被当成免费存储
MAX_SCRIPT = 20000
# 预置行放不下更多了。超了让用户先删,而不是悄悄挤掉最老的 —— 那是替用户删数据
MAX_PER_USER = 50

# 排序字段只存不执行,但会原样回传给前端,所以还是收一下形状
_SORT_RE = re.compile(r"^[A-Za-z0-9_.|]{1,64}$")

# 幂等 DDL · 与 db/migrations/0019_user_screen_preset.sql 一致。
#
# 为什么要在这里再写一遍:`db/migrations/` 挂在 postgres 的
# `/docker-entrypoint-initdb.d`,**只在数据卷第一次初始化时执行**。
# 已经在跑的部署加了新 .sql 也不会执行,而且**不报错** —— 表没建,
# 第一个用到它的请求 500,你还以为迁移跑过了。见仓内 CLAUDE.md 那条铁律。
# 同 cap_group_names.py / settings.py 的 _DDL 写法。
_DDL = """
CREATE TABLE IF NOT EXISTS user_screen_preset (
  id         BIGSERIAL PRIMARY KEY,
  user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  market     TEXT NOT NULL,
  script     TEXT NOT NULL,
  sort_by    TEXT,
  sort_desc  BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (user_id, name)
);
CREATE INDEX IF NOT EXISTS idx_user_screen_preset_user
  ON user_screen_preset (user_id, updated_at DESC);
"""

_ddl_applied = False


class SavedError(ValueError):
    """用户能看懂的校验错误 —— 路由转成 400,message 原样给前端。"""


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


def _row(r) -> dict:
    return {
        "id": r[0], "name": r[1], "market": r[2], "script": r[3],
        "sort_by": r[4], "sort_desc": bool(r[5]),
        "updated_at": r[6].isoformat() if r[6] else None,
    }


def list_for(user_id: str) -> list[dict]:
    """这个用户保存的全部扫描策略,最近改过的在前。"""
    _ensure_table()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, market, script, sort_by, sort_desc, updated_at "
            "FROM user_screen_preset WHERE user_id = %s ORDER BY updated_at DESC",
            (user_id,),
        )
        return [_row(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _clean(name: str, market: str, script: str, sort_by: str | None,
           markets: set[str]) -> tuple[str, str, str, str | None]:
    name = (name or "").strip()
    if not name:
        raise SavedError("名称不能为空")
    if len(name) > MAX_NAME:
        raise SavedError(f"名称最多 {MAX_NAME} 个字(现在 {len(name)} 个)")
    market = (market or "").strip().lower()
    if market not in markets:
        raise SavedError(f"不认识的市场:{market or '(空)'}")
    script = (script or "").strip()
    if not script:
        raise SavedError("当前没有条件,没什么可保存的")
    if len(script) > MAX_SCRIPT:
        raise SavedError(f"脚本太长({len(script)} 字符,上限 {MAX_SCRIPT})")
    # 前端保存的一定是解析过的条件拼回来的脚本,必然带 plot。
    # 这里只挡「明显不是扫描脚本」的东西,不调 parse_script ——
    # 那要联网拉字段表,会让「保存」这个本该瞬间完成的动作变慢甚至失败。
    if not re.search(r"(^|\n)\s*plot\s+\w+\s*=", script):
        raise SavedError("脚本里没有 plot 语句,不像一份完整的扫描条件")
    if sort_by is not None:
        sort_by = sort_by.strip() or None
        if sort_by and not _SORT_RE.match(sort_by):
            sort_by = None           # 排序字段不合法就不存,不值得为它拒绝整次保存
    return name, market, script, sort_by


def save(user_id: str, name: str, market: str, script: str,
         sort_by: str | None, sort_desc: bool, markets: set[str]) -> dict:
    """保存。同名覆盖。返回 {item, created}。"""
    name, market, script, sort_by = _clean(name, market, script, sort_by, markets)
    _ensure_table()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM user_screen_preset WHERE user_id = %s AND name = %s",
            (user_id, name),
        )
        exists = cur.fetchone() is not None
        if not exists:
            cur.execute("SELECT COUNT(*) FROM user_screen_preset WHERE user_id = %s", (user_id,))
            if cur.fetchone()[0] >= MAX_PER_USER:
                raise SavedError(
                    f"最多保存 {MAX_PER_USER} 个扫描策略,先删掉一些不用的再存。"
                    f"(同名保存会覆盖原来那个,不占新名额)")
        cur.execute(
            "INSERT INTO user_screen_preset (user_id, name, market, script, sort_by, sort_desc) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (user_id, name) DO UPDATE SET "
            "  market = EXCLUDED.market, script = EXCLUDED.script, "
            "  sort_by = EXCLUDED.sort_by, sort_desc = EXCLUDED.sort_desc, updated_at = NOW() "
            "RETURNING id, name, market, script, sort_by, sort_desc, updated_at",
            (user_id, name, market, script, sort_by, bool(sort_desc)),
        )
        item = _row(cur.fetchone())
        conn.commit()
        logger.info("[screen_saved] {} {} 「{}」({} · {} 字符)",
                    user_id, "覆盖" if exists else "新建", name, market, len(script))
        return {"item": item, "created": not exists}
    finally:
        conn.close()


def delete(user_id: str, preset_id: int) -> bool:
    """删一个。**只删自己的** —— WHERE 里带 user_id,别人的 id 猜中了也删不掉。"""
    _ensure_table()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM user_screen_preset WHERE id = %s AND user_id = %s",
            (preset_id, user_id),
        )
        n = cur.rowcount
        conn.commit()
        return n > 0
    finally:
        conn.close()
