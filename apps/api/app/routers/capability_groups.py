"""能力分组改名 · 写接口。

读在 `/api/catalog/capabilities`(每个组多带一个 `display_name`),
写在这里。**故意分开**:`/api/catalog/` 在 middleware 的
`_PUBLIC_PREFIXES` 里是免登录的,写操作放进那个前缀会变成免登录可写。
这个前缀不在白名单里,middleware 对它是硬 401。

改的是**显示名**,不是数据本身 —— 原组名(`category`)仍然是 key,
排序、URL 的 `?group=` 参数、前端筛选都还用它。设计理由见
`app/services/cap_group_names.py` 的模块 docstring。
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services import cap_group_names

router = APIRouter(prefix="/capability-groups", tags=["capability-groups"])


class RenameIn(BaseModel):
    """`display_name` 传空串 = 恢复这个组的默认名字。

    单独做一个 DELETE 端点也行,但"改成空"和"恢复默认"在用户那里
    本来就是同一个动作(把输入框清空回车),接口跟着合成一个,
    前端少一条分支。
    """
    category: str
    display_name: str = ""


@router.put("")
async def rename_group(body: RenameIn, request: Request):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        # middleware 已经挡了没 token 的请求,走到这里说明 token 有效但
        # payload 里没 sub —— 属于签发端的问题,不该当成 401 让用户去重登。
        raise HTTPException(500, "登录态异常 · 拿不到用户身份")

    category = (body.category or "").strip()
    if not category:
        raise HTTPException(400, "category 不能为空")

    known = cap_group_names.current_categories()
    if category not in known:
        # 组名是算出来的,用户装/删 SKILL 会让组凭空出现或消失。
        # 明说"这个组现在不存在了",比静默写一条永远不会被用到的记录强。
        raise HTTPException(404, f"分组 {category!r} 不存在 · 它可能刚被删掉了,刷新一下")

    name = (body.display_name or "").strip()
    if name:
        if len(name) > cap_group_names.MAX_LEN:
            raise HTTPException(400, f"组名最多 {cap_group_names.MAX_LEN} 个字符")
        # 换行/制表符进来会把侧栏那一行撑破,而且从界面上看不出多了什么
        if any(ch in name for ch in "\r\n\t"):
            raise HTTPException(400, "组名里不能有换行或制表符")

        # 重名校验。组名重复不会弄坏任何东西(URL 用的是原 key,点进去还是
        # 对的),但侧栏会出现两行一模一样的字、计数却不同 —— 用户会以为
        # 界面坏了。所以在这里挡掉,而不是留给前端做。
        current = cap_group_names.get_all(user_id)
        taken = {(current.get(c) or c): c for c in known if c != category}
        if name in taken:
            raise HTTPException(409, f"已经有一个组叫「{name}」了(原名 {taken[name]})· 换一个")

    cap_group_names.set_name(user_id, category, name)
    return {"ok": True, "category": category, "display_name": name or category,
            "reset": not name}
