"""智能炒股 · 市场状态（regime）判定器（第四段 `R5` · `plan/R5.md` §一.2 / `03 §4-A`）。

**为什么单独有这一层。** `01 §3.6 承重一` 说得很直白：没有市场状态绑定，
`R6` 的提案聚合就是**跨状态污染**的 —— 牛市里「追高好」与熊市里「追高差」被聚成一团，
提案必然错。这是「投资记忆」与「普通 Agent 记忆」最大的区别，所以判定器与经验层一起交付。

## 输出：**固定四元组**（一个字节都不许多、不许少）

```python
{"label": "<闭集之一|unknown>", "rule_version": "<规则版本键>",
 "source_snapshot_id": "<SNAP-… 或 null>", "as_of": "<ISO 时刻>"}
```

## 四条不许退让的规则（`R5.md` §一.2）

1. **阈值随规则版本固定。** 规则（用什么量、什么阈值、切几档）写在下文 `RULES`
   这张**带版本号的表**里，`rule_version` 指回去。**改阈值 = 换版本** ——
   旧结论永不被新阈值重新解释（同「`plan` 写后不可改」的精神）。
2. **行情缺失 ⇒ `label='unknown'`。** 不许用「上一次的值」「默认值」或任何兜底常量顶上。
   窗口不够长、基准取不到、价格是 `None`/非正数 —— 一律 `unknown`（`_clean_closes`）。
3. **`unknown` 是独立取值，不得与明确 regime 混成同组。**
   本模块只保证「产出约定」；聚合侧的纪律（`regime_tags` 含 `unknown` 的样本不得与
   明确 regime 的样本聚合）**交给 `R6`**，写在 `R5` 成果文档的交接里。
4. **没有可靠数据源时：继续复盘、停止策略提案。** 即「能否判定出明确 regime」是提案的
   **前置条件**。本模块提供 `is_available(result)` 这一个判据；`R6` 在提案入口调它，
   `unknown` 时只复盘、不提案（`R5` 只把返回值和「不可用」状态做出来）。

## 本部署当前的现实（**如实写在代码里，别当成 bug**）

判定器要的是**基准指数的日线窗口**（`RULES[…]["benchmarks"]`）。
本部署的 `fin_snapshot` 里存的是**持仓 / 成交标的**的行情快照，**没有基准指数**——
所以 `observe()` 在现网默认返回 `None`，`detect()` 于是给出 `unknown`。
这正是上面第 4 条的形态：**没有可靠数据源 → 继续复盘、停止策略提案**，
而不是拿一只持仓股票冒充大盘。要接基准行情时接 `observe()` 一处即可
（它已经是唯一碰库的入口），判定规则与四元组输出都不用动。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from app.services.fin import symbols as symbols_svc

SHANGHAI = timezone(timedelta(hours=8))

# ── 闭集 ────────────────────────────────────────────────────────────────
# `unknown` **单列**：它不是「没判定出 bull/bear/range 之外的一种 regime」，
# 而是「没判定」这件事本身的取值（规则 2 / 3）。
UNKNOWN = "unknown"
LABELS = ("bull", "bear", "range")          # 明确 regime 的闭集（不含 unknown）
ALL_LABELS = LABELS + (UNKNOWN,)

# ── 带版本号的规则表 ────────────────────────────────────────────────────
# **改这里任何一条阈值 = 新增一个版本键**，不许原地改 —— 旧经验上记的
# `regime_source` 指着老版本，新阈值不许回头重新解释它们（规则 1）。
#
# `regime-v1` 的取值理由（写下来免得下一个人以为它是拍脑袋定的）：
#   · `ma_window=60` / `trend_lookback=120`：约一季度均线 + 半年趋势 ——
#     太短（如 5/20）会把一次回踩判成熊市，太长（如 200）本地日线常常不够长
#     而永远返回 unknown（那等于判定器不存在）。
#   · `bull_band/bear_band=±5%`：站上中期均线且半年涨跌超过 5% 才算**趋势**，
#     中间地带一律 `range`（宁可说「震荡」，不硬给方向）。
#   · 判定用**日线收盘**，`min_bars=121`（= trend_lookback + 1）：不够就不判（规则 2）。
RULES: dict[str, dict[str, Any]] = {
    "regime-v1": {
        # 各市场的基准指数（展示 / 取数身份；`observe` 按它去取日线窗口）。
        "benchmarks": {"CN_A": "000300", "HK": "HSI", "US": ".INX"},
        "ma_window": 60,
        "trend_lookback": 120,
        "bull_band": 0.05,
        "bear_band": -0.05,
        "min_bars": 121,
    },
}

# 默认规则版本（写入方应显式带上判定器返回的那个 `rule_version`，别自己硬编码）。
RULE_VERSION = "regime-v1"


def _rules(rule_version: str) -> dict[str, Any]:
    rules = RULES.get(rule_version)
    if rules is None:
        # 未登记的版本 = 编程 / 配置错误，**不静默降级成 unknown** ——
        # 那会把「版本写错了」伪装成「没行情」，两件完全不同的事。
        raise ValueError(
            f"未登记的 regime 规则版本 {rule_version!r}；已登记：{', '.join(RULES)}")
    return rules


def _now_iso() -> str:
    return datetime.now(SHANGHAI).isoformat()


def _clean_closes(observations: Any) -> Optional[list[float]]:
    """从 observations 里取出**干净**的收盘序列（升序）。任一格不合法 → `None`。

    「不合法」包括：不是 dict、`closes` 缺失 / 空 / 不是序列、某格是 `None` /
    非有限数 / ≤ 0。**任一格坏掉就整条作废**（返回 `None`）——
    悄悄跳过坏格子会拼出一个**不是真的**窗口，看起来像判定过了（规则 2 的背面）。
    """
    if not isinstance(observations, dict):
        return None
    raw = observations.get("closes")
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    out: list[float] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        num = float(value)
        if num != num or num in (float("inf"), float("-inf")) or num <= 0:
            return None
        out.append(num)
    return out


def classify(observations: Any, *, rule_version: str = RULE_VERSION) -> str:
    """纯函数：日线窗口 → 闭集取值之一，或 `unknown`。

    判据（`regime-v1`）：站上 `ma_window` 日均线 **且** 近 `trend_lookback`
    交易日涨跌超阈值 → `bull` / `bear`；其余（含窗口不足、数据不合法）→ `range` / `unknown`。
    """
    rules = _rules(rule_version)
    closes = _clean_closes(observations)
    if closes is None or len(closes) < rules["min_bars"]:
        # 规则 2：窗口不够长 = 没行情，**不许拿短窗口冒充**。
        return UNKNOWN
    window = rules["ma_window"]
    ma = sum(closes[-window:]) / window
    base = closes[-1 - rules["trend_lookback"]]
    last = closes[-1]
    ret = last / base - 1.0
    if last > ma and ret >= rules["bull_band"]:
        return "bull"
    if last < ma and ret <= rules["bear_band"]:
        return "bear"
    return "range"


def _source_snapshot_id(observations: Any, market: Optional[str],
                        given: Optional[str]) -> Optional[str]:
    """四元组里的 `source_snapshot_id`。

    调用方给了就用它（`observe()` 从 `fin_snapshot` 读时给的是**真实的** `SNAP-…`）；
    没给但有窗口 → 用「基准 + 最后一根日期」推一个**确定性**身份。
    两者都取不到 → `None`（四元组允许 null，任务书 §一.2）。
    """
    if given:
        return str(given)
    if not isinstance(observations, dict) or market is None:
        return None
    code = str(observations.get("code") or "").strip()
    dates = observations.get("dates") or []
    last = str(dates[-1]).strip() if dates else ""
    if code and last:
        return f"SNAP-BENCH-{market}-{code}-{last}"
    return None


def detect(*, market: Optional[str], observations: Any = None,
           source_snapshot_id: Optional[str] = None, as_of: Any = None,
           rule_version: str = RULE_VERSION) -> dict:
    """**唯一判定入口**。返回固定四元组（见模块文档）。**缺行情不抛错，给 `unknown`。**

    `market` 为 `None`（一期单市场语义下可能没传）或认不出 → `unknown`：
    认不出市场就没有基准身份，没有基准身份就没有 regime —— 不猜。
    """
    rules = _rules(rule_version)                      # 版本写错 = 抛（不伪装成 unknown）
    mkt: Optional[str] = None
    if market not in (None, ""):
        mkt = symbols_svc.normalize_market(market)    # 未知市场 = 抛（fail-closed）
        if mkt not in rules["benchmarks"]:
            raise ValueError(f"规则 {rule_version} 没有 {mkt} 的基准配置")

    if mkt is None or observations is None:
        label = UNKNOWN
    else:
        label = classify(observations, rule_version=rule_version)

    return {
        "label": label,
        "rule_version": rule_version,
        "source_snapshot_id": _source_snapshot_id(observations, mkt, source_snapshot_id),
        "as_of": _iso(as_of) if as_of is not None else _now_iso(),
    }


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=SHANGHAI)
        return dt.isoformat()
    return str(value)


def is_available(result: Any) -> bool:
    """这份判定结果**可不可用于提案**（`R5.md` §一.2 规则 4 的判据，`R6` 用）。

    `unknown` = 没有可靠数据源 ⇒ 继续复盘、**停止策略提案**。仅此一处判据，
    别在提案模块里另写一份 `label != 'unknown'`。
    """
    return isinstance(result, dict) and str(result.get("label") or "") in LABELS


def tags_for(result: Any) -> list[str]:
    """判定结果 → 写进经验的 `regime_tags`。`unknown` 也照写（规则 3）。"""
    if not isinstance(result, dict):
        return [UNKNOWN]
    label = str(result.get("label") or "").strip()
    return [label] if label else [UNKNOWN]


# ════════════════════════════════════════════════════════════════════════
# 取数（**本模块唯一碰库的地方**）
# ════════════════════════════════════════════════════════════════════════

def _benchmark_code(market: str, rule_version: str) -> str:
    return _rules(rule_version)["benchmarks"][market]


def observe(*, market: Optional[str], conn=None, provider: Optional[Callable] = None,
            rule_version: str = RULE_VERSION) -> Optional[dict]:
    """取某市场**基准指数**的日线窗口 → `{"code","closes","dates","source_snapshot_id"}`；取不到 → `None`。

    `provider` 给了就用它（测试 / 以后换数据源用）；否则从 `fin_snapshot` 里
    按基准代码取快照序列（升序），**本部署没有基准快照 → `None`**（见模块文档末节）。

    只取 `quality != 'missing'` 且 `last_price` 非空的行 —— 缺行不补齐、
    不拿前一天顶替（规则 2）。序列长度由 `classify` 判（不够 → `unknown`）。
    """
    if market in (None, ""):
        return None
    mkt = symbols_svc.normalize_market(market)
    if provider is not None:
        return provider(mkt)
    if conn is None:
        return None
    code = _benchmark_code(mkt, rule_version)
    try:
        import psycopg2.extras
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT snapshot_id, last_price, "
                "       (snapshot_time AT TIME ZONE 'Asia/Shanghai')::date AS day "
                "FROM fin_snapshot "
                "WHERE code = %s AND last_price IS NOT NULL AND quality <> 'missing' "
                "ORDER BY snapshot_time ASC",
                (code,),
            )
            rows = [dict(r) for r in cur.fetchall()]
    except Exception:  # noqa: BLE001 —— 取数失败 = 没有行情，不是判定失败
        return None
    if not rows:
        return None
    return {
        "code": code,
        "closes": [float(r["last_price"]) for r in rows],
        "dates": [str(r["day"]) for r in rows],
        "source_snapshot_id": rows[-1].get("snapshot_id"),
    }


def detect_for_market(*, market: Optional[str], conn=None,
                      provider: Optional[Callable] = None,
                      rule_version: str = RULE_VERSION,
                      as_of: Any = None) -> dict:
    """`observe()` + `detect()` 的组合（复核工作流用这一条，别自己拼）。"""
    obs = observe(market=market, conn=conn, provider=provider, rule_version=rule_version)
    sid = obs.get("source_snapshot_id") if isinstance(obs, dict) else None
    return detect(market=market, observations=obs, source_snapshot_id=sid,
                  as_of=as_of, rule_version=rule_version)
