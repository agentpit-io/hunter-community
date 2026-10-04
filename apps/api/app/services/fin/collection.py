"""采集补齐 · 新闻 / 基本面（五期 L09 · 五期方案 §四 `L09` · 技术方案 §2.1 / §6.1 / §6.2）。

**这一段解决什么**：方案 §2.1 要「24 小时自动采集 —— 持续订阅与增量抓取行情、新闻、
基本面、情绪信息，并记录数据缺口与新鲜度」。改造前 fin 侧只有 K 线 ETL / 交易日历 /
标的同步；`news` 与 `financial_metric` 两张表**早就存在**（`app/services/database.py`），
但**没接进 fin 侧的定时采集**；情绪**全仓无表**（见 `sentiment.py`）。

本模块把**新闻与基本面**两条链路接上，情绪（只有表 + 登记口）在 `sentiment.py`。

## 边界（`plan/五期追加规则.md` §3.2 / 方案 §6.1 的**缩小范围**）

- **不是新建数据源** —— 复用已有取数实现：
  - 新闻：`finance_data_client.get_news`（个股新闻 + 公告，含用户自配源）；
  - 基本面：`quant.financial_store.download_one`（akshare 财务指标 → `financial_metric`）。
- **数据源不通 / 拿不到 → 记缺口（`fin_data_gap`）、不写假行**。空手回来就记一条
  `kind='no_data'`，**绝不插一条空标题 / 零值行**充数（红线 5）。
- **缺口复用已有机制**：`fin_data_gap`（`0028_fin_data_gap.sql` · 运行期 DDL 在
  `apps/paper/app/data_gap.py` / `app/aux_ddl.py`），**不另建一套**。本模块的 INSERT
  与 `paper.app.data_gap.record` **逐列一致**（两个容器、两套依赖，不能互相 import；
  改列名/口径时**两处一起改**）。

## 时间字段口径（与 `L03` 对齐，别另发明一套）

| 字段 | 新闻 | 基本面 |
|---|---|---|
| `event_time`（事件时刻） | `published_at`（来源标称发布时刻，解析不出 → NULL） | `report_date`（报告期） |
| `ingested_at`（入库时刻） | `fetched_at`（本模块写 `now()`，**这是真值**） | `updated_at` / `fetched_at`（写库时 `now()`） |
| `available_at`（当时实际可获取时刻） | **NULL**（源不给，也没测可获取延迟 → 红线 5） | **NULL**（同上） |
| `revision_id`（数据修订版本） | **NULL**（新闻无修订号） | **NULL**（财报重述版本，接口不给） |

`published_at` 解析不出时**存 NULL，不拿 `now()` 顶替**（那是把「入库时刻」冒充成「发布时刻」）。

## 采集标的（universe）

显式给 `codes` 就用它；否则 = **自选股（`stocks`）∪ 系统标的（`fin_instrument`）**
按市场过滤、去重、排序、截断。两者都是「系统已知的标的」，**不是全市场** ——
全市场每只票都抓新闻/财报既慢又无意义，且方案 §6.1 只要求「增量抓取」。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Iterable, Optional

import psycopg2
from loguru import logger

# 连接方式与 `app/services/fin/data_snapshot.py` / `app/services/fin/store.py` 同口径：
# psycopg2 直连，模块级 `DATABASE_URL`（测试按同一套手法 patch 它）。
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://hunter:hunter@localhost:5432/hunter")

# 采集通道用的市场写法（`cn` / `hk` / `us`，与 K 线 ETL 同款）→
# ① 本仓统一三值（`CN_A` / `HK` / `US`，进 `fin_data_gap.market`）
# ② `stocks.market` 用的值（`A` / `HK` / `US`，`database.py` 的外键形态）
# ③ `fin_instrument.market` 用的值（`CN_A` / `HK` / `US`）
_MARKETS: dict[str, dict[str, str]] = {
    "cn": {"label": "CN_A", "stocks": "A", "instrument": "CN_A"},
    "hk": {"label": "HK", "stocks": "HK", "instrument": "HK"},
    "us": {"label": "US", "stocks": "US", "instrument": "US"},
}

# `fin_data_gap.kind` 的合法值由 `0028` 的 CHECK 约束（`no_data` / `no_timestamp` / `no_price`）。
# 采集用的是 `no_data` —— 语义与它完全一致：「数据源没给这张票数据（断流 / 池里没有）」。
GAP_KIND = "no_data"


def get_conn():
    return psycopg2.connect(DATABASE_URL)


class CollectionError(ValueError):
    """采集入参不合法（路由层翻 400）。"""


def market_label(market: str) -> str:
    """`cn` / `hk` / `us` → `CN_A` / `HK` / `US`。未知 → 原样（不猜）。"""
    return _MARKETS.get((market or "").strip().lower(), {}).get("label", market)


def _valid_code(code: str, market: str) -> bool:
    """代码形态是否属于该市场 —— **纯形态判定，不猜标的**。

    与 `fin_data.market_of` 同口径：A 股 6 位数字、港股 5 位数字、美股含字母。
    用途：把误入清单的脏数据（如测试残留的 7 位数字）挡在采集之外 —— 否则它们
    每轮都会去取数失败、往 `fin_data_gap` 里灌噪声条目。
    """
    s = (code or "").strip()
    if not s:
        return False
    if market == "cn":
        return s.isdigit() and len(s) == 6
    if market == "hk":
        return s.isdigit() and len(s) == 5
    if market == "us":
        return any(ch.isalpha() for ch in s)
    return False


def _table_exists(cur, name: str) -> bool:
    """表在不在（只读、不抛）。查不出来当「不在」，跳过该来源而不是报错。"""
    try:
        cur.execute("SELECT to_regclass(%s) AS t", (f"public.{name}",))
        row = cur.fetchone()
    except Exception:  # noqa: BLE001
        return False
    if row is None:
        return False
    value = row.get("t") if isinstance(row, dict) else row[0]
    return value is not None


def collection_universe(cur, market: str, limit: int,
                        codes: Optional[Iterable[str]] = None) -> list[str]:
    """该市场的采集标的清单（去重 · 升序 · 截断）。

    显式 `codes` 优先（只做形态过滤）。否则 = 自选股（`stocks.enabled`）∪ 系统标的
    （`fin_instrument`），**两处都按市场过滤**；表不存在就跳过该来源（不报错）。
    """
    market = (market or "").strip().lower()
    if market not in _MARKETS:
        raise CollectionError(f"市场必须是 cn / hk / us 之一，收到 {market!r}")
    if codes is not None:
        seen: list[str] = []
        for c in codes:
            c = (c or "").strip()
            if c and _valid_code(c, market) and c not in seen:
                seen.append(c)
        return seen[:limit]

    m = _MARKETS[market]
    found: set[str] = set()
    # 自选股（api 自己的表）。
    if _table_exists(cur, "stocks"):
        try:
            cur.execute("SELECT DISTINCT code FROM stocks WHERE enabled = TRUE AND market = %s",
                        (m["stocks"],))
            found.update(str(r[0]).strip() for r in cur.fetchall() if r[0])
        except Exception as exc:  # noqa: BLE001
            logger.warning("[collect] 读 stocks 失败（跳过该来源）：{}", exc)
    # 系统标的（fin_instrument · paper 的表，同库只读）。
    if _table_exists(cur, "fin_instrument"):
        try:
            cur.execute("SELECT DISTINCT code FROM fin_instrument WHERE market = %s",
                        (m["instrument"],))
            found.update(str(r[0]).strip() for r in cur.fetchall() if r[0])
        except Exception as exc:  # noqa: BLE001
            logger.warning("[collect] 读 fin_instrument 失败（跳过该来源）：{}", exc)

    return sorted(c for c in found if _valid_code(c, market))[:limit]


def record_gap(cur, code: str, *, market: Optional[str], source: str,
               detail: Optional[str] = None, kind: str = GAP_KIND) -> None:
    """记一条采集缺口。**列与 `paper.app.data_gap.record` 逐列一致**（口径唯一）。

    失败只记日志，**绝不让一次缺口记录打断采集**（与 paper 同口径）。
    """
    try:
        cur.execute(
            """
            INSERT INTO fin_data_gap (code, market, source, kind, detail, event_time)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (code, market, source, kind, detail, None),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[collect] 记缺口失败 {} {} · {}", code, source, exc)


# ── 新闻 ──────────────────────────────────────────────────────────────────

def _parse_ts(value) -> Optional[datetime]:
    """新闻源给的发布时间（ISO8601 字符串 / datetime）→ 带时区 datetime。

    **解析不出就返回 None**（→ 存 NULL），不拿当前时间顶替 —— 那是把入库时刻
    冒充成发布时刻（红线 5）。
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _news_exists(cur, code: Optional[str], title: str, published_at: Optional[datetime]) -> bool:
    """去重键 = （代码, 标题, 发布时刻）。`news` 表没有唯一约束（历史表，不动它），
    所以在应用层去重。`IS NOT DISTINCT FROM` 让 NULL（无代码 / 无发布时刻）也能比对。"""
    cur.execute(
        "SELECT 1 FROM news WHERE code IS NOT DISTINCT FROM %s "
        "AND title = %s AND published_at IS NOT DISTINCT FROM %s LIMIT 1",
        (code, title, published_at),
    )
    return cur.fetchone() is not None


def collect_news(market: str, *, limit: int = 30, codes: Optional[Iterable[str]] = None,
                 per_code: int = 20) -> dict:
    """把该市场的标的新闻抓进 `news` 表（增量：已存在的（代码,标题,发布时刻）不重复插）。

    取数**复用 `finance_data_client.get_news`**（个股新闻 + 公告，含用户自配源）。

    返回 `{ok, market, codes, fetched, inserted, gaps, latest_published_at, latest_fetched_at}`。
    **每只票拿到 0 条 → 记一条缺口**（`kind='no_data'`）—— 与 `fin_data_gap` 的既有语义
    一致（「数据源没给这张票数据」），这也是「数据源不通时记缺口、不写假行」的落点：
    **一行假新闻都不插**。
    """
    from app.services.finance_data_client import get_news

    market = (market or "").strip().lower()
    label = market_label(market)
    conn = get_conn()
    inserted = 0
    gaps = 0
    fetched = 0
    scanned: list[str] = []
    try:
        with conn.cursor() as cur:
            if not _table_exists(cur, "news"):
                return {"ok": False, "market": label, "codes": 0, "fetched": 0,
                        "inserted": 0, "gaps": 0,
                        "reason": "news 表不存在（基础表没建） —— 无法采集"}
            universe = collection_universe(cur, market, limit, codes)
        scanned = list(universe)

        for code in universe:
            try:
                items = get_news(code, limit=per_code) or []
            except Exception as exc:  # noqa: BLE001 —— 单只失败不打断整轮
                logger.warning("[collect.news] {} 取新闻失败：{}", code, exc)
                items = []
            fetched += len(items)
            now = datetime.now(timezone.utc)
            with conn.cursor() as cur:
                wrote = 0
                for it in items:
                    title = (it.get("title") or "").strip()
                    if not title:
                        continue                      # 没标题的不插（空行无意义）
                    pub = _parse_ts(it.get("published_at"))
                    if _news_exists(cur, code, title, pub):
                        continue
                    cur.execute(
                        "INSERT INTO news (code, title, source, url, content, "
                        "published_at, fetched_at, available_at, revision_id) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, NULL)",
                        (code, title, (it.get("source") or "").strip() or None,
                         (it.get("url") or "").strip() or None,
                         it.get("content"), pub, now),
                    )
                    wrote += 1
                # 缺口只在**源一条都没给**时记（源不通 / 该票无新闻）——**不插假行**。
                # ⚠️ 不能用 `wrote == 0` 判：源正常返回、但都是已有行（去重命中）时
                #    `wrote` 也是 0，那是「增量没新东西」，**不是缺口**（一轮轮跑会把
                #    每天的正常采集刷成一堆假缺口）。
                if not items:
                    record_gap(cur, code, market=label, source="news",
                               detail=f"本轮取到 0 条新闻（per_code={per_code}）")
                    gaps += 1
                inserted += wrote
                conn.commit()

        latest = _latest_news(conn)
        logger.info("[collect.news] market={} 标的 {} · 采到 {} 条 · 新增 {} · 缺口 {}",
                    label, len(scanned), fetched, inserted, gaps)
        return {"ok": True, "market": label, "codes": len(scanned), "fetched": fetched,
                "inserted": inserted, "gaps": gaps, **latest}
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        logger.error("[collect.news] market={} 采集失败：{}", label, exc)
        return {"ok": False, "market": label, "codes": len(scanned), "fetched": fetched,
                "inserted": inserted, "gaps": gaps, "reason": f"{type(exc).__name__}: {exc}"}
    finally:
        conn.close()


# ── 基本面 ────────────────────────────────────────────────────────────────

def collect_fundamentals(market: str, *, limit: int = 50,
                         codes: Optional[Iterable[str]] = None,
                         keep_raw: bool = False) -> dict:
    """把该市场标的的财报抓进 `financial_metric`（复用 `financial_store.download_one`）。

    **只有 A 股（`cn`）有已接的数据源**（akshare `stock_financial_analysis_indicator`）；
    港 / 美股财报**未接**（源存在，管线没做）—— 那两种市场如实返回「数据源未接」，
    **不记缺口**（缺的是「没有源」，不是「这一轮没采到」，两回事）。

    增量：**已经有指标的票跳过**（`financial_store.has_metrics`）—— 每晚只补新票 /
    没有数据的票，不重复下载（每只 8.6 秒，全量重下没有意义）。每只失败 → 记缺口。

    返回 `{ok, market, candidates, downloaded, metrics, failed, skipped, reason?}`。
    """
    from app.services.quant import financial_store

    market = (market or "").strip().lower()
    label = market_label(market)
    if market != "cn":
        return {"ok": False, "market": label, "candidates": 0, "downloaded": 0,
                "metrics": 0, "failed": 0, "skipped": 0,
                "reason": f"{label} 财报数据源未接（当前只接了 A 股的 akshare 财务指标）"}

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            universe = collection_universe(cur, market, limit, codes)
    finally:
        conn.close()

    have = financial_store.has_metrics(universe) if universe else set()
    candidates = [c for c in universe if c not in have][:limit]

    downloaded = 0
    metrics = 0
    failed = 0
    gaps: list[str] = []
    for code in candidates:
        try:
            res = financial_store.download_one(code, keep_raw=keep_raw)
        except Exception as exc:  # noqa: BLE001 —— 单只失败不打断整轮
            logger.warning("[collect.fundamental] {} 下载异常：{}", code, exc)
            res = {"ok": False, "why": f"{type(exc).__name__}: {exc}"}
        if res.get("ok"):
            downloaded += 1
            metrics += int(res.get("metrics") or 0)
        else:
            failed += 1
            gaps.append(code)
            logger.info("[collect.fundamental] {} 失败：{}", code, res.get("why"))

    # 缺口单独一批写（download_one 自带连接，不把它的长事务和这里混在一起）。
    if gaps:
        gconn = get_conn()
        try:
            with gconn.cursor() as cur:
                for code in gaps:
                    record_gap(cur, code, market=label, source="fundamental",
                               detail="本轮拉财报拿不到数据（akshare 财务指标）")
            gconn.commit()
        except Exception as exc:  # noqa: BLE001
            gconn.rollback()
            logger.warning("[collect.fundamental] 写缺口失败：{}", exc)
        finally:
            gconn.close()

    latest = _latest_financial(universe)
    logger.info("[collect.fundamental] market={} 候选 {} · 入库 {} 只 / {} 条指标 · 失败 {}",
                label, len(candidates), downloaded, metrics, failed)
    return {"ok": True, "market": label, "candidates": len(candidates),
            "downloaded": downloaded, "metrics": metrics, "failed": failed,
            "skipped": len(universe) - len(candidates), **latest}


# ── 新鲜度读数（返回给工作流日志 / 验收用）──────────────────────────────────

def _latest_news(conn) -> dict:
    """`news` 的行数 + 最新 `fetched_at`（供「真的按调度进来」的读数）。"""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), max(fetched_at) FROM news")
            n, mx = cur.fetchone()
        return {"news_rows": int(n),
                "latest_news_fetched_at": mx.isoformat() if mx else None}
    except Exception:  # noqa: BLE001
        return {}


def _latest_financial(codes: list[str]) -> dict:
    """`financial_metric` 的行数 + 最新 `updated_at`（读数用）。"""
    if not codes:
        return {"financial_metric_rows": 0, "latest_financial_updated_at": None,
                "latest_report_date": None}
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), max(updated_at), max(report_date) "
                        "FROM financial_metric WHERE code = ANY(%s)", (codes,))
            n, mx, rd = cur.fetchone()
        return {"financial_metric_rows": int(n),
                "latest_financial_updated_at": mx.isoformat() if mx else None,
                "latest_report_date": rd.isoformat() if rd else None}
    except Exception:  # noqa: BLE001
        return {}
    finally:
        conn.close()


__all__ = ["CollectionError", "collection_universe", "record_gap", "collect_news",
           "collect_fundamentals", "market_label", "get_conn", "GAP_KIND"]
