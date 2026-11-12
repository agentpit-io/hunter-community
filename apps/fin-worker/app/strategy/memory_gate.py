"""经验消费点 · **规范化标的的精确匹配**（第四段 `R3` 建，`R5` 改口径读 `symbols` 列）。

经验对决策的作用**只有一条，且是安全方向**：

> 冻结经验集里若含「**命中本次标的的负向结论**」→ 本轮该标的 `halted`，
> 走「如实记不下单」的路径。

它**只可能让系统更少下单**，不可能凭空多下单 —— 所以即便经验有误，也不会放大风险。
（「经验命中就加仓 / 加数量」这类正向放大，本模块**绝不做**。）

## ⚠️ R5 改了什么（`plan/R5.md` §一.3）

`R5` 之前（`R3`）：判据是「`applicability` 自由文本里**精确写出**了这个标的」。
`R5` 之后（现行）：**读经验条目的 `symbols` 列**（`0042` 加的 `TEXT[]`，
元素是规范化标的 `<MARKET>:<CODE>`），**不再对 `applicability` 做任何匹配**。

为什么必须改：文本匹配只能做到「精确写出才对」，而「精确」这件事本该由**列**保证，
不该由「写自由文本的人恰巧写对了」保证。`03 §4-A` 点名：**不得从 `applicability`
文本做包含匹配**；`symbols` 这一列存在的全部理由就是让 `HK:00700` 与 `US:0700`
**判为不同标的**（裸 `0700` 撞车是包含匹配的经典事故）。

### 旧行（`symbols IS NULL`）不再拦 —— 这是**有意的**，不是回归

`0042` 明文规定 **旧行一律保持 `NULL`、不凭文本猜标的**。于是「迁移前写的经验」
（只有 `applicability`、没有 `symbols`）在新口径下**不再拦单**。这是两件事的**必然**取舍：
要么保留文本匹配（那就没真的改成读列），要么旧行不拦（那就等于少拦几条）。
任务书选了后者（`R5.md` §一.1 三条硬约束 + `03 §4-A`）。**回归不是「旧行为原样」，
而是「负向经验命中 → `halted` 这件事仍然成立」** —— 用一条带 `symbols` 的新经验复现
（见 `apps/fin-worker/tests/test_memory_gate.py` 与 `R5` 成果文档）。

## 判据（三条 + 一条负向，全部成立才算拦）

1. `kind == 'verified'` —— 真做过验证的结论，不是假设；
2. `status == '已确认'` —— 没被推翻、也不是待验证；
3. `symbols` 列**精确包含**规范化后的本次标的（字符串全等，不是包含、不是模糊）；
4. **负向**（`polarity`）：显式 `support` / `neutral` 不拦；`refute` **以及 `NULL`
   （旧行未回填，未知）**一律拦 —— **fail-closed**：拿不准它是不是负向时按负向处理，
   宁可少下一单（同 `R3`「只收紧不放松」的方向）。

「只收紧不放松」在这里读作：**从「任何已验证结论都拦」收紧到「负向才拦」，
但把「未知」也当负向拦** —— 净效果是「拦住的不比原来少（旧行照拦），新写的正向结论才放行」。

## `normalize_symbol` 必须与 api 侧一致

规范化口径的真值在 `apps/api/app/services/fin/symbols.py`（写 `symbols` 列的地方）。
fin-worker 与 api 是两个独立应用（各自 venv、各自 `sys.path`），**没法 import**，
所以这里保留一份**最小镜像**。改任一处必须同时改另一处 —— 两边不一致的表现是
**明明写进去的标的不被拦**（字符串差一个字符就不相等），且**不报错**。

```python
# api：      symbols.normalize('HK', ' aapl ')          -> 'HK:AAPL'
# fin-worker： memory_gate.normalize_symbol('HK', ' aapl ') -> 'HK:AAPL'   # 必须相等
```
"""

from __future__ import annotations

from typing import Any, Optional

# 本仓统一的市场三值（与 `symbols.MARKETS` / `memory.MARKETS` 同源）。
_MARKET_ALIASES = {
    "A": "CN_A", "CN": "CN_A", "CN-A": "CN_A", "CHA": "CN_A", "CN_A": "CN_A",
    "HK": "HK", "HKG": "HK",
    "US": "US", "USA": "US",
}
# 交易所后缀（`fin_trade.code` 里 `AAPL` 与 `AAPL.US` 两种形态都出现过）。
_EXCHANGE_SUFFIXES = (".US", ".HK", ".SH", ".SZ", ".BJ")

# 显式「不是负向」的取值 —— 只有这两个不拦；`refute` 与 `None`（未知）都拦。
_NON_BLOCKING_POLARITIES = ("support", "neutral")


def normalize_market(market: Any) -> str:
    """市场写法 → `CN_A` / `HK` / `US`；认不出 → 原样大写（**不抛**，见 `normalize_symbol`）。"""
    key = str(market or "").strip().upper().replace(" ", "")
    return _MARKET_ALIASES.get(key, key)


def normalize_symbol(market: Any, code: Any) -> str:
    """把「市场 + 代码」规范化成 `<MARKET>:<CODE>`（去空格、转大写、去交易所后缀）。

    **与 `apps/api/app/services/fin/symbols.normalize` 逐字对齐**（见模块文档末节）。
    代码为空 → 返回 `""`（调用方据此判「算不出标的」，不是编一个 `HK:`）。
    **不做零填充、不剥前导零** —— `US:0700` 里那个 `0` 是身份的一部分。
    """
    text = str(code or "").strip().upper()
    for suffix in _EXCHANGE_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
            break
    text = text.replace(" ", "")
    if not text:
        return ""
    return f"{normalize_market(market)}:{text}"


def _symbols_of(item: dict) -> set[str]:
    """这条经验的 `symbols` 列（`TEXT[]`）→ 集合。空 / 缺失 / 形状不对 → 空集合。"""
    raw = item.get("symbols")
    if not isinstance(raw, (list, tuple)):
        return set()
    return {str(s).strip() for s in raw if str(s or "").strip()}


def is_blocking(item: dict, *, market: Any, code: Any) -> bool:
    """这条冻结经验是否**拦本次标的**（判据见模块文档，四条同时成立）。

    ⚠️ `symbols` 是 `R5` 之后唯一的标的来源。**不再看 `applicability`** ——
    那是 `R3` 的过渡做法，`R5` 已按 `03 §4-A` 换成读列。
    """
    if not isinstance(item, dict):
        return False
    if str(item.get("kind") or "") != "verified":
        return False
    if str(item.get("status") or "") != "已确认":
        return False
    # 负向层（fail-closed）：只有显式 support / neutral 放行；refute 与「未知(NULL)」都拦。
    polarity = item.get("polarity")
    polarity = None if polarity in (None, "") else str(polarity).strip()
    if polarity in _NON_BLOCKING_POLARITIES:
        return False
    target = normalize_symbol(market, code)
    if not target:
        return False
    return target in _symbols_of(item)


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
                # 命中依据：**读的是列**（`R5` 起）。留 `applicability` 只为给人看上下文。
                "symbols": list(item.get("symbols") or []),
                "polarity": item.get("polarity"),
                "applicability": item.get("applicability"),
                "kind": item.get("kind"),
                "status": item.get("status"),
            }
    return None
