"""经验消费点 · **规范化标的的精确匹配**（第四段 R3 · `plan/R3.md` §一.4）。

R3 这一轮经验对决策的作用**只有一条，且是安全方向**：

> 冻结经验集里若含「命中本次标的」的负向结论 → 本轮该标的 `halted`，走「如实记不下单」的路径。

它**只可能让系统更少下单**，不可能凭空多下单 —— 所以即便经验有误，也不会放大风险。
（「经验命中就加仓 / 加数量」这类正向放大，本轮**绝不做**。）

## 判据 = 规范化标的的**精确匹配**，不是文本包含

`03 §4-A` 点名：「**不许**对 `applicability` 自由文本做**包含**匹配」。缘由是一句话：
包含匹配的下场是 **`US:0700` 把 `HK:00700` 拦掉**（两串裸 `0700` 撞车），
而误拦会让系统在本该下单时不下单，**且没人看得出原因**（checkpoint 里只有一条别的市场的经验）。

所以口径写死成两步（`spec` 逐字）：

1. 把本次标的规范化成 **`<market>:<code>`** —— 市场取自项目 / 决策上下文，代码去空格转大写。
   例：`HK` + `00700` → `HK:00700`。
2. 把 `applicability` **按非字母数字字符切词**，逐词规范化后与之**全等比较**。

第 2 步的「逐词」在本实现里是一串**连续**的词：`signature("HK:00700") = ("HK", "00700")`，
在 `applicability` 的切词序列里找**连续子序列**（`("HK","00700")` 两词紧邻）。
这样写有三个好处，每一条都对着真值表：

| `applicability` 原文 | 本次标的 | 命中？ | 为什么 |
|---|---|---|---|
| `HK:00700 追高后回撤` | `HK:00700` | ✅ | 切词 `["HK","00700","追","高",…]` 里 `HK`,`00700` 连续且全等 |
| `US:0700 同类形态` | `HK:00700` | ❌ | 连续子序列是 `("US","0700")`，市场不同 —— 这正是「规范化」要解决的问题 |
| `放量后 0700 回落` | `HK:00700` | ❌ | 只有 `00700` 一个词，凑不出两词的签名（**裸代码不是标的声明**） |

`CN_A` 这类带下划线的市场码天然被切词分开（`CN_A:601398` → `("CN","A","601398")`），
所以用连续子序列而不是两词对，两个市场的写法共用一套代码。

## 宁可保守（漏拦），也不模糊匹配

写法不同的同一只票（`港股 00700` → 切词是 `("港","股","00700")`，市场词是中文）**判不命中**。
这是**有意的**：加中文市场别名表就是把「精确」重新变成「模糊」——
而且经验写 `applicability` 的是我们自己（复核工作流）与真人（成长页），
都该写规范化形态。真要放宽，等 `R5` 的 `symbols` 列落地后**改读那一列**（见正文 §交接）。

## ⚠️ 下一阶段交接（`R5` 必读）

`symbols` 这一列**本轮还没有**（`0042_memory_layer.sql` 在 `R5` 才加）。所以本轮只能对
`applicability` 做上面的规范化匹配。**等 `R5` 加了 `symbols` 之后，这里的判定要改成读
`symbols` 列**（那时不再依赖自由文本）—— 本模块就是那个改动点，别在别处另写一份。
"""

from __future__ import annotations

import re
from typing import Any, Optional

# 切词：**非字母数字**都算分隔符（中文字、空格、`:` `-` `/` `_` `.` 全是）。
_NON_ALNUM = re.compile(r"[^0-9A-Za-z]+")


def normalize_symbol(market: Any, code: Any) -> str:
    """把「市场 + 代码」规范化成 `<MARKET>:<CODE>`（去空格、转大写）。

    市场取**决策上下文里的规范三值**（`CN_A` / `HK` / `US`），不在这里翻译别名 ——
    `_resolve_market` 已经把它归一到那三个值，这里再猜一次就是第二套口径。
    """
    return f"{str(market or '').strip().upper()}:{str(code or '').strip().upper()}"


def symbol_signature(symbol: Any) -> tuple[str, ...]:
    """规范化标的 → 切词后的词序列（全大写）。`HK:00700` → `("HK","00700")`。"""
    return tuple(w.upper() for w in _NON_ALNUM.split(str(symbol or "")) if w)


def applicability_hits(applicability: Any, target_symbol: Any) -> bool:
    """`applicability` 里**有没有精确写出**这个标的（不是包含、不是模糊）。

    判据：`applicability` 的切词序列里存在**连续的一段**与 `signature(target)` 全等。
    空目标 / 空 `applicability` / 单串裸代码 → 一律不命中（保守）。
    """
    sig = symbol_signature(target_symbol)
    if not sig or not str(target_symbol or "").strip():
        return False
    words = [w.upper() for w in _NON_ALNUM.split(str(applicability or "")) if w]
    n = len(sig)
    if n == 0 or len(words) < n:
        return False
    for i in range(len(words) - n + 1):
        if tuple(words[i:i + n]) == sig:
            return True
    return False


def is_blocking(item: dict, *, market: Any, code: Any) -> bool:
    """这条冻结经验是否**拦本次标的**。

    三条同时成立才算（`plan/R3.md` §一.4 的字面判据）：

    1. `kind == 'verified'` —— 真做过验证的结论，不是假设；
    2. `status == '已确认'` —— 没被推翻、也不是待验证；
    3. `applicability` **精确命中**本次标的（`applicability_hits`）。

    ⚠️ **「负向」这一层本轮判不出来**：区分正负的 `polarity` 列在 `0042`（`R5`）才加。
    本轮按任务书的字面判据执行 —— 结论是**只收紧**（任何关于该标的的已验证结论都会拦），
    方向安全。`R5` 拿到 `polarity` 之后应在这里加上 `polarity='refute'`（或等价物），
    并回来更新本模块与成果文档（这条交接写在 `R3` 成果文档「下一阶段交接」里）。
    """
    if not isinstance(item, dict):
        return False
    if str(item.get("kind") or "") != "verified":
        return False
    if str(item.get("status") or "") != "已确认":
        return False
    return applicability_hits(item.get("applicability"), normalize_symbol(market, code))


def blocking_experience(items: Any, *, market: Any, code: Any) -> Optional[dict]:
    """冻结经验集里**第一条**拦本次标的的条目；没有 → `None`。

    「第一条」按 `memory.query` 的返回顺序（`as_of` 倒序、id 升序）——
    确定性，重放时同一份冻结集给出同一条，所以 checkpoint 里记的 `experience_id` 稳定。
    """
    for item in items or []:
        if is_blocking(item, market=market, code=code):
            return {
                "experience_id": item.get("experience_id"),
                "statement": item.get("statement"),
                "applicability": item.get("applicability"),
                "kind": item.get("kind"),
                "status": item.get("status"),
            }
    return None
