"""智能炒股 · 标的市场规范化（第四段 `R5` · `plan/R5.md` §一.3）。

**这个模块存在的全部理由，就是让 `HK:00700` 与 `US:0700` 判为不同标的。**

在它之前，「这条经验说的是哪只票」只能靠 `applicability` 自由文本（`R3` 的做法）。
文本匹配的下场是：**`US:0700` 会把 `HK:00700` 拦掉** —— 两串裸 `0700` 撞在一起，
而误拦会让系统在本该下单时不下单，**且没人看得出原因**（checkpoint 里只有一条
别的市场的经验）。所以 `R5` 把标的身份从「文本」搬进「列」（`0042` 的 `symbols TEXT[]`），
本模块就是那一列唯一的口径 —— **写入口径与消费口径都从这里取，别处不许再写一份**。

## 形态：`<MARKET>:<CODE>`

| 输入 | 输出 | 说明 |
|---|---|---|
| `normalize('HK', '00700')` | `HK:00700` | 任务书 §一.3 的真值 |
| `normalize('US', '0700')` | `US:0700` | **与上一行不相等** —— 市场是身份的一部分 |
| `normalize('hk', ' aapl ')` | `HK:AAPL` | 市场大小写、前后空格都归一 |
| `normalize('a', '600519')` | `CN_A:600519` | 市场别名 `a` / `cn` → `CN_A` |
| `normalize('US', 'AAPL.US')` | `US:AAPL` | 交易所后缀去掉（`fin_trade.code` 里两种形态都出现过） |

## 三条口径（改之前先读）

1. **代码不做零填充、不剥前导零。** `US:0700` 必须原样保留那个 `0` ——
   一旦「顺手」pad 成 5 位或去零，`US:0700` 就变成 `US:700`，
   任务书那条真值表当场失效。**只去空格、转大写、去交易所后缀。**
2. **市场是闭集，认不出就抛。** `CN_A` / `HK` / `US` 是本仓统一的三值
   （与 `fin_data.market_label`、`memory.MARKETS`、`fin_project_market` 同源）。
   认不出的市场**不静默保留原样**（那会让 `NASDAQ` 与 `US` 变成两个市场）；
   抛 `ValueError`，由写入口翻成 400（fail-closed）。
3. **不做模糊匹配、不加中文市场别名表。** 加别名表就是把「精确」重新变回「模糊」，
   与 `R3` 的结论一致。真要放宽，等有需求时再单独讨论。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# 本仓统一的市场三值（与 `fin_data._MARKET_LABEL` / `memory.MARKETS` 同源）。
MARKETS = ("CN_A", "HK", "US")

# 市场别名 → 规范三值。只认**写法差异**（大小写 / 常见简写），不认交易板块
# （`sh` / `sz` 是交易所不是市场，收进来会把「板块」混成「市场」）。
_MARKET_ALIASES = {
    "A": "CN_A",
    "CN": "CN_A",
    "CN-A": "CN_A",
    "CHA": "CN_A",       # 一些外部系统对 A 股的历史写法
    "CN_A": "CN_A",
    "HK": "HK",
    "HKG": "HK",
    "US": "US",
    "USA": "US",
}

# 交易所后缀：`fin_trade.code` / `fin_snapshot.code` 里 `AAPL` 与 `AAPL.US` 两种都出现过。
# 规范化时一律去掉（市场已由 `market` 参数给定，后缀是冗余的且会让同一只票有两个身份）。
_EXCHANGE_SUFFIXES = (".US", ".HK", ".SH", ".SZ", ".BJ")

# 规范化后的形态：`<大写下划线市场>:<大写代码>`。用于服务层校验写入的 `symbols`。
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z_]*:[A-Z0-9._\-]+$")


def normalize_market(market: Any) -> str:
    """把市场写法归一到 `CN_A` / `HK` / `US`；认不出 → `ValueError`（fail-closed）。"""
    key = str(market or "").strip().upper().replace(" ", "")
    resolved = _MARKET_ALIASES.get(key)
    if resolved is None:
        raise ValueError(
            f"未知市场 {market!r}：只认 {', '.join(MARKETS)}（或其写法变体 "
            f"{', '.join(sorted(_MARKET_ALIASES))}）—— 不静默保留原样")
    return resolved


def normalize_code(code: Any) -> str:
    """把代码归一：去空格、转大写、去交易所后缀。空 → `ValueError`。

    **不做零填充、不剥前导零**（见模块文档第 1 条）—— `0700` 与 `700` 是两个不同的输入。
    """
    text = str(code or "").strip().upper()
    for suffix in _EXCHANGE_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
            break
    # 代码里不该有空格（`00700` / `AAPL`）；有就说明调用方传错了，去掉再判空。
    text = text.replace(" ", "")
    if not text:
        raise ValueError(f"代码为空，无法规范化：{code!r}")
    return text


def normalize(market: Any, code: Any) -> str:
    """`(market, code)` → `<MARKET>:<CODE>`。任一认不出 → `ValueError`。"""
    return f"{normalize_market(market)}:{normalize_code(code)}"


def normalize_many(pairs: Iterable[tuple[Any, Any]]) -> list[str]:
    """一批 `(market, code)` → **去重排序**的规范化标的列表。

    排序是为了让同一批标的无论输入顺序如何，落到 `symbols` 列里都是同一个数组 ——
    `GROUP BY symbols` 要的是确定性（同一个集合不许有两个数组形态）。
    """
    out: set[str] = set()
    for market, code in pairs:
        try:
            out.add(normalize(market, code))
        except ValueError:
            # 认不出的（市场别名 / 空代码）**跳过，不编一个** ——
            # 写 `symbols` 的调用方（复核工作流）拿到的是账本里的真实代码，
            # 出现认不出的说明上游数据形态变了，宁可少写一个标的，也不塞一个错的。
            continue
    return sorted(out)


def is_symbol(value: Any) -> bool:
    """是不是一个规范化形态的标的键（服务层校验 `symbols` 入参用）。"""
    return bool(_SYMBOL_RE.match(str(value or "").strip()))


def parse(symbol: Any) -> Optional[tuple[str, str]]:
    """`<MARKET>:<CODE>` → `(market, code)`；形态不对 → `None`（不抛）。"""
    text = str(symbol or "").strip()
    if not is_symbol(text):
        return None
    market, _, code = text.partition(":")
    return market, code
