"""把 MCP 工具返回压到 AtomCode 的 16 KB 阈值以内 —— 待办池 P0-11 的绕法。

**问题**（`atomcode-capabilities/src/tools/output_artifact.rs:79/82`）：
工具返回超过 `THRESHOLD_BYTES = 16*1024` 时，内核把它砍成
「头 4096 字节 + 尾 4094 字节」，中间整段对模型不可见，只插一行
`[atomcode: output truncated …]`。两个常量都是 `const`，**全树没有任何
环境变量或配置项能改**。

**为什么这对投研发行版是 P0**：财务/筛选类返回动辄几十 KB，截断是常态。
M2 实测（`q2-thesis-review-atomcode-r1`）一次运行里 5 次 `akshare_call`
有 4 次被截断，模型没调 `fetch_output`，写出的财务复核里有三个数在任何一份
工具返回里都搜不到，却都标了工具来源。

**这里做的事**：在**我们自己的 MCP 层**先把返回压到阈值以下。
关键不只是「变小」，而是**留一份结构完整的 JSON**：

    内核的砍法  → 头 4 KB + 尾 4 KB，**JSON 语法是断的**，模型只能猜中间
    这里的砍法  → 合法 JSON + 一个 `_hca_size_guard` 块，明说「共 M 条，
                  这里只给了 N 条，剩下的是被我们裁掉的，不是没有」

模型看得懂「少了多少、为什么少、怎么拿全」，就不需要拿记忆去补。

不碰内核，纯我方代码。阈值可用 `HCA_MCP_MAX_BYTES` 调（默认 15000，
给内核的 16384 留 1 KB 余量：内核量的是整个工具返回的字节数，
不同传输路径上还会有一点包装）。
"""
from __future__ import annotations

import json
import os

# 内核写死的阈值，只作注释用途；我们自己的预算要低于它。
KERNEL_THRESHOLD_BYTES = 16 * 1024
MAX_BYTES = int(os.getenv("HCA_MCP_MAX_BYTES", "15000"))
# 至少保留这么多条，否则宁可返回一条「太大了，请缩小查询」也不要给 0 条
MIN_ITEMS = 1


def nbytes(s: str) -> int:
    return len(s.encode("utf-8"))


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _biggest_list_key(obj: dict) -> str | None:
    """找出最值得裁的那个列表字段：元素最多的那个。"""
    best, best_n = None, 0
    for k, v in obj.items():
        if isinstance(v, list) and len(v) > best_n:
            best, best_n = k, len(v)
    return best if best_n > 1 else None


def fit(text: str, tool: str = "", max_bytes: int | None = None) -> str:
    """把一段工具返回压到预算以内，**并且说清楚裁掉了什么**。

    三档处理，能用结构化的就不用粗暴的：

    1. 本来就没超 → 原样返回（绝大多数调用走这条）
    2. 是 JSON 对象且有列表字段 → 折半裁列表，补 `_hca_size_guard` 说明
    3. 其余（超大字符串 / 裁到 1 条还超） → 头部截断 + 明确说明

    不管走哪条，**返回的永远是合法 JSON**（第 3 档会包一层），
    这样模型不会看到半截结构而去猜。
    """
    budget = MAX_BYTES if max_bytes is None else max_bytes
    if nbytes(text) <= budget:
        return text

    try:
        obj = json.loads(text)
    except Exception:                                          # noqa: BLE001
        obj = None

    if isinstance(obj, list):
        obj = {"data": obj}
        wrapped_list = True
    else:
        wrapped_list = False

    if isinstance(obj, dict):
        key = _biggest_list_key(obj)
        if key is not None:
            items = obj[key]
            total = len(items)
            lo, hi, best = MIN_ITEMS, total, None
            # 二分找「能塞进预算的最大条数」
            while lo <= hi:
                mid = (lo + hi) // 2
                trial = dict(obj)
                trial[key] = items[:mid]
                trial["_hca_size_guard"] = _note(tool, key, mid, total, budget)
                s = _dump(trial)
                if nbytes(s) <= budget:
                    best, lo = s, mid + 1
                else:
                    hi = mid - 1
            if best is not None:
                return best
            # 裁到 MIN_ITEMS 还超 → 落到第 3 档

    # 第 3 档：非结构化，或者单条就超预算
    return _head_truncate(text, tool, budget, wrapped_list)


def guard_mcp_text(
    text: str,
    tool_name: str = "",
    *,
    tool: str = "",
    max_bytes: int | None = None,
) -> str:
    """`fit()` 的语义化别名，供 MCP 工具在返回出口统一调用（16KB 三档大小闸）。"""
    return fit(text, tool=tool_name or tool, max_bytes=max_bytes)


def _head_truncate(text: str, tool: str, budget: int, wrapped_list: bool) -> str:
    """按字节截头，**但量的是序列化之后的字节数**。

    这里踩过一次真坑：原先写的是 `keep = budget - 600`，按**原始**字节截，
    然后整个塞进 `json.dumps`。可 JSON 转义会膨胀 —— 一段全是 `"` 和 `\\`
    的内容进 JSON 后体积翻倍。实测一条 36 023 字节、内容是 `"\\` 重复的记录，
    裁完输出 **29 245 字节**，不但超预算，**还超过内核 16 384 的阈值** ——
    于是又被内核砍成断裂 JSON，这一档的修复等于没做。

    所以改成对**最终输出**二分：拿 `_dump()` 的实际字节数当判据。
    """
    raw = text.encode("utf-8")

    def build(keep_bytes: int) -> str:
        head = raw[:keep_bytes].decode("utf-8", "ignore")
        return _dump({
            "_hca_size_guard": {
                "reason": "单条记录就超过预算，只能按字节截断",
                "tool": tool or None,
                "original_bytes": len(raw),
                "budget_bytes": budget,
                "kept_bytes": nbytes(head),
                "warning": "下面的 raw_head 是**不完整**的文本，尾部被切掉了。"
                           "不要从它推断被切掉的部分，也不要把推断出来的数字标成工具返回的。",
                "what_to_do": "缩小查询范围（时间区间 / 条数 / 指定字段）后重新调用。",
            },
            "raw_head": head,
            **({"_note": "原始返回是一个数组"} if wrapped_list else {}),
        })

    lo, hi, best = 0, len(raw), None
    while lo <= hi:
        mid = (lo + hi) // 2
        s = build(mid)
        if nbytes(s) <= budget:
            best, lo = s, mid + 1
        else:
            hi = mid - 1
    # 连 raw_head 为空都塞不下（预算小到放不下说明本身）：仍然返回那个说明，
    # 因为「一句能读懂的说明」比「半截数据」有用。这是已知的下界。
    return best if best is not None else build(0)


def _note(tool: str, key: str, kept: int, total: int, budget: int) -> dict:
    return {
        "reason": f"返回超过 {budget} 字节，已由 HunterCode 的大小闸裁剪",
        "tool": tool or None,
        "field": key,
        "kept_items": kept,
        "total_items": total,
        "dropped_items": total - kept,
        "warning": f"`{key}` 里**只有前 {kept} 条**是真实返回的；"
                   f"其余 {total - kept} 条没有返回给你，**不是不存在**。"
                   "不要凭记忆或推断补齐，更不要把补出来的内容标成工具返回。",
        "what_to_do": "需要更多就缩小范围再调一次（按时间 / 按代码 / 按字段筛），"
                      "或者分批调用。",
        "why": "AtomCode 内核对超过 16 KB 的工具返回会砍成头尾各 4 KB、"
               "中间不可见且 JSON 语法断裂；这里提前裁成完整 JSON，"
               "好让你知道自己少看了什么。",
    }
