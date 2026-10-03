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

## 权限：谁可以碰这三张表

api 连接用的是库属主身份（见 `0041` 文件头那三条理由），能力来自**连接身份**。
`fin_paper_rw` 对这三张表**一行授权都没有**（R1 验收项）—— 「唯一入口」最硬的收口
就是账本角色连 `SELECT` 都没有。

fin-worker **不碰这三张表**：它经 `HunterApiClient` 走内网口令调用本模块的路由，
全程不连数据库（`apps/fin-worker/tests/test_no_ledger_access.py` 的守护口径已扩展到经验三表）。
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Optional

import psycopg2
import psycopg2.extras

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://hunter:hunter@localhost:5432/hunter")

SHANGHAI = timezone(timedelta(hours=8))

# ── 枚举（与 0041 的 CHECK 逐字一致，改一处必须改另一处）────────────────────
KINDS = ("fact", "hypothesis", "verified")
STATUSES = ("待验证", "已确认", "已推翻")
SOURCES = ("ai", "human_mixed")
EVIDENCE_KINDS = ("trade", "report", "fact", "snapshot", "external")
PURPOSES = ("decision", "review", "holdout")
MARKETS = ("CN_A", "HK", "US")

# 有对应表、可以「验在不在」的四类引用（规则 3）。`external` **故意不在**这里 —— 见模块文档。
REF_TABLE_SQL = {
    "trade": "SELECT 1 FROM fin_trade WHERE trade_id = %s",
    "report": "SELECT 1 FROM fin_report WHERE report_id = %s",
    "fact": "SELECT 1 FROM fin_report_fact WHERE report_id || ':' || metric_key = %s",
    "snapshot": "SELECT 1 FROM fin_snapshot WHERE snapshot_id = %s",
}

# 调用方通道 → 强制 source / created_by（规则 7）。**入参里传什么都没用**。
CALLER_SOURCE = {"internal": "ai", "jwt": "human_mixed"}

# 规则 6：statement 里出现阿拉伯数字（半角或全角）即拒。
_ARABIC_DIGIT_RE = re.compile(r"[0-9０-９]")


class MemoryValidationError(ValueError):
    """任一硬校验不过 —— 路由据此回 **400**（绝不静默降级）。"""


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
    }


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


def _verify_refs(cur, evidence: list[dict]) -> None:
    """规则 3：逐条验引用真实存在（防「编一个引用」）。"""
    for ev in evidence:
        sql = REF_TABLE_SQL.get(ev["evidence_kind"])
        if sql is None:      # 不可达（validate_append 已拦 external）—— 保险起见
            raise MemoryValidationError(
                f"证据类型 {ev['evidence_kind']!r} 无法在库内校验，拒绝")
        cur.execute(sql, (ev["ref_id"],))
        if cur.fetchone() is None:
            raise MemoryValidationError(
                f"证据引用不存在：{ev['evidence_kind']} {ev['ref_id']}")


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
    conn=None,
) -> dict:
    """**唯一写入口**。八条硬校验（见模块文档），任一不过 → `MemoryValidationError`（→400）。

    `caller` ∈ `{'internal','jwt'}` 决定 `source` / `created_by`（规则 7）——
    **入参里没有 source 这个位置**，调用方（路由）想把请求体里的 source 塞进来也无处可塞。

    `conn` 给了就用它（测试复用同一连接），否则自己开一个。
    """
    if caller not in CALLER_SOURCE:
        raise MemoryValidationError(f"未知调用方通道：{caller!r}")
    source = CALLER_SOURCE[caller]
    created_by = "ai" if caller == "internal" else f"user:{user_id}"
    if caller == "jwt" and not user_id:
        raise MemoryValidationError("JWT 通道缺少用户身份")

    # 服务端硬校验（纯函数，先跑 —— 不做任何 IO 的检查不该先碰库）
    fields = validate_append(kind=kind, statement=statement, evidence=evidence,
                             market=market, status=status, method=method,
                             sample_size=sample_size, confidence=confidence,
                             uncertainty=uncertainty, supersedes=supersedes)

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
            _verify_refs(cur, fields["evidence"])             # 规则 3

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

            cur.execute(
                """
                INSERT INTO fin_experience (
                  experience_id, project_id, market, kind, status, source, statement,
                  applicability, invalidation_condition,
                  method, sample_size, uncertainty, confidence,
                  as_of, valid_from, valid_until,
                  exposure_scope, holdout_tainted,
                  memory_snapshot_id, created_by
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s,
                  %s, %s,
                  %s, %s, %s, %s,
                  %s, %s, %s,
                  %s, %s,
                  %s, %s
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
                cur.execute(
                    """
                    INSERT INTO fin_memory_snapshot (
                      memory_snapshot_id, project_id, market, trade_date, point,
                      purpose, experience_ids, query_filter
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (snap_id, project_id, market, trade_date, point, purpose, ids,
                     psycopg2.extras.Json({
                         "for_decision": bool(for_decision),
                         "as_of_basis": as_of_basis,
                         "kind": kind, "status": status,
                         "exposure_scope": "searchable",   # 冻结的永远是搜索路径
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
        "evidence_count": len(evidence),
        "evidence": evidence,
        # 派生标志，不是状态值（方案 §1.4：状态只有三种）
        "needs_recheck": needs_recheck,
    }


# ════════════════════════════════════════════════════════════════════════
# 五 · 快照回放：按 id 取冻结集合，**不重跑查询**
# ════════════════════════════════════════════════════════════════════════

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
            "created_at": _jsonable(snap.get("created_at")),
            "items": items,
        }
    finally:
        if own:
            conn.close()
