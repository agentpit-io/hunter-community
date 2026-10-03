"""智能炒股 · 统一经验系统 · Memory Service（**唯一入口**，第四段 R2）。

这是**全仓唯一**碰 `fin_experience` / `fin_experience_evidence` / `fin_memory_snapshot`
三张表的模块。对外只有两个函数，名字照 `01方案 §10.1` 的工具名，一个字节都不用改：

| 工具 | 语义 |
|---|---|
| `append_evidence(...)` | **唯一写入口**（八条硬校验，任一不过 → `MemoryValidationError` → 路由翻 400） |
| `query(...)` | **唯一读入口**；`freeze=True` 时顺带冻结并返回 `memory_snapshot_id` |

**冻结不是第三个工具**（方案 §3.4）：一次 `query(freeze=True)` 同时「查到」与「冻住」。
这样「唯一入口」这句话在**工具清单层面**也是真的（还是那两个），与
`09 :492` 的表头注释 `memory.query / memory.append_evidence · 唯一入口` 逐字一致。

---

## 为什么「过滤在服务端、不在调用方」

`01方案 §8.4`：「历史回放不得读取后来才形成的经验。最终保留测试集结果不得回流到
正在选择候选的搜索记忆，防止评估泄露。」这句话如果交给调用方（工作流 / 前端）自己记得
过滤，等于把红线押在每一处调用点的自觉上 —— 只要有一处忘了，泄露就发生了，而且**不报错**。
所以这里把四条硬过滤**焊死在 SQL 里**，`query()` 的签名里**根本没有**能放开它们的参数：

1. **防评估泄露（零开关）**：永远 `WHERE exposure_scope='searchable' AND holdout_tainted=false`。
   **没有** `include_holdout` / `debug` / `admin` 之类参数能放开 —— 「不实现就不可能被误开」，
   比「默认关闭的开关」强一档（追加规则 §五 第 1 条）。
2. **时间权限**：永远 `WHERE as_of <= <基准时刻>`。基准 = 入参 `as_of`；不传就用
   「查询发起时刻」，并在响应里回显 `as_of_basis` 让它可以被审计。
3. **`for_decision=True` 的严口径**：只返回 `kind IN ('fact','verified') AND status='已确认'`
   且 `valid_until IS NULL OR valid_until > now()`。**`hypothesis` 从决策路径上直接不返回** ——
   「假设不下单」由**服务端不给**，而不是让工作流自己记得过滤。
4. **权限**：JWT 通道按 `sub` → `fin_project.user_id` 过滤（口径同
   `routers/fin_report.py:98`）；跨用户一律 **404**，不区分「不存在」与「无权限」。

## `append_evidence` 的八条硬校验（任一不过 → 400，绝不静默降级）

| # | 规则 |
|---|---|
| 1 | `evidence` 至少一行 —— 「经验必须带证据」 |
| 2 | `kind='hypothesis'` ⇒ `status` 强制 `'待验证'`（表 CHECK 双保险） |
| 3 | 每条证据 `ref_id` 必须**真实存在**于对应表（防「编一个引用」） |
| 4 | 任一证据 `holdout_tainted=True` ⇒ 本条经验强制 `exposure_scope='holdout_only'` + `holdout_tainted=True`（传染，不可申诉） |
| 5 | `kind='verified'` ⇒ `method` 与 `sample_size` 必填、`sample_size ≥ 1` |
| 6 | `statement` 里出现**阿拉伯数字** ⇒ 400（数字一律走 `evidence_kind='fact'` 引用） |
| 7 | `source` **由服务端按调用方定**（内网口令 → `ai`；JWT → `human_mixed` + `created_by='user:<uuid>'`），**入参里传什么都不认** |
| 8 | **没有「改」入口，只有「加」**：推翻 = 追加一条 `status='已推翻'` 并把原条目 `superseded_by` 指向它；**不 DELETE** |

## 三条本模块做过的决定（写下来免得下一个人重猜）

- **规则 6 的判据 = 「含任一阿拉伯数字即 400」**（半角 `0-9` + 全角 `０-９`）。
  `report.py:extract_numbers` 会先把时刻 / 日期掩掉再抽数字 —— 那对**报告正文**是对的
  （日期不是数字主张），但方案 §3.2 规则 6 点名要拦的正是「15:30」这种写法，所以这里
  **不能复用**它，另写一个更窄的判据：只要出现阿拉伯数字就拒。
  `statement` 是人可读的一句话结论，结论里不该有裸数字。
  适用范围**只有 `statement`** —— `applicability` / `invalidation_condition` 是边界描述，
  方案自己的例子就含数字（`港股主板 · 14:30 后` / `成交额口径变更或样本 < 30`）。
- **`evidence_kind='external'` 本轮一律 400**。四类可校验的引用（`trade` / `report` /
  `fact` / `snapshot`）各有对应表可以「验在不在」；`external`（外部文档 id）在库内
  **没有任何可校验的来源** —— 收下它就等于绕过规则 3「防编造引用」。所以宁可不收，
  报错里点名可用的四类（「空的比假的好」）。
- **`market` 过滤含跨市场结论**：表注释写明 `market` 为 `NULL` = **跨市场结论**，
  所以按某市场查询时返回「该市场结论 + 跨市场结论」（`market = %s OR market IS NULL`），
  不传 `market` 则返回全部。这一条是本模块对 schema 注释的忠实读法，写在测试里。

## R3 加的两件（`plan/R3.md` §一.3b / §一.1）

- **冻结带内容哈希（§3b）**：`query(freeze=True)` 写快照时，把每条经验的**内容指纹**
  （`kind|status|statement|applicability|valid_until|superseded_by`）与一个 `aggregate_hash`
  一并写进 `fin_memory_snapshot.query_filter`。**不新增列、不新增迁移**（`query_filter` 本就是 JSONB）。
  口径是契约，写在 `CONTENT_HASH_VERSION` 旁边；回放时 `get_snapshot` 会算一遍此刻的指纹，
  在 `content_drift` 里点出「哪些条被改过可见性」—— 这是「只冻 id 名单」验不出来的那件事。
- **`human_mixed` 由证据真值判定（§4.1）**：内网通道写经验时，若**每条证据都是人工成交**
  （`fin_trade.source ∈ {human, human_confirmed}`），服务端记 `source='human_mixed'`；否则记 `ai`。
  复核工作流没有 JWT，人工成交的「人味」只能这样落到服务端判定里（见 `resolve_source`）。
  **没有新增第三个工具名** —— 仍然是 `memory.query` / `memory.append_evidence` 两个。

## R5（`0042`）加的三件（`plan/R5.md` §一）

- **九个结构化列**：`memory_layer` / `polarity` / `symbols` / `strategy_keys` /
  `regime_tags` / `regime_source` / `importance` / `last_validated_at` / `duplicate_of`。
  **旧行一律 NULL，不回填、不凭文本猜**（迁移侧见 `0042` 文件头）。
  `memory.query` 的返回体（`_item`）带上它们，消费方（`memory_gate` / `R6` 聚合）
  从此读列，不再读 `applicability` 自由文本。
- **规则 9**（`01 §8.3`）：`memory_layer='strategy'` ⇒ 必须有 `strategy_keys`
  且 `kind='verified'` —— 策略记忆是「以后该怎么买卖」，猜一条进来会让进化按想象改参数。
- **`importance` 只影响展示排序，不参与统计加权**（`03 §4-A`）——
  `R6` 的提案聚合**不许**按它加权。这条写在这里，是因为将来加聚合的人一定会先看这个模块。

## 权限：谁可以碰这三张表

api 连接用的是库属主身份（见 `0041` 文件头那三条理由），能力来自**连接身份**。
`fin_paper_rw` 对这三张表**一行授权都没有**（R1 验收项）—— 「唯一入口」最硬的收口
就是账本角色连 `SELECT` 都没有。

fin-worker **不碰这三张表**：它经 `HunterApiClient` 走内网口令调用本模块的路由，
全程不连数据库（`apps/fin-worker/tests/test_no_ledger_access.py` 的守护口径已扩展到经验三表）。
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Optional

import psycopg2
import psycopg2.extras

from app.services.fin import switches
from app.services.fin import symbols as symbols_svc

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://hunter:hunter@localhost:5432/hunter")

SHANGHAI = timezone(timedelta(hours=8))

# ── 枚举（与 0041 的 CHECK 逐字一致，改一处必须改另一处）────────────────────
KINDS = ("fact", "hypothesis", "verified")
STATUSES = ("待验证", "已确认", "已推翻")
SOURCES = ("ai", "human_mixed")
EVIDENCE_KINDS = ("trade", "report", "fact", "snapshot", "external")
PURPOSES = ("decision", "review", "holdout")
MARKETS = ("CN_A", "HK", "US")

# ── R5（`0042`）新列的闭集 —— 与迁移里的 CHECK **逐字一致**，改一处必须改另一处 ──
# `memory_layer` 取 `02` 的「多层记忆」一节（Episodic / Semantic / Procedural）
# 外加 `strategy`（策略记忆）。**闭集**，不许自由文本 —— 自由文本无法确定性聚合。
MEMORY_LAYERS = ("episodic", "semantic", "procedural", "strategy")
# 支持 / 推翻 / 中性。`refute`（失败经验）是自进化的燃料，必须保留。
POLARITIES = ("support", "refute", "neutral")

# ── 枚举的中文名（`R9` · 成长页筛选下拉用）──────────────────────────────────
# 唯一的用途是「界面显示」，**不参与任何判定**（判定读的是上面那些 key）。
# 放在服务端而不是前端：成长页要求「筛选值全部来自后端、前端不写死任何枚举」——
# 枚举的定义在服务端（上面那几行），它的显示名就该跟它待在一起，否则就是同一件事
# 写在两处（`CLAUDE.md` 的「同一件事写在多处」那条）。
KIND_LABELS = {"fact": "事实", "hypothesis": "假设", "verified": "验证结论"}
STATUS_LABELS = {"待验证": "待验证", "已确认": "已确认", "已推翻": "已推翻"}
MARKET_LABELS = {"CN_A": "A 股", "HK": "港股", "US": "美股"}
SOURCE_LABELS = {"ai": "AI 自主", "human_mixed": "人机混合"}
MEMORY_LAYER_LABELS = {
    "episodic": "事件记忆", "semantic": "语义记忆",
    "procedural": "行为记忆", "strategy": "策略记忆",
}
POLARITY_LABELS = {"support": "支持", "refute": "失败经验（推翻）", "neutral": "中性"}
# regime 的显示名：与 `regime.LABELS`（明确 regime）+ `unknown` 对齐，改一处必须改另一处。
REGIME_LABELS = {"bull": "牛市", "bear": "熊市", "range": "震荡", "unknown": "未知"}

# 有对应表、可以「验在不在」的引用（规则 3）。`external` **故意不在**这里 —— 见模块文档。
# `trade` 也不在这张表里：它除了「验在不在」还要把 `source` 读回来（人机归因，见 `_verify_refs`）。
REF_TABLE_SQL = {
    "report": "SELECT 1 FROM fin_report WHERE report_id = %s",
    "fact": "SELECT 1 FROM fin_report_fact WHERE report_id || ':' || metric_key = %s",
    "snapshot": "SELECT 1 FROM fin_snapshot WHERE snapshot_id = %s",
}

# 调用方通道 → 强制 source / created_by（规则 7）。**入参里传什么都没用**。
CALLER_SOURCE = {"internal": "ai", "jwt": "human_mixed"}

# `fin_trade.source` 的三个取值里，后两个是**人工**成交（`0023:230` 的 CHECK）。
HUMAN_TRADE_SOURCES = ("human", "human_confirmed")

# ── 内容指纹（R3 §3b）· **这是契约，不是实现细节** ──────────────────────────
# 冻结快照的 `query_filter.content_hashes` / `aggregate_hash` 由下面三个函数算出。
# 口径：sha256( kind \x1f status \x1f statement \x1f applicability \x1f valid_until \x1f superseded_by )，
#       NULL 一律写空串；`aggregate_hash` = sha256(按 experience_id 升序的 "<id>=<hash>\n" 拼接)。
# **换口径 = 换版本**：必须改 CONTENT_HASH_VERSION（不许静默改），口径本身写在 R3 成果文档里。
CONTENT_HASH_VERSION = "1"
_HASH_SEP = "\x1f"            # 单元分隔符 —— 正常文本里不会出现，比 "|" 稳
_HASH_FIELDS = ("kind", "status", "statement", "applicability", "valid_until", "superseded_by")

# 规则 6：statement 里出现阿拉伯数字（半角或全角）即拒。
_ARABIC_DIGIT_RE = re.compile(r"[0-9０-９]")


class MemoryValidationError(ValueError):
    """任一硬校验不过 —— 路由据此回 **400**（绝不静默降级）。"""


class MemoryDisabledError(Exception):
    """经验库总开关 `FIN_MEMORY_ENABLED=0` —— 写入口拒绝。

    **不是** `MemoryValidationError` 的子类：请求本身没毛病，是这个部署没开这项能力，
    所以路由把它翻成 **503**、不是 400（见 `routers/fin_memory.py` 的两个写 handler）。
    `query` 那边不抛这个 —— 它按 §2 的语义**返回空集合**。
    """


def get_conn():
    """与 `store.py` / `report.py` 同口径：psycopg2 直连 `DATABASE_URL`，无连接池。"""
    return psycopg2.connect(DATABASE_URL)


# ════════════════════════════════════════════════════════════════════════
# 一 · 纯函数（不碰库，可在 tests/test_fin_memory.py 里直接测）
# ════════════════════════════════════════════════════════════════════════

def statement_has_numbers(text: Any) -> bool:
    """`statement` 里是否出现阿拉伯数字（规则 6 的判据）。

    半角 `0-9` 与全角 `０-９` 都算 —— 两者都是阿拉伯数字，视觉上也都是数字。
    中文数字（一二三）**不算**：那不是裸数字主张，是人话的一部分。
    """
    return bool(_ARABIC_DIGIT_RE.search(str(text or "")))


def _parse_dt(value: Any, *, field: str) -> Optional[datetime]:
    """把入参时间归一成**带时区**的 datetime；空 → None。

    没有时区的一律按上海时间（+08:00）解释 —— 本仓的账本口径是上海时间
    （`fin_market_calendar` / 额度重置都按上海），`as_of` 是「这是几时的认知」，
    不能因为调用方少写了一个 `+08:00` 就把它当成 UTC（那会差 8 小时，回放边界静默偏移）。
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day, tzinfo=SHANGHAI)
    else:
        text = str(value).strip()
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
        except ValueError as exc:
            raise MemoryValidationError(f"{field} 不是合法的 ISO 时间：{value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SHANGHAI)
    return dt


def _clean_num(value: Any, *, field: str) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):        # bool 是 int 的子类，别让它混成 0/1
        raise MemoryValidationError(f"{field} 应是数字，收到布尔值")
    if not isinstance(value, (int, float, Decimal)):
        raise MemoryValidationError(f"{field} 应是数字，收到 {type(value).__name__}")
    num = float(value)
    if num != num or num in (float("inf"), float("-inf")):
        raise MemoryValidationError(f"{field} 应是有限数字")
    return num


def _clean_str_list(value: Any, *, field: str) -> Optional[list[str]]:
    """一个字符串数组入参 → **去重排序**的列表；空 / 未给 → `None`。

    排序是为了确定性：同一批值无论输入顺序如何，落到列里都是同一个数组
    （`GROUP BY` 要的是同一个集合只有一个形态）。元素必须是字符串。
    """
    if value is None or value == "":
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple, set)):
        raise MemoryValidationError(f"{field} 应是字符串数组，收到 {type(value).__name__}")
    out: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise MemoryValidationError(f"{field} 的元素应是字符串，收到 {type(item).__name__}")
        text = item.strip()
        if not text:
            raise MemoryValidationError(f"{field} 的元素不能为空")
        out.add(text)
    return sorted(out)


def _clean_symbols(value: Any) -> Optional[list[str]]:
    """`symbols` 入参 → **规范化形态**的去重排序列表（`<MARKET>:<CODE>`）。

    服务端**再校验一次形态**：写入口径在 `services/fin/symbols.py`，但入参可能来自
    前端 / 工作流，绕过那一步直接塞一个裸代码（`0700`）就会让 `HK:00700` 与 `US:0700`
    重新撞车 —— 那正是这一列要消灭的东西。形状不对 → 400，不静默改写。
    """
    items = _clean_str_list(value, field="symbols")
    if items is None:
        return None
    for sym in items:
        if not symbols_svc.is_symbol(sym):
            raise MemoryValidationError(
                f"symbols 元素 {sym!r} 不是规范化标的形态（应形如 HK:00700 / US:0700 / CN_A:601398）"
                "—— 规范化走 services/fin/symbols.normalize，别直接塞裸代码")
    return items


def validate_append(
    *,
    kind: Any,
    statement: Any,
    evidence: Any,
    market: Any = None,
    status: Any = None,
    method: Any = None,
    sample_size: Any = None,
    confidence: Any = None,
    uncertainty: Any = None,
    supersedes: Any = None,
    memory_layer: Any = None,
    polarity: Any = None,
    symbols: Any = None,
    strategy_keys: Any = None,
    regime_tags: Any = None,
    regime_source: Any = None,
    importance: Any = None,
    last_validated_at: Any = None,
    duplicate_of: Any = None,
) -> dict:
    """`append_evidence` 的**纯校验**（规则 1/2/5/6 + 枚举 + 取值范围），不碰库。

    返回归一后的字段 dict（`status` / `market` / `evidence` 都已是服务端决定的形态）。
    任何一条不过 → `MemoryValidationError`。
    """
    # ── kind / statement ──────────────────────────────────────────────
    kind = str(kind or "").strip()
    if kind not in KINDS:
        raise MemoryValidationError(
            f"kind 必须是 {', '.join(KINDS)} 之一，收到 {kind or '(空)'!r}")

    statement = str(statement or "").strip()
    if not statement:
        raise MemoryValidationError("statement 不能为空")
    # 规则 6：结论里不许有阿拉伯数字。
    if statement_has_numbers(statement):
        bad = "".join(sorted(set(_ARABIC_DIGIT_RE.findall(statement))))
        raise MemoryValidationError(
            f"statement 里不许出现阿拉伯数字（收到 {bad!r}）—— "
            "数字一律通过 evidence_kind='fact' 的证据引用，不要写进结论")

    # ── market ────────────────────────────────────────────────────────
    market = None if market in (None, "") else str(market).strip()
    if market is not None and market not in MARKETS:
        raise MemoryValidationError(
            f"market 必须是 {', '.join(MARKETS)} 之一或留空（跨市场结论），收到 {market!r}")

    # ── 规则 1：证据至少一行 ──────────────────────────────────────────
    if not isinstance(evidence, (list, tuple)) or not evidence:
        raise MemoryValidationError("evidence 至少一行 —— 经验必须带证据")

    cleaned: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for i, ev in enumerate(evidence):
        if not isinstance(ev, dict):
            raise MemoryValidationError(f"evidence[{i}] 应是对象")
        ekind = str(ev.get("evidence_kind") or "").strip()
        ref_id = str(ev.get("ref_id") or "").strip()
        if ekind not in EVIDENCE_KINDS:
            raise MemoryValidationError(
                f"evidence[{i}].evidence_kind 必须是 {', '.join(EVIDENCE_KINDS)} 之一，收到 {ekind or '(空)'!r}")
        if not ref_id:
            raise MemoryValidationError(f"evidence[{i}].ref_id 不能为空")
        # external 无库内可校验来源 —— 收下就等于绕过规则 3，宁可不收。
        if ekind == "external":
            raise MemoryValidationError(
                "evidence_kind='external'（外部文档 id）在本轮没有可校验的来源，一律拒绝；"
                f"可用的证据类型：{', '.join(k for k in EVIDENCE_KINDS if k != 'external')}")
        key = (ekind, ref_id)
        if key in seen:               # 表里 UNIQUE(experience_id, evidence_kind, ref_id)
            raise MemoryValidationError(f"证据重复：{ekind} {ref_id}")
        seen.add(key)
        cleaned.append({
            "evidence_kind": ekind,
            "ref_id": ref_id,
            "ref_note": ev.get("ref_note"),
            # 按真值归一：任何「看起来为真」的写法都算污染（1 / "true" / True）。
            # 防泄露是**不可申诉**的红线，宁可在畸形入参上朝「更不可见」的方向多传染一步，
            # 也不能因为调用方把 `true` 写成了 `1` 就静默放行。
            "holdout_tainted": bool(ev.get("holdout_tainted")),
        })

    # ── 规则 5：verified 必填 method / sample_size ────────────────────
    method = None if method in (None, "") else str(method).strip()
    sample_size = None if sample_size in (None, "") else sample_size
    if kind == "verified":
        if not method:
            raise MemoryValidationError("kind='verified' 必须提供 method（怎么验的）")
        if sample_size is None:
            raise MemoryValidationError("kind='verified' 必须提供 sample_size（样本笔数）")
        if isinstance(sample_size, bool) or not isinstance(sample_size, int):
            raise MemoryValidationError(f"sample_size 应是整数，收到 {type(sample_size).__name__}")
        if sample_size < 1:
            raise MemoryValidationError("sample_size 必须 ≥ 1")

    if sample_size is not None and not isinstance(sample_size, bool) and not isinstance(sample_size, int):
        raise MemoryValidationError(f"sample_size 应是整数，收到 {type(sample_size).__name__}")

    confidence_v = _clean_num(confidence, field="confidence")
    if confidence_v is not None and not (0.0 <= confidence_v <= 1.0):
        raise MemoryValidationError(f"confidence 应在 0~1 之间，收到 {confidence_v}")
    uncertainty_v = _clean_num(uncertainty, field="uncertainty")
    if uncertainty_v is not None and uncertainty_v < 0:
        raise MemoryValidationError(f"uncertainty 应 ≥ 0，收到 {uncertainty_v}")

    # ── 规则 2 / 8：status ────────────────────────────────────────────
    status = None if status in (None, "") else str(status).strip()
    if status is not None and status not in STATUSES:
        raise MemoryValidationError(
            f"status 必须是 {', '.join(STATUSES)} 之一，收到 {status!r}")
    if kind == "hypothesis":
        status = "待验证"                      # 规则 2 强制，入参传什么都不认
    elif supersedes:
        status = "已推翻"                      # 规则 8：推翻 = 追加一条「已推翻」
    elif status is None:
        status = "已确认"                      # fact / verified 默认即「已确认」

    supersedes = None if supersedes in (None, "") else str(supersedes).strip()

    # ── R5（`0042`）新增列 ────────────────────────────────────────────
    memory_layer = None if memory_layer in (None, "") else str(memory_layer).strip()
    if memory_layer is not None and memory_layer not in MEMORY_LAYERS:
        raise MemoryValidationError(
            f"memory_layer 必须是 {', '.join(MEMORY_LAYERS)} 之一或留空，收到 {memory_layer!r}")
    polarity = None if polarity in (None, "") else str(polarity).strip()
    if polarity is not None and polarity not in POLARITIES:
        raise MemoryValidationError(
            f"polarity 必须是 {', '.join(POLARITIES)} 之一或留空，收到 {polarity!r}")

    symbols_v = _clean_symbols(symbols)
    strategy_keys_v = _clean_str_list(strategy_keys, field="strategy_keys")
    regime_tags_v = _clean_str_list(regime_tags, field="regime_tags")
    regime_source = None if regime_source in (None, "") else str(regime_source).strip()

    importance_v = _clean_num(importance, field="importance")
    if importance_v is not None and not (0.0 <= importance_v <= 1.0):
        raise MemoryValidationError(f"importance 应在 0~1 之间，收到 {importance_v}")
    last_validated_dt = _parse_dt(last_validated_at, field="last_validated_at")
    duplicate_of = None if duplicate_of in (None, "") else str(duplicate_of).strip()

    # 规则 9（`01 §8.3`）：策略记忆必须来自真值 —— 有稳定版本键、且是验证结论。
    # 「策略记忆」是「以后该怎么买卖」，猜一条进来会让进化基于想象改参数（那是自进化的头号死法）。
    if memory_layer == "strategy":
        if not strategy_keys_v:
            raise MemoryValidationError(
                "memory_layer='strategy' 必须提供 strategy_keys（稳定版本键，不是显示名）")
        if kind != "verified":
            raise MemoryValidationError(
                "memory_layer='strategy' 的 kind 必须是 'verified'（策略记忆要来自回测 / 实盘真值）")

    return {
        "kind": kind,
        "statement": statement,
        "market": market,
        "status": status,
        "method": method,
        "sample_size": sample_size,
        "confidence": confidence_v,
        "uncertainty": uncertainty_v,
        "evidence": cleaned,
        "supersedes": supersedes,
        "memory_layer": memory_layer,
        "polarity": polarity,
        "symbols": symbols_v,
        "strategy_keys": strategy_keys_v,
        "regime_tags": regime_tags_v,
        "regime_source": regime_source,
        "importance": importance_v,
        "last_validated_at": last_validated_dt,
        "duplicate_of": duplicate_of,
    }


def resolve_source(caller: str, evidence: list[dict],
                   trade_sources: dict[str, str]) -> tuple[str, str]:
    """服务端决定 `source` / `created_by`（规则 7）。**入参里没有 source 这个位置。**

    | 情形 | source | created_by |
    |---|---|---|
    | 内网口令（fin-worker），普通结论 | `ai` | `ai` |
    | 内网口令，**证据全部是人工成交** | `human_mixed` | `human:trade` |
    | JWT（真人） | `human_mixed` | `user:<uuid>` |

    第二行是 **R3 新增**（`plan/R3.md` §一.1「AI 与人分开」）：复核工作流没有 JWT，
    而人工成交的「人味」是一条可以**由服务端从证据真值判定**的事实 ——
    调用方伪造不了，它只能引用库里真实存在的 `fin_trade` 行，而那一行的
    `source` 是 paper 写下的。所以「服务端说了算」这条没有被削弱，
    只是判据从「谁在调」多了「证据是谁的交易」。

    `evidence` 非空且**每一条**都是人工成交才算 —— 只要掺了一条 AI 证据或报告证据，
    就退回 `ai`（保守：宁可把人工经验记成 AI 的，也不把 AI 经验记成人的）。
    """
    if caller == "jwt":
        return CALLER_SOURCE["jwt"], "user"          # created_by 由调用点补 uuid
    if evidence and all(ev["evidence_kind"] == "trade"
                        and trade_sources.get(ev["ref_id"]) in HUMAN_TRADE_SOURCES
                        for ev in evidence):
        return "human_mixed", "human:trade"
    return CALLER_SOURCE["internal"], "ai"


# ── 内容指纹（口径见模块顶部的常量注释；**契约**，换口径要改版本号）──────────

def _hash_field(row: dict, name: str) -> str:
    value = row.get(name)
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def content_hash(row: dict) -> str:
    """一条经验的内容指纹（`kind|status|statement|applicability|valid_until|superseded_by`）。

    这六个字段是**可见性会波及的那些**：`status` 从「待验证」变「已确认」、
    `valid_until` 到期、`superseded_by` 指过去、正文被改写 —— 都会让它变。
    只冻 id 名单是看不出来的，所以冻结时一并冻这个。
    """
    raw = _HASH_SEP.join(_hash_field(row, f) for f in _HASH_FIELDS)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def content_hashes(rows: list[dict]) -> dict[str, str]:
    """按 `experience_id` 升序的 `{id: hash}`（顺序固定 → aggregate 可复现）。"""
    return {r["experience_id"]: content_hash(r)
            for r in sorted(rows, key=lambda r: r["experience_id"])}


def aggregate_hash(hashes: dict[str, str]) -> str:
    """把 `{id: hash}` 压成一个值：sha256(按 id 升序的 `"<id>=<hash>\\n"` 拼接)。"""
    joined = "".join(f"{k}={v}\n" for k, v in sorted(hashes.items()))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# ════════════════════════════════════════════════════════════════════════
# 二 · JSON 化（Decimal → float，datetime → ISO 字符串，同 store.py 口径）
# ════════════════════════════════════════════════════════════════════════
def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    """一行 DB dict → JSON 可序列化（`_jsonable` 逐字段），与 `store.py:48` 同口径。"""
    return {k: _jsonable(v) for k, v in row.items()}


# ════════════════════════════════════════════════════════════════════════
# 三 · 唯一写入口：append_evidence
# ════════════════════════════════════════════════════════════════════════

def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:24]


def _owned_project(cur, project_id: str, user_id: Optional[str]) -> dict:
    """取项目并校验归属。`user_id=None`（内网口令通道）只校验存在。

    跨用户 / 不存在一律 `LookupError`（路由 → 404）—— **不区分两者**，不泄露存在性。
    """
    cur.execute("SELECT project_id, user_id FROM fin_project WHERE project_id = %s", (project_id,))
    row = cur.fetchone()
    if not row:
        raise LookupError("项目不存在")
    if user_id is not None and str(row["user_id"]) != str(user_id):
        raise LookupError("项目不存在")
    return dict(row)


def _verify_refs(cur, evidence: list[dict]) -> dict[str, str]:
    """规则 3：逐条验引用真实存在（防「编一个引用」）。

    顺带把**交易证据的人机归因**读回来（`fin_trade.source`）—— 服务端据此决定这条经验
    记 `ai` 还是 `human_mixed`（`resolve_source`）。调用方伪造不了这一条：它只能引用
    **库里真实存在**的成交行，而那一行的 `source` 是 paper 写下的。

    返回 `{trade_id: source}`（只含 `trade` 类型的证据行）。
    """
    trade_sources: dict[str, str] = {}
    for ev in evidence:
        kind = ev["evidence_kind"]
        if kind == "trade":
            cur.execute("SELECT source FROM fin_trade WHERE trade_id = %s", (ev["ref_id"],))
            row = cur.fetchone()
            if row is None:
                raise MemoryValidationError(f"证据引用不存在：trade {ev['ref_id']}")
            trade_sources[ev["ref_id"]] = str(row["source"])
            continue
        sql = REF_TABLE_SQL.get(kind)
        if sql is None:      # 不可达（validate_append 已拦 external）—— 保险起见
            raise MemoryValidationError(
                f"证据类型 {kind!r} 无法在库内校验，拒绝")
        cur.execute(sql, (ev["ref_id"],))
        if cur.fetchone() is None:
            raise MemoryValidationError(f"证据引用不存在：{kind} {ev['ref_id']}")
    return trade_sources


def append_evidence(
    *,
    caller: str,
    project_id: str,
    kind: Any,
    statement: Any,
    evidence: Any,
    user_id: Optional[str] = None,
    market: Any = None,
    status: Any = None,
    applicability: Any = None,
    invalidation_condition: Any = None,
    method: Any = None,
    sample_size: Any = None,
    uncertainty: Any = None,
    confidence: Any = None,
    as_of: Any = None,
    valid_from: Any = None,
    valid_until: Any = None,
    memory_snapshot_id: Optional[str] = None,
    supersedes: Any = None,
    memory_layer: Any = None,
    polarity: Any = None,
    symbols: Any = None,
    strategy_keys: Any = None,
    regime_tags: Any = None,
    regime_source: Any = None,
    importance: Any = None,
    last_validated_at: Any = None,
    duplicate_of: Any = None,
    conn=None,
) -> dict:
    """**唯一写入口**。八条硬校验（见模块文档），任一不过 → `MemoryValidationError`（→400）。

    `caller` ∈ `{'internal','jwt'}` 决定 `source` / `created_by`（规则 7）——
    **入参里没有 source 这个位置**，调用方（路由）想把请求体里的 source 塞进来也无处可塞。

    `conn` 给了就用它（测试复用同一连接），否则自己开一个。
    """
    # R4 · 总开关在**服务端**：`FIN_MEMORY_ENABLED=0` ⇒ 写入口直接拒绝（→503）。
    # 这一条排在所有校验之前 —— 「关」就是关，与请求长什么样无关。
    if not switches.memory_enabled():
        raise MemoryDisabledError(
            "经验库未启用（FIN_MEMORY_ENABLED=0）：本部署当前不接受经验写入")

    if caller not in CALLER_SOURCE:
        raise MemoryValidationError(f"未知调用方通道：{caller!r}")
    if caller == "jwt" and not user_id:
        raise MemoryValidationError("JWT 通道缺少用户身份")

    # 服务端硬校验（纯函数，先跑 —— 不做任何 IO 的检查不该先碰库）
    fields = validate_append(kind=kind, statement=statement, evidence=evidence,
                             market=market, status=status, method=method,
                             sample_size=sample_size, confidence=confidence,
                             uncertainty=uncertainty, supersedes=supersedes,
                             memory_layer=memory_layer, polarity=polarity,
                             symbols=symbols, strategy_keys=strategy_keys,
                             regime_tags=regime_tags, regime_source=regime_source,
                             importance=importance, last_validated_at=last_validated_at,
                             duplicate_of=duplicate_of)

    as_of_dt = _parse_dt(as_of, field="as_of")
    if as_of_dt is None:
        as_of_dt = datetime.now(SHANGHAI)
    valid_from_dt = _parse_dt(valid_from, field="valid_from")
    valid_until_dt = _parse_dt(valid_until, field="valid_until")
    if valid_from_dt and valid_until_dt and valid_until_dt <= valid_from_dt:
        raise MemoryValidationError("valid_until 必须晚于 valid_from")

    # 规则 4：任一证据被保底测试集污染 ⇒ 整条经验传染为 holdout_only，不可申诉。
    tainted = any(ev["holdout_tainted"] for ev in fields["evidence"])
    exposure_scope = "holdout_only" if tainted else "searchable"

    experience_id = _new_id("exp_")
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _owned_project(cur, project_id, user_id)          # 规则 4（归属；跨用户 404）
            trade_sources = _verify_refs(cur, fields["evidence"])   # 规则 3（顺带读回交易归因）
            # 规则 7：`source` 由服务端定 —— 判据含「证据是不是全为人工成交」（见 resolve_source）。
            source, created_by = resolve_source(caller, fields["evidence"], trade_sources)
            if caller == "jwt":
                created_by = f"user:{user_id}"

            superseded_row = None
            if fields["supersedes"]:
                # 规则 8：推翻 = 追加 + 指向；原条目**不删**。
                cur.execute(
                    "SELECT experience_id, project_id, superseded_by "
                    "FROM fin_experience WHERE experience_id = %s",
                    (fields["supersedes"],))
                superseded_row = cur.fetchone()
                if not superseded_row:
                    raise MemoryValidationError(
                        f"要推翻的经验不存在：{fields['supersedes']}")
                if superseded_row["project_id"] != project_id:
                    raise MemoryValidationError("只能推翻同一项目下的经验")
                if superseded_row["superseded_by"]:
                    raise MemoryValidationError(
                        f"该经验已被 {superseded_row['superseded_by']} 推翻，不能再次推翻")

            # 判重指向（`duplicate_of`）：与 `supersedes` 同一道校验 —— 引用必须真实存在、
            # 且同一项目。**不删**（追加不删，与 `superseded_by` 同精神）。
            if fields["duplicate_of"]:
                cur.execute(
                    "SELECT project_id FROM fin_experience WHERE experience_id = %s",
                    (fields["duplicate_of"],))
                dup_row = cur.fetchone()
                if not dup_row:
                    raise MemoryValidationError(
                        f"判重指向的经验不存在：{fields['duplicate_of']}")
                if dup_row["project_id"] != project_id:
                    raise MemoryValidationError("只能指向同一项目下的经验")

            cur.execute(
                """
                INSERT INTO fin_experience (
                  experience_id, project_id, market, kind, status, source, statement,
                  applicability, invalidation_condition,
                  method, sample_size, uncertainty, confidence,
                  as_of, valid_from, valid_until,
                  exposure_scope, holdout_tainted,
                  memory_snapshot_id, created_by,
                  memory_layer, polarity, symbols, strategy_keys,
                  regime_tags, regime_source, importance, last_validated_at, duplicate_of
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s,
                  %s, %s,
                  %s, %s, %s, %s,
                  %s, %s, %s,
                  %s, %s,
                  %s, %s,
                  %s, %s, %s, %s,
                  %s, %s, %s, %s, %s
                )
                """,
                (
                    experience_id, project_id, fields["market"], fields["kind"],
                    fields["status"], source, fields["statement"],
                    _opt_str(applicability), _opt_str(invalidation_condition),
                    fields["method"], fields["sample_size"],
                    fields["uncertainty"], fields["confidence"],
                    as_of_dt, valid_from_dt, valid_until_dt,
                    exposure_scope, tainted,
                    memory_snapshot_id, created_by,
                    fields["memory_layer"], fields["polarity"], fields["symbols"],
                    fields["strategy_keys"], fields["regime_tags"], fields["regime_source"],
                    fields["importance"], fields["last_validated_at"], fields["duplicate_of"],
                ),
            )

            for ev in fields["evidence"]:
                cur.execute(
                    """
                    INSERT INTO fin_experience_evidence (
                      evidence_id, experience_id, evidence_kind, ref_id, ref_note, holdout_tainted
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (_new_id("evd_"), experience_id, ev["evidence_kind"], ev["ref_id"],
                     _opt_str(ev.get("ref_note")), ev["holdout_tainted"]),
                )

            if superseded_row:
                cur.execute(
                    "UPDATE fin_experience SET superseded_by = %s WHERE experience_id = %s",
                    (experience_id, superseded_row["experience_id"]))

            cur.execute(
                "SELECT * FROM fin_experience WHERE experience_id = %s", (experience_id,))
            row = dict(cur.fetchone())
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if own:
            conn.close()

    row["holdout_tainted"] = bool(row.get("holdout_tainted"))
    row["exposure_scope"] = row.get("exposure_scope") or exposure_scope
    return _row_to_dict(row)


# ── 小工具 ───────────────────────────────────────────────────────────────
def _opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# ════════════════════════════════════════════════════════════════════════
# 四 · 唯一读入口：query（含冻结）
# ════════════════════════════════════════════════════════════════════════

def _basis_now() -> datetime:
    return datetime.now(SHANGHAI)


def query(
    *,
    project_id: str,
    caller: str = "internal",
    user_id: Optional[str] = None,
    market: Optional[str] = None,
    kind: Optional[str] = None,
    status: Optional[str] = None,
    for_decision: bool = False,
    as_of: Any = None,
    freeze: bool = False,
    purpose: str = "decision",
    trade_date: Optional[str] = None,
    point: Optional[str] = None,
    memory_snapshot_id: Optional[str] = None,
    conn=None,
) -> dict:
    """**唯一读入口**。四条硬过滤（见模块文档）焊死在 SQL 里，签名里没有能放开它们的参数。

    `freeze=True` 时顺带把这次命中的 id 集合冻结成一条 `fin_memory_snapshot`，
    返回它的 `memory_snapshot_id` —— 回放走 `GET /snapshots/{id}`（按 id 取，不重跑查询）。

    ⚠️ 本函数**没有任何** `include_holdout` / `debug` / `admin` 参数，以后也不要加。
    """
    # R4 · 总开关在**服务端**：`FIN_MEMORY_ENABLED=0` ⇒ 返回**空集合**（200，不是报错）。
    # 语义照 `03 §2` —— 关掉的是「查得到经验」这件事本身，调用方按「没有经验」继续。
    # `memory_disabled` 是给自己看的自描述位（前端不依赖它）。
    if not switches.memory_enabled():
        return {
            "memory_snapshot_id": None,
            "as_of_basis": _basis_now().isoformat(),
            "items": [],
            "memory_disabled": True,
        }

    if caller not in CALLER_SOURCE:
        raise MemoryValidationError(f"未知调用方通道：{caller!r}")

    basis = _parse_dt(as_of, field="as_of") or _basis_now()
    as_of_basis = basis.isoformat()

    market = None if market in (None, "") else str(market).strip()
    if market is not None and market not in MARKETS:
        raise MemoryValidationError(f"未知市场：{market!r}（可选 {', '.join(MARKETS)}）")
    kind = None if kind in (None, "") else str(kind).strip()
    if kind is not None and kind not in KINDS:
        raise MemoryValidationError(f"未知 kind：{kind!r}")
    status = None if status in (None, "") else str(status).strip()
    if status is not None and status not in STATUSES:
        raise MemoryValidationError(f"未知 status：{status!r}")
    if purpose not in PURPOSES:
        raise MemoryValidationError(f"未知 purpose：{purpose!r}（可选 {', '.join(PURPOSES)}）")

    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _owned_project(cur, project_id, user_id)      # 规则 4（权限 / 跨用户 404）

            where = [
                "e.project_id = %s",
                # 规则 1：防评估泄露，**零开关**。这两个条件是常量，没有任何参数能关掉。
                "e.exposure_scope = 'searchable'",
                "e.holdout_tainted = false",
                # 规则 2：时间边界 —— 只看基准时刻之前已形成的经验。
                "e.as_of <= %s",
            ]
            args: list[Any] = [project_id, basis]
            if market is not None:
                # 表注释：market 为 NULL = 跨市场结论 → 它适用于每个市场，一并返回。
                where.append("(e.market = %s OR e.market IS NULL)")
                args.append(market)
            if kind is not None:
                where.append("e.kind = %s")
                args.append(kind)
            if status is not None:
                where.append("e.status = %s")
                args.append(status)
            if for_decision:
                # 规则 3：决策口径更严 —— 假设不下单，是**服务端不给**。
                where.append("e.kind IN ('fact','verified')")
                where.append("e.status = '已确认'")
                where.append("(e.valid_until IS NULL OR e.valid_until > now())")

            cur.execute(
                "SELECT e.*, "
                "  (SELECT count(*) FROM fin_experience_evidence v "
                "    WHERE v.experience_id = e.experience_id) AS evidence_count "
                "FROM fin_experience e WHERE " + " AND ".join(where) +
                " ORDER BY e.as_of DESC, e.experience_id ASC",
                tuple(args),
            )
            rows = [dict(r) for r in cur.fetchall()]

            ids = [r["experience_id"] for r in rows]
            ev_map: dict[str, list[dict]] = {i: [] for i in ids}
            if ids:
                cur.execute(
                    "SELECT experience_id, evidence_kind, ref_id "
                    "FROM fin_experience_evidence WHERE experience_id = ANY(%s) "
                    "ORDER BY experience_id, evidence_kind, ref_id",
                    (ids,),
                )
                for ev in cur.fetchall():
                    ev_map[ev["experience_id"]].append(
                        {"evidence_kind": ev["evidence_kind"], "ref_id": ev["ref_id"]})

            now = datetime.now(timezone.utc)
            items = [_item(r, ev_map.get(r["experience_id"], []), now) for r in rows]

            snap_id = None
            if freeze:
                snap_id = _new_id("msnap_")
                # R3 §3b：冻结**必须带内容哈希/版本** —— 只冻 id 名单有个洞：
                # 同一个 id 名单在两天后可能已经「不是那一批内容」（status 变已确认、
                # valid_until 到期、superseded_by 指过去）。指纹一起冻进去，重放时
                # 才验得出「后来改变过可见性」。
                hashes = content_hashes(rows)
                filters = {
                    "for_decision": bool(for_decision),
                    "as_of_basis": as_of_basis,
                    "market": market, "kind": kind, "status": status,
                    "exposure_scope": "searchable",   # 冻结的永远是搜索路径
                }
                cur.execute(
                    """
                    INSERT INTO fin_memory_snapshot (
                      memory_snapshot_id, project_id, market, trade_date, point,
                      purpose, experience_ids, query_filter
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (snap_id, project_id, market, trade_date, point, purpose, ids,
                     psycopg2.extras.Json({
                         # `filters` 是 §3b 要求的嵌套形态；同一组键**同时平铺**在顶层，
                         # 是为了兼容 R2 已发布的读法（快照的 JSONB 是自描述的，多一组键无害）。
                         "filters": filters,
                         **filters,
                         "content_hash_version": CONTENT_HASH_VERSION,
                         "content_hashes": hashes,
                         "aggregate_hash": aggregate_hash(hashes),
                     })),
                )
                # 规则 3 末句：形成的经验记下「形成于哪一版快照」（只回填过去的）
                if ids:
                    cur.execute(
                        "UPDATE fin_experience SET memory_snapshot_id = %s "
                        "WHERE experience_id = ANY(%s) AND memory_snapshot_id IS NULL",
                        (snap_id, ids))
        conn.commit()
        return {
            "memory_snapshot_id": snap_id,
            "as_of_basis": as_of_basis,
            "items": items,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        if own:
            conn.close()


def _item(row: dict, evidence: list[dict], now: datetime) -> dict:
    """一行经验 → 响应里的一个 item（照方案 §3.3 的字段）。"""
    valid_until = row.get("valid_until")
    needs_recheck = bool(valid_until is not None and valid_until <= now)
    return {
        "experience_id": row["experience_id"],
        "kind": row["kind"],
        "status": row["status"],
        "market": row.get("market"),
        "statement": row["statement"],
        "applicability": row.get("applicability"),
        "invalidation_condition": row.get("invalidation_condition"),
        "method": row.get("method"),
        "sample_size": row.get("sample_size"),
        "uncertainty": _jsonable(row.get("uncertainty")),
        "confidence": _jsonable(row.get("confidence")),
        "as_of": _jsonable(row.get("as_of")),
        "valid_from": _jsonable(row.get("valid_from")),
        "valid_until": _jsonable(valid_until),
        "source": row["source"],
        "superseded_by": row.get("superseded_by"),
        # ── R5（`0042`）结构化标签：消费方（`memory_gate` / `R6` 聚合）读的是这些列，
        # 不再从 `applicability` 自由文本里猜。旧行为 NULL（**不回填**，见 `0042` 文件头）。
        "memory_layer": row.get("memory_layer"),
        "polarity": row.get("polarity"),
        "symbols": row.get("symbols"),
        "strategy_keys": row.get("strategy_keys"),
        "regime_tags": row.get("regime_tags"),
        "regime_source": row.get("regime_source"),
        "importance": _jsonable(row.get("importance")),
        "last_validated_at": _jsonable(row.get("last_validated_at")),
        "duplicate_of": row.get("duplicate_of"),
        "evidence_count": len(evidence),
        "evidence": evidence,
        # 派生标志，不是状态值（方案 §1.4：状态只有三种）
        "needs_recheck": needs_recheck,
    }


# ════════════════════════════════════════════════════════════════════════
# 五 · 快照回放：按 id 取冻结集合，**不重跑查询**
# ════════════════════════════════════════════════════════════════════════

def _content_drift(query_filter: dict, rows: list[dict]) -> dict:
    """冻结时的内容指纹 vs 此刻的内容指纹（R3 §3b 的可执行证明）。

    `frozen_aggregate_hash` 是**当时冻进去**的那个值，永远不变；
    `current_aggregate_hash` 是拿此刻的行重算的；两者不等 **或** 有 id 缺失
    ⇒ `match=false`，`drifted` / `missing` 点出具体是哪几条。

    老快照（R2 冻的，`query_filter` 里没有 `content_hashes`）→ `hashes_frozen=false`，
    **不假装它匹配**（那时确实没冻指纹）。
    """
    frozen = dict(query_filter.get("content_hashes") or {})
    current = content_hashes(rows)
    drifted = sorted(k for k in frozen if k in current and current[k] != frozen[k])
    missing = sorted(set(frozen) - set(current))
    frozen_agg = query_filter.get("aggregate_hash")
    cur_agg = aggregate_hash(current) if current else None
    return {
        "content_hash_version": query_filter.get("content_hash_version"),
        "hashes_frozen": bool(frozen),
        "frozen_aggregate_hash": frozen_agg,
        "current_aggregate_hash": cur_agg,
        "drifted": drifted,
        "missing": missing,
        "match": bool(frozen) and not drifted and not missing and cur_agg == frozen_agg,
    }

def get_snapshot(*, memory_snapshot_id: str, user_id: Optional[str] = None,
                 conn=None) -> dict:
    """取一份冻结快照（回放与审计用）。

    **不重跑查询**：按 `experience_ids` 逐 id 取，后来才写的经验进不来 —— 这就是
    「历史回放不得读后来才形成的经验」的技术兑现（`01方案 §8.4`）。

    JWT 通道按快照的 `project_id` 校验归属，跨用户 → `LookupError`（→404）。
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM fin_memory_snapshot WHERE memory_snapshot_id = %s",
                        (memory_snapshot_id,))
            snap = cur.fetchone()
            if not snap:
                raise LookupError("快照不存在")
            snap = dict(snap)
            if user_id is not None:
                _owned_project(cur, snap["project_id"], user_id)

            ids = list(snap.get("experience_ids") or [])
            items = []
            by_id: dict[str, dict] = {}
            if ids:
                cur.execute(
                    "SELECT * FROM fin_experience WHERE experience_id = ANY(%s)", (ids,))
                by_id = {r["experience_id"]: dict(r) for r in cur.fetchall()}
                cur.execute(
                    "SELECT experience_id, evidence_kind, ref_id "
                    "FROM fin_experience_evidence WHERE experience_id = ANY(%s) "
                    "ORDER BY experience_id, evidence_kind, ref_id",
                    (ids,),
                )
                ev_map: dict[str, list[dict]] = {}
                for ev in cur.fetchall():
                    ev_map.setdefault(ev["experience_id"], []).append(
                        {"evidence_kind": ev["evidence_kind"], "ref_id": ev["ref_id"]})
                now = datetime.now(timezone.utc)
                # 按冻结时的先后还原，不按 as_of 排（回放的是那一版集合）
                items = [_item(by_id[i], ev_map.get(i, []), now) for i in ids if i in by_id]
            # R3 §3b：把「冻结时的内容指纹」与「此刻的内容指纹」摆在一起 ——
            # 后来改过可见性（status / valid_until / superseded_by / 正文）时，
            # `frozen_aggregate_hash` **不变**（它就是当时冻的那个），而 `drifted`
            # 会点出被改动的 id。这就是「只存 id 名单」看不出来的那件事。
            drift = _content_drift(dict(snap.get("query_filter") or {}),
                                   [by_id[i] for i in ids if i in by_id])
        conn.rollback()      # 只读
        return {
            "memory_snapshot_id": snap["memory_snapshot_id"],
            "project_id": snap["project_id"],
            "market": snap.get("market"),
            "trade_date": _jsonable(snap.get("trade_date")),
            "point": snap.get("point"),
            "purpose": snap["purpose"],
            "experience_ids": ids,
            "query_filter": _jsonable(snap.get("query_filter")),
            "content_drift": drift,
            "created_at": _jsonable(snap.get("created_at")),
            "items": items,
        }
    finally:
        if own:
            conn.close()


# ════════════════════════════════════════════════════════════════════════
# 六 · 筛选下拉的取值（R9 · 成长页）· **只读**
# ════════════════════════════════════════════════════════════════════════
#
# 成长页要求「筛选值全部来自后端、前端不写死任何枚举」（`plan/R9.md` §二.1）。
# 所以这里把「这个项目里有哪些可用的筛选值」做成一个只读接口的取值来源：
#   · 闭集维度（kind / status）**列全部取值**（含计数 0 的）—— 界面上「状态只有三种」
#     这句话才有一个可被机器验证的来源（前端不许自己拼这三种）；
#   · 开放维度（market / regime / polarity / memory_layer / symbols）**只列数据里出现过的**，
#     换项目后选项自然跟着变（空出来的维度不占位置）。
#
# 计数口径与 `query()` 的硬过滤**逐字一致**：`exposure_scope='searchable'`
#   AND `holdout_tainted=false`（防评估泄露，零开关），另加 `as_of <= now()`。
# **这不是一条新的读取路径** —— 它只做聚合，返回的是「有哪些值」，不返回任何经验正文；
# 经验正文的唯一读入口仍然是 `query()`。

def _opt(value: str, label: str, count: int) -> dict:
    return {"value": value, "label": label, "count": int(count)}


def filter_options(*, project_id: str, user_id: Optional[str] = None, conn=None) -> dict:
    """某个项目下**可用的筛选取值**（含计数）。JWT 通道按项目归属校验（跨用户 → `LookupError`）。"""
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _owned_project(cur, project_id, user_id)      # 规则 4（权限 / 跨用户 404）
            cur.execute(
                "SELECT kind, status, market, polarity, memory_layer, symbols, regime_tags "
                "FROM fin_experience "
                "WHERE project_id = %s AND exposure_scope = 'searchable' "
                "  AND holdout_tainted = false AND as_of <= now()",
                (project_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]
        conn.rollback()      # 只读
    finally:
        if own:
            conn.close()

    def tally(field: str, flat: bool = False) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in rows:
            v = r.get(field)
            if v is None:
                continue
            if flat:
                for x in v:
                    out[str(x)] = out.get(str(x), 0) + 1
            else:
                out[str(v)] = out.get(str(v), 0) + 1
        return out

    kind_c, status_c = tally("kind"), tally("status")
    market_c, polarity_c, layer_c = tally("market"), tally("polarity"), tally("memory_layer")
    regime_c = tally("regime_tags", flat=True)
    symbol_c = tally("symbols", flat=True)

    ordered = lambda present: {k: present[k] for k in sorted(present)}  # noqa: E731
    return {
        "project_id": project_id,
        # 闭集：全部取值（含 0）——「只有三种状态」的机器可验来源
        "kinds": [_opt(k, KIND_LABELS.get(k, k), kind_c.get(k, 0)) for k in KINDS],
        "statuses": [_opt(s, STATUS_LABELS.get(s, s), status_c.get(s, 0)) for s in STATUSES],
        # 开放集：只列数据里出现过的，按固定顺序排（market 用 MARKETS 的顺序，其余字典序）
        "markets": [_opt(m, MARKET_LABELS.get(m, m), market_c[m])
                    for m in MARKETS if m in market_c],
        "regimes": [_opt(g, REGIME_LABELS.get(g, g), regime_c[g])
                    for g in list(REGIME_LABELS) if g in regime_c],
        "polarities": [_opt(p, POLARITY_LABELS.get(p, p), polarity_c[p])
                       for p in POLARITIES if p in polarity_c],
        "memory_layers": [_opt(l, MEMORY_LAYER_LABELS.get(l, l), layer_c[l])
                          for l in MEMORY_LAYERS if l in layer_c],
        "symbols": [_opt(s, s, symbol_c[s]) for s in ordered(symbol_c)],
        # 计数为 0 的闭集取值也返回，界面据此**照实显示「这条筛不出东西」**，
        # 而不是悄悄不给这个选项（那会让用户以为系统里根本没有这种状态）。
        "counted_rows": len(rows),
    }

