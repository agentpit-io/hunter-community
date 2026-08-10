"""Chat debate 用的股票解析 · 复用 agents.sentinel.stock_search

用户输入可能是: "腾讯" / "00700" / "腾讯控股 00700" / "600519" / "贵州茅台"
返回标准 (code, name) · 失败抛 HTTPException(400)
"""
from fastapi import HTTPException

from agents.sentinel.stock_search import search as stock_search


import re


def _clean_query(q: str) -> str:
    """剥掉 SKILL 模板残留的 {占位符} 括号和常见杂质字符

    用户点 SKILL 卡后 · 若没完全替换占位符 · 输入框会残留 { 和 }
    (发生在:焦点没锁到 textarea · 用户点了别处再来打字 · 选区丢失)
    这里最后一道拦截 · 不加对用户不友好的错误提示 · 直接尽力猜。
    """
    if not q:
        return ""
    # 剥占位符括号(全/半角)
    q = re.sub(r"[{}【】\[\]（）()<>《》「」『』]", "", q)
    # 剥常见前后缀
    q = re.sub(r"^(股票\s*[:：]?\s*|代码\s*[:：]?\s*)", "", q)
    return q.strip()


async def resolve_stock(query: str) -> tuple[str, str]:
    """
    解析用户输入的股票查询串·返 (标准6位代码, 中文名称)

    Args:
        query: 用户输入 · 如 "腾讯" / "600519" / "贵州茅台" / "{300308}"

    Returns:
        (code, name) 元组·如 ("600519", "贵州茅台")

    Raises:
        HTTPException 400: 未找到匹配股票
    """
    q = _clean_query(query)
    if not q:
        raise HTTPException(400, "股票查询不能为空")

    results = await stock_search(q, limit=1)
    if not results:
        raise HTTPException(
            400,
            f"未找到股票 '{q}' · 请输入 A 股代码(如 600519)或中文名(如 贵州茅台)"
        )

    top = results[0]
    code = str(top.get("code") or "").strip()
    name = str(top.get("name") or "").strip()
    if not code or not name:
        raise HTTPException(400, f"股票 '{q}' 解析失败 · 数据源返回异常")

    return code, name
