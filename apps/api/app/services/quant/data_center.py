"""数据中心 · 范围解析 / 概览 / 预估。

方案见 doc/开源hunter-community/01详细工作目录/11量化策略/
      22_20260822_数据中心_技术方案.md

## 这个模块只做"算账",不下载

下载任务在 data_job.py(第 3 步)。这里负责回答三个问题:

    · 现在库里有什么          → overview()
    · 我能选哪些范围          → scopes()
    · 选了之后要多久、多大    → estimate()

预估要准,否则用户对着"约 9 分钟"等了 40 分钟,下次就不敢点了。
所以下面所有速率和体积都是**实测值**,不是拍的 —— 见各常量的注释。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path

from app.services.database import get_conn

log = logging.getLogger(__name__)


# ── 实测速率 ────────────────────────────────────────────────
# 日线:800 只实测 1446 秒 → 1.81 秒/只(含 0.15 秒限速)
RATE_KLINE_SEC = 1.81
# 财报:pe_inv 10 只实测 86 秒 → 8.6 秒/只(AKShare 财务接口限流)
RATE_FIN_SEC = 8.6

# ── 实测体积(MB / 只)────────────────────────────────────
# klines       51 MB / 805 只 / 约 14 个月 → 0.055 MB/只/年
# factor_value 89 MB / 801 只 / 约 1 年    → 0.105 MB/只/年
#
# **因子表比 K 线表还大**,这一点容易漏:原型第一版只算了 K 线,
# 磁盘预估少了一半多。因子是 8 个 × 每个调仓日一行,行数比日线还多。
MB_KLINE_PER_YEAR = 0.055
MB_FACTOR_PER_YEAR = 0.105
# 财报窄表:实测 10 只 2680 行 592 kB → 268 行/只 · 221 字节/行 · 0.059 MB/只
MB_FIN_METRIC = 0.06
# 财报原始归档:实测 6 只 120 行 256 kB → 0.042 MB/只
#
# 比方案里估的 0.5 MB **小 12 倍**。原因:最终只归档「财务指标」一张表
# (86 列),而不是四张表(资产负债 319 + 利润 203 + 现金流 254)——
# 实测发现一个接口就把算好的比率全给了,三大报表根本没拉。
MB_FIN_RAW = 0.045

_BASELINE = Path(__file__).resolve().parents[3] / "data" / "stocks_catalog_baseline.json"

# 一级行业 —— 归并成 7 个,不直接用东财的 80+ 板块名(平铺给用户选反而更难挑)
L1_ORDER = ["科技", "医药", "消费", "新能源", "金融", "制造", "资源"]


# ═══════════════════════════════════════════════════════════
# 范围解析
# ═══════════════════════════════════════════════════════════

def _all_a_codes() -> list[str]:
    """全 A 股清单 —— 读仓库自带的 baseline JSON,**不联网**。

    这份清单随代码分发(5534 只),`agents/sentinel/stock_search.py`
    早就在用它做兜底。所以"全 A 股"这个选项是秒级的,
    真正花时间的是那 5400 只的日线下载。
    """
    try:
        d = json.loads(_BASELINE.read_text(encoding="utf-8"))
    except Exception as e:                                    # noqa: BLE001
        log.warning("[data_center] 读全A清单失败: %s", e)
        return []
    items = d if isinstance(d, list) else (d.get("items") or d.get("stocks") or [])
    return [str(x.get("code")).zfill(6) for x in items if x.get("code")]


def _index_codes(index_code: str) -> list[str]:
    from app.services.quant import universe as uv
    try:
        return uv.query_current(index_code)
    except Exception as e:                                    # noqa: BLE001
        log.warning("[data_center] 取 %s 成分股失败: %s", index_code, e)
        return []


def _industry_codes(l2_list: list[str]) -> list[str]:
    if not l2_list:
        return []
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT DISTINCT code FROM stock_industry WHERE l2 = ANY(%s)",
                    (l2_list,))
        return [r[0] for r in cur.fetchall()]
    finally:
        cur.close(); conn.close()


def _watchlist_codes(user_id: str | None) -> list[str]:
    if not user_id:
        return []
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT code FROM stocks WHERE user_id=%s AND enabled AND market='A'",
                    (str(user_id),))
        return [r[0] for r in cur.fetchall()]
    finally:
        cur.close(); conn.close()


_INDEX_OF = {"hs300": "000300", "zz500": "000905", "zz1000": "000852"}


def resolve_scope(scope: dict, user_id: str | None = None) -> tuple[list[str], str]:
    """把前端传的 scope 解析成股票代码列表。

    返回 (codes, 说明)。**拿不到就返回空 + 说明为什么** ——
    静默返回空的话,用户看到"0 只"完全不知道是自己没选行业、
    还是行业表没 seed。
    """
    kind = (scope or {}).get("kind") or "hs300"

    if kind in _INDEX_OF:
        codes = _index_codes(_INDEX_OF[kind])
        if not codes:
            return [], f"{kind} 的成分股还没同步 —— 下载时会自动拉一次"
        return codes, ""

    if kind == "industry":
        l2 = (scope or {}).get("industries") or []
        if not l2:
            return [], "还没选行业"
        codes = _industry_codes(l2)
        if not codes:
            return [], "行业分类表还是空的 —— 下载时会自动同步一次"
        return codes, ""

    if kind == "watchlist":
        codes = _watchlist_codes(user_id)
        if not codes:
            return [], "「我的自选」是空的 —— 先到「自选」页加几只股票"
        return codes, ""

    if kind == "all_a":
        codes = _all_a_codes()
        return codes, ("" if codes else "全A清单读取失败")

    if kind == "manual":
        raw = (scope or {}).get("codes") or []
        codes = [str(c).strip().zfill(6) for c in raw if str(c).strip()]
        return codes, ("" if codes else "还没填股票代码")

    return [], f"不认识的范围「{kind}」"


# ═══════════════════════════════════════════════════════════
# 概览
# ═══════════════════════════════════════════════════════════

def overview() -> dict:
    """当前数据概览 —— 数据页顶部那一排。"""
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""SELECT count(DISTINCT code), min(covered_from), max(covered_to)
                         FROM data_coverage WHERE data_type='kline'""")
        k_n, k_from, k_to = cur.fetchone() or (0, None, None)
        cur.execute("SELECT count(DISTINCT code) FROM data_coverage WHERE data_type='financial'")
        f_n = (cur.fetchone() or [0])[0]
        cur.execute("SELECT count(DISTINCT factor_key) FROM factor_value")
        fk = (cur.fetchone() or [0])[0]
        # 磁盘:量化相关的几张表加起来
        cur.execute("""SELECT coalesce(sum(pg_total_relation_size(c.oid)), 0)
                         FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname='public' AND c.relname = ANY(%s)""",
                    (["klines", "factor_value", "financial_metric",
                      "financial_raw", "index_component"],))
        disk_mb = round(((cur.fetchone() or [0])[0] or 0) / 1024 / 1024, 1)
    finally:
        cur.close(); conn.close()

    from app.services.quant.factor_defs import enabled_factors
    total_factors = len(enabled_factors())

    return {
        "stocks": k_n or 0,
        "kline_from": k_from.isoformat() if k_from else None,
        "kline_to": k_to.isoformat() if k_to else None,
        "financial_stocks": f_n or 0,
        "factors_with_data": fk or 0,
        "factors_total": total_factors,
        "disk_mb": disk_mb,
        # 库是空的 → 前端提示"到「数据」页下载",而不是让用户对着空界面发懵
        "empty": (k_n or 0) == 0,
    }


# ═══════════════════════════════════════════════════════════
# 可选范围
# ═══════════════════════════════════════════════════════════

def scopes(user_id: str | None = None) -> dict:
    """可选范围 + 每个的股票数(给前端预估用)。"""
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""SELECT l1, l2, count(*) FROM stock_industry
                        GROUP BY l1, l2 ORDER BY l1, count(*) DESC""")
        ind_rows = cur.fetchall()
    except Exception:                                         # noqa: BLE001
        ind_rows = []
    finally:
        cur.close(); conn.close()

    by_l1: dict[str, list] = {}
    for l1, l2, n in ind_rows:
        by_l1.setdefault(l1, []).append({"l2": l2, "count": n})

    return {
        "indexes": [
            {"kind": "hs300", "label": "沪深 300", "count": len(_index_codes("000300"))},
            {"kind": "zz500", "label": "中证 500", "count": len(_index_codes("000905"))},
            {"kind": "zz1000", "label": "中证 1000", "count": len(_index_codes("000852"))},
        ],
        "all_a": {"kind": "all_a", "label": "全 A 股", "count": len(_all_a_codes())},
        "watchlist": {"kind": "watchlist", "label": "我的自选",
                      "count": len(_watchlist_codes(user_id))},
        # 行业表没 seed 时返回空 list + seeded=false,前端据此提示
        # "行业分类还没同步",而不是显示一个空白的行业区
        "industries": [{"l1": l1, "children": by_l1.get(l1, [])} for l1 in L1_ORDER],
        "industry_seeded": bool(ind_rows),
    }


# ═══════════════════════════════════════════════════════════
# 预估
# ═══════════════════════════════════════════════════════════

# 右端容差:数据到"最近一个交易日"就算齐了。
#
# 不留容差的话「可跳过」永远是 0:今天是周一,数据到上周五 —— 那已经是
# 最新交易日了,但 covered_to(周五) < want_to(周一),于是判定"没覆盖"。
# 结果增量更新这个功能等于不存在,用户第二次下同样范围还是等一遍全程。
#
# 5 天足够覆盖周末 + 连续假期的头几天。宁可偶尔多下一次,
# 也不要让"跳过"永远不触发。
_FRESH_TOLERANCE_DAYS = 5


def _covered(codes: list[str], data_type: str, want_from: date, want_to: date) -> set[str]:
    """这些票里,哪些**已经完整覆盖**了要的区间(可以整只跳过)。"""
    if not codes:
        return set()
    right = want_to - timedelta(days=_FRESH_TOLERANCE_DAYS)
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""SELECT code FROM data_coverage
                        WHERE code = ANY(%s) AND data_type=%s
                          AND covered_from <= %s AND covered_to >= %s""",
                    (codes, data_type, want_from, right))
        return {r[0] for r in cur.fetchall()}
    finally:
        cur.close(); conn.close()


def estimate(scope: dict, span_months: int, with_financial: bool,
             keep_raw: bool = False, user_id: str | None = None) -> dict:
    """预估:只数 / 可跳过 / 耗时 / 磁盘。

    `span_months = 0` 表示「只补最新」。

    **「只补最新」的耗时按只数算,不按数据量算** —— 这点容易搞反:
    以为"只补几天很快"就把总时间也除掉了。实际每只票仍然要打一次上游,
    800 只补最新还是要十几分钟。
    """
    codes, note = resolve_scope(scope, user_id)
    n = len(codes)
    if not n:
        # **字段要齐**。早退时少给一个 key,前端读 d.warn 就是 undefined,
        # 而 Python 客户端直接 KeyError —— 实测踩到过
        return {"stocks": 0, "skip": 0, "todo": 0, "seconds": 0,
                "disk_mb": 0, "note": note, "warn": ""}

    end = date.today()
    latest_only = span_months <= 0
    # 「只补最新」= 只要最近这一小段,所以覆盖判定用一个很短的窗口:
    # 已经覆盖到今天的可以跳过,差几天的要补
    start = end - timedelta(days=7 if latest_only else span_months * 31)

    skip_k = _covered(codes, "kline", start, end)
    skip = set(skip_k)
    if with_financial:
        # 要财报的话,日线和财报都齐了才能跳过
        skip &= _covered(codes, "financial", start, end)
    todo = n - len(skip)

    years = max(0.1, (span_months or 1) / 12.0)
    sec = todo * (RATE_KLINE_SEC * (0.5 if latest_only else 1.0)
                  + (RATE_FIN_SEC if with_financial else 0.0))
    mb = todo * ((MB_KLINE_PER_YEAR + MB_FACTOR_PER_YEAR) * (0.05 if latest_only else years)
                 + (MB_FIN_METRIC if with_financial else 0)
                 + (MB_FIN_RAW if (with_financial and keep_raw) else 0))

    return {
        "stocks": n,
        "skip": len(skip),
        "todo": todo,
        "seconds": int(sec),
        "disk_mb": round(mb, 1),
        "note": note,
        # 大范围要显式告诉用户可以暂停 —— 老板说"不要怕跑的时间长",
        # 但前提是用户知道自己随时能停、已下的不会丢
        "warn": ("这个范围很大 —— 中途随时可以暂停,已下载的不会丢,下次接着跑"
                 if todo > 2000 else ""),
    }
