"""财报 · 下载 → 提炼 → 落库(financial_metric)。

方案见 doc/开源hunter-community/01详细工作目录/11量化策略/
      22_20260822_数据中心_技术方案.md §10

## 为什么要落库而不是每次现拉

现在 `akshare_client` 是每次算因子就去 AKShare 拉一遍,靠 `lru_cache`
挡住重复请求。两个问题:

  1. 缓存是**进程内存**,容器一重启就没了 —— 每周任务都要重新付
     800 只 × 8.6 秒 ≈ 1.9 小时的冷启动
  2. 缓存 key 是 `(code, year-2)` 且**永不失效**。api 长期运行时新季报
     出来了,缓存里还是旧数据,而界面显示的"最新日期"是本周六 ——
     看起来新鲜、数字是旧的(方案 §G0 记的问题)

落库之后:下载一次永久可用,新季报靠"再下一次"进来,而不是靠进程重启。

## 一个接口就够

原本以为要拉四张表(资产负债/利润/现金流/财务指标)。实测发现
`stock_financial_analysis_indicator` 一个接口就把**算好的比率**全给了:

    销售毛利率(%)          → gross_margin
    净资产收益率(%)        → roe
    总资产净利润率(%)      → roa
    资产负债率(%)          → debt_ratio
    主营业务收入增长率(%)  → revenue_growth_yoy
    净利润增长率(%)        → earnings_growth_yoy

所以不用自己从三大报表推(营收 − 成本 → 毛利率 那一套),
下载量和出错面都小得多。三大报表只有 EV/EBITDA 才需要,
那个因子暂时不在这一步范围内。
"""
from __future__ import annotations

import json
import logging
import math
import re
import warnings
from datetime import date

log = logging.getLogger(__name__)


def get_conn():
    """延迟导入 —— **取数和解析这两个函数不该拖进数据库依赖**。

    服务器采集(scripts/harvest_to_csv.py)只用 fetch_indicator + parse,
    直接写 CSV、不入库。而顶层 `from app.services.database import get_conn`
    会连带拖进 loguru、psycopg2 一整串,在只装了 requests+akshare 的
    采集机上直接 ModuleNotFoundError。

    放到函数里之后,不落库的调用方完全不碰这些依赖。
    """
    from app.services.database import get_conn as _c
    return _c()

# 上游列名 → 我们的 metric_key。
#
# **列名是实测 dump 出来的,不是猜的**(600519 · stock_financial_analysis_indicator)。
# 猜错的后果是这个 key 永远拿不到值,而因子那边只会显示"没有数据" ——
# 排查起来要翻两层。
COLUMN_MAP: dict[str, str] = {
    # ⚠ 实测 600519:这一列上游给的全是 nan(而同一行的「资产负债率」有值),
    # 所以 gross_margin 大概率长期拿不到。保留映射 —— 万一上游哪天补上了
    # 就自动有数据。但**因子那边必须能看出它是空的**,见下面的 report()。
    "销售毛利率(%)": "gross_margin",
    "净资产收益率(%)": "roe",
    "加权净资产收益率(%)": "roe_weighted",
    "总资产净利润率(%)": "roa",
    "资产负债率(%)": "debt_ratio",
    "主营业务收入增长率(%)": "revenue_growth_yoy",
    "净利润增长率(%)": "earnings_growth_yoy",
    "净资产增长率(%)": "equity_growth_yoy",
    "总资产增长率(%)": "asset_growth_yoy",
    "摊薄每股收益(元)": "eps_diluted",
    "每股净资产_调整后(元)": "bps",
    "每股经营性现金流(元)": "cfps",
    "总资产(元)": "total_asset",
    "存货周转率(次)": "inventory_turnover",
    "总资产周转率(次)": "asset_turnover",
}

METRIC_KEYS = sorted(set(COLUMN_MAP.values()))

# 只留最近这么多期。
#
# 全历史 103 期 = 26 年,而回测最多几年、因子算同比只要 8 期。
# 留 20 期(5 年)在体积和够用之间。
KEEP_PERIODS = 20


def _num(v) -> float | None:
    """上游给的是字符串,且有 '--' / '' / '不适用' 这些占位。

    **取不到就是 None,不是 0** —— 0 会被当成"这家公司毛利率是 0%"
    参与打分,而那是个有意义的值。CLAUDE.md:空的比假的好。
    """
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s or s in ("--", "-", "None", "nan", "不适用", "未披露"):
        return None
    # 有些字段带单位后缀,比如 "12.34%"
    s = re.sub(r"[%元次天]$", "", s)
    try:
        f = float(s)
    except ValueError:
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def fetch_indicator(code: str, years_back: int = 3):
    """拉财务指标表。**带硬超时** —— akshare 底层 requests 没有超时,
    卡住就是永久卡住,而它是在下载任务的循环里被调的。"""
    import concurrent.futures as cf
    warnings.filterwarnings("ignore")
    try:
        import akshare as ak
    except Exception as e:                                    # noqa: BLE001
        log.warning("[financial] akshare 不可用: %s", e)
        return None

    start_year = str(date.today().year - years_back)

    def _do():
        return ak.stock_financial_analysis_indicator(symbol=code, start_year=start_year)

    # 超时 75 秒、只重试一次。
    #
    # 这几个数是实测调出来的:
    #   · `stock_financial_analysis_indicator` 内部**按年逐个请求**
    #     (日志里能看到 0/7 的进度条 = 7 年 7 次 HTTP),
    #     所以 years_back 从 6 降到 3,请求数几乎减半
    #   · 40 秒不够,慢的票会一直 TimeoutError
    #   · 但重试 3 次 × 40 秒 = 每只永久失败的票磨 125 秒。
    #     5400 只的任务里 10% 永久失败,光耗在失败上就是 18 小时 ——
    #     所以只重试一次,worst case 从 125 秒降到 ~80 秒
    import time as _t
    for attempt in range(2):
        ex = cf.ThreadPoolExecutor(max_workers=1)
        try:
            df = ex.submit(_do).result(timeout=75 if attempt == 0 else 40)
            ex.shutdown(wait=False)
            if df is not None and len(df):
                return df
        except Exception as e:                                # noqa: BLE001
            if attempt:
                log.warning("[financial] 拉 %s 失败(重试 1 次): %s",
                            code, type(e).__name__)
        _t.sleep(1.0)
    return None


def parse(df) -> list[tuple[date, str, float]]:
    """DataFrame → [(报告期, metric_key, 值)]。

    **列名找不到就跳过那个 key,不猜相近的列** —— 猜错会把
    "总资产周转率"当成"总资产"存进去,而那种错在因子值里看不出来。
    """
    if df is None or len(df) == 0:
        return []
    cols = set(df.columns)
    if "日期" not in cols:
        log.error("[financial] 返回里没有「日期」列 —— 上游可能换了结构,不猜着解析")
        return []

    missing = [c for c in COLUMN_MAP if c not in cols]
    if missing:
        # 少几列是正常的(不同行业口径不同),但要能看见少了什么
        log.debug("[financial] 缺列: %s", missing[:5])

    out: list[tuple[date, str, float]] = []
    recs = df.to_dict("records")
    # 上游是按日期升序给的,取最近 KEEP_PERIODS 期
    recs = recs[-KEEP_PERIODS:] if len(recs) > KEEP_PERIODS else recs
    for r in recs:
        raw_d = str(r.get("日期") or "")[:10]
        try:
            rd = date.fromisoformat(raw_d)
        except ValueError:
            continue
        for col, key in COLUMN_MAP.items():
            if col not in cols:
                continue
            v = _num(r.get(col))
            if v is not None:
                out.append((rd, key, v))
    return out


def save(code: str, rows: list[tuple[date, str, float]]) -> int:
    if not rows:
        return 0
    conn = get_conn(); cur = conn.cursor()
    n = 0
    try:
        for rd, key, v in rows:
            cur.execute(
                """INSERT INTO financial_metric (code, report_date, metric_key, value)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (code, report_date, metric_key) DO UPDATE
                     SET value = EXCLUDED.value, updated_at = now()""",
                (code, rd, key, v))
            n += cur.rowcount
        conn.commit()
        return n
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close(); conn.close()


def save_raw(code: str, df) -> int:
    """原始归档 —— 只在用户勾了「保留原始报表」时调。

    留它的理由:下载 8.6 秒/只、解析毫秒级。以后加一个新因子
    (比如存货周转率的同比),有归档就是重新解析几秒,没有就要重下。
    """
    if df is None or len(df) == 0:
        return 0
    recs = df.to_dict("records")
    recs = recs[-KEEP_PERIODS:] if len(recs) > KEEP_PERIODS else recs
    conn = get_conn(); cur = conn.cursor()
    n = 0
    try:
        for r in recs:
            raw_d = str(r.get("日期") or "")[:10]
            try:
                rd = date.fromisoformat(raw_d)
            except ValueError:
                continue
            payload = {k: (None if v is None else str(v)) for k, v in r.items()}
            cur.execute(
                """INSERT INTO financial_raw (code, report_date, report_type, payload)
                   VALUES (%s,%s,'indicator',%s::jsonb)
                   ON CONFLICT (code, report_date, report_type) DO UPDATE
                     SET payload = EXCLUDED.payload, fetched_at = now()""",
                (code, rd, json.dumps(payload, ensure_ascii=False)))
            n += cur.rowcount
        conn.commit()
        return n
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close(); conn.close()


def download_one(code: str, keep_raw: bool = False) -> dict:
    """下一只票的财报并落库。返回 {ok, metrics, periods, raw}。"""
    df = fetch_indicator(code)
    if df is None or len(df) == 0:
        return {"ok": False, "why": "拿不到财报数据"}
    rows = parse(df)
    if not rows:
        return {"ok": False, "why": "解析不出任何指标"}
    n = save(code, rows)
    raw_n = save_raw(code, df) if keep_raw else 0
    periods = len({r[0] for r in rows})
    return {"ok": True, "metrics": n, "periods": periods, "raw": raw_n}


def read_metric(codes: list[str], metric_key: str, trade_date: date,
                lookback_days: int = 400) -> dict[str, float]:
    """从库里读某个指标在 trade_date 之前**最近一期**的值。

    这是"先查库"的那一半 —— 因子不再每次去 AKShare 拉。好处:

      · 不受进程内存缓存影响(容器重启不用重付 1.9 小时冷启动)
      · 新季报靠"再下一次"进来,而不是靠重启进程刷缓存

    lookback 400 天:季报一年 4 期,400 天保证至少能找到一期;
    再往前的数据对"当期基本面"没有意义。
    """
    if not codes:
        return {}
    lo = date.fromordinal(max(1, trade_date.toordinal() - lookback_days))
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            """SELECT DISTINCT ON (code) code, value
                 FROM financial_metric
                WHERE code = ANY(%s) AND metric_key=%s
                  AND report_date <= %s AND report_date >= %s
                  AND value IS NOT NULL
                ORDER BY code, report_date DESC""",
            (codes, metric_key, trade_date, lo))
        return {c: float(v) for c, v in cur.fetchall()}
    except Exception as e:                                    # noqa: BLE001
        log.warning("[financial] 读 %s 失败: %s", metric_key, e)
        return {}
    finally:
        cur.close(); conn.close()


def coverage_report(codes: list[str]) -> list[dict]:
    """每个指标实际拿到了多少只票 —— 用来暴露"上游这列是空的"这种情况。

    实测过一个真实案例:`销售毛利率(%)` 这列上游返回全是 nan,于是
    gross_margin 因子永远没数据。如果不报出来,用户选了这个因子只会看到
    "没有数据",而排查要翻到上游返回值那一层。
    """
    if not codes:
        return []
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""SELECT metric_key, count(DISTINCT code), max(report_date)
                         FROM financial_metric WHERE code = ANY(%s)
                        GROUP BY metric_key""", (codes,))
        got = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    finally:
        cur.close(); conn.close()
    out = []
    for k in METRIC_KEYS:
        n, latest = got.get(k, (0, None))
        out.append({
            "metric_key": k,
            "stocks": n,
            "latest": latest.isoformat() if latest else None,
            "note": ("上游这一列没有数据" if n == 0 else
                     ("覆盖不全" if n < len(codes) * 0.5 else "")),
        })
    return out


def has_metrics(codes: list[str], need_raw: bool = False) -> set[str]:
    """哪些票**已经齐了、可以跳过**。

    `need_raw=True` 时要求指标和原始归档都有 —— 否则会出现
    「选了没生效」:用户第一次不勾归档下了一批,之后想补归档再下一次,
    被整只跳过,`financial_raw` 永远是空的,而界面上他明明勾了。
    这类"选项静默失效"比直接报错难查得多。
    """
    if not codes:
        return set()
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT DISTINCT code FROM financial_metric WHERE code = ANY(%s)",
                    (codes,))
        have = {r[0] for r in cur.fetchall()}
        if not need_raw:
            return have
        cur.execute("SELECT DISTINCT code FROM financial_raw WHERE code = ANY(%s)",
                    (codes,))
        have_raw = {r[0] for r in cur.fetchall()}
        return have & have_raw
    finally:
        cur.close(); conn.close()
