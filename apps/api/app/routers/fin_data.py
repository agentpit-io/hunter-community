"""内网 · 智能炒股的数据面入口（`配置/paper` 与 `fin-worker` 调它）。

| 路径 | 谁调 | 干什么 |
|---|---|---|
| `GET  /internal/fin/quote/{code}` | paper 的行情适配层 | 统一行情结构（最新价 + 买一/卖一盘口 + 数据源时刻） |
| `GET  /internal/fin/instruments` | fin-worker 的同步任务 | 解析涨跌停（代码形态）/ ST（腾讯通道简称）元数据 |
| `POST /internal/fin/data/snapshot` · `GET …/{id}` | L03 · 策略 / 研究工作流 | `DataSnapshot` 对象（只追加） |
| `POST /internal/fin/collect/news` | L09 · `fin.news_collect` | 新闻接进 fin 侧定时采集（写 `news`） |
| `POST /internal/fin/collect/fundamental` | L09 · `fin.fundamental_collect` | 基本面接进 fin 侧定时采集（写 `financial_metric`） |
| `POST /internal/fin/sentiment` · `GET …/{code}` | L09 · 情绪登记口 | 情绪**只追加**登记 / 读回（**数据源未接**） |

鉴权与 `/api/internal/*` 其余端点同一把口令（`X-Hunter-Internal-Key`）。

**采集（L09）**：`POST …/collect/*` 是**写**接口（写 `news` / `financial_metric` /
`fin_data_gap`）—— 它们在 `/internal/*` 前缀下（**要口令**），与面向用户的免登录前缀
`/api/catalog/*` 无关（`CLAUDE.md` 铁律）。数据源不通时**记缺口、不写假行**（红线 5）。

**为什么不让 paper 直接打 `/api/quote/{code}`**：那条路径先查用户的股票表
（`get_stocks()`），代码不在表里就 404 —— 它是给**用户看自己的自选**用的。
记账服务要的是「任意合法代码此刻的报价」，所以走这条不带用户上下文的内部路径。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

import psycopg2.extras
from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel

from app.services import fin_data
from app.services.fin import data_snapshot as ds_svc

router = APIRouter(prefix="/internal/fin", tags=["internal-fin-data"])

_INTERNAL_KEY = os.getenv("HUNTER_INTERNAL_KEY", "")


def _auth(request: Request) -> None:
    key = request.headers.get("X-Hunter-Internal-Key", "")
    if not _INTERNAL_KEY or key != _INTERNAL_KEY:
        raise HTTPException(401, "internal auth failed")


@router.get("/quote/{code}")
def get_quote(request: Request, code: str) -> dict:
    """统一行情结构；**拿不到就 404**（不返回零价，也不拿上次的价顶替）。"""
    _auth(request)
    q = fin_data.quote(code)
    if q is None:
        raise HTTPException(404, f"{code} 没有可用行情（数据源未接通或该代码无报价）")
    return q


@router.get("/instruments")
def get_instruments(
    request: Request,
    codes: str = Query("", description="逗号分隔的代码（A 股 6 位 / 港股 5 位 / 美股字母）；留空 = 全量"),
    market: str = Query("cn"),
    limit: int = Query(2000, ge=1, le=10000),
) -> dict:
    """把标的元数据解析出来给 fin-worker 同步进 `fin_instrument`。

    **三个市场都支持（N3 放开）**：

    | market | 解析什么 | 每手股数来源 | 涨跌幅 |
    |---|---|---|---|
    | `cn` | 板块 → 涨跌停幅度 + ST（腾讯简称） | 100（交易所规则） | `limit_up_pct` |
    | `hk` | 交易所 / 每手 / 名称 | 港交所官方 `data/hk_master.csv` | `NULL`（无涨跌幅限制） |
    | `us` | 交易所 / 名称 | `1`（事实） | `NULL`（无涨跌幅限制） |

    A 股：名称 / 板块优先取 `company_master`（跨库但同实例，`09 §八`），没有就用
    `stock_universe`，再没有就由代码形态推板块（公开规则）。**都判不出 → 该标的
    `available=false` 且带上原因**：调用方据此跳过它，风控第 4 条会拒绝它 ——
    绝不猜一个涨跌幅写进账本。

    `codes` 留空 = 全量同步（A 股 = 两个 master 表的并集；港股 = `stock_universe`
    该市场行，空则回落港交所官方清单；美股 = `stock_universe` 该市场行，空则**拒绝并说明**
    —— 美股没有本地清单，不猜）。
    """
    _auth(request)
    wanted = [c.strip() for c in codes.split(",") if c.strip()]
    mkt = (market or "cn").strip().lower()
    if mkt in ("hk", "us"):
        return _intl_instruments(mkt, wanted, limit)
    if mkt != "cn":
        return {"market": market, "items": [],
                "note": f"未知市场 {market!r}（只认 cn / hk / us）"}

    from app.services import database

    master: dict[str, dict] = {}
    conn = None
    try:
        conn = database.get_conn()
        with conn.cursor() as cur:
            if wanted:
                cur.execute(
                    "SELECT stock_code, name, board FROM company_master WHERE stock_code = ANY(%s)",
                    (wanted,),
                )
                for code, name, board in cur.fetchall():
                    master[code] = {"name": name, "board": board}
                missing = [c for c in wanted if c not in master]
                if missing:
                    cur.execute(
                        "SELECT code, name FROM stock_universe "
                        " WHERE code = ANY(%s) AND market = 'cn'",
                        (missing,),
                    )
                    for code, name in cur.fetchall():
                        master.setdefault(code, {"name": name, "board": None})
            else:
                cur.execute(
                    "SELECT stock_code, name, board FROM company_master LIMIT %s", (limit,)
                )
                for code, name, board in cur.fetchall():
                    master[code] = {"name": name, "board": board}
                cur.execute(
                    "SELECT code, name FROM stock_universe WHERE market = 'cn' LIMIT %s", (limit,)
                )
                for code, name in cur.fetchall():
                    master.setdefault(code, {"name": name, "board": None})
    except Exception as exc:  # noqa: BLE001 —— master 读不到就返回空集，调用方如实处理
        logger.warning("[internal.fin] 读 master 数据失败：{}", exc)
        return {"market": market, "items": [], "error": f"master 数据不可读：{exc}"}
    finally:
        if conn is not None:
            conn.close()

    targets = wanted or sorted(master.keys())[:limit]

    # ST 判定必须走能给出**真实证券简称**的通道（拍板 2026-10-02 §七）：
    # hunter 网关的 `name` 就是代码本身、`company_master` 只有 300 行，两者都
    # 判不出 ST。这里统一从腾讯 `qt.gtimg.cn` 批量取简称（免 key、全市场覆盖），
    # 取不到的那只 `available=false`（零容忍，绝不猜）。
    # 取数失败不阻断整条链路：拿不到简称的标的自然落到「拒绝该标的」。
    quote_names: dict[str, str] = {}
    if targets:
        try:
            quote_names = fin_data.tencent_names(targets)
        except fin_data.TencentWafBlocked as exc:
            logger.warning("[internal.fin] ST 名称通道被 WAF 拦下，本批按「判不出」处理：{}", exc)

    items = [
        fin_data.instrument(code, name=(master.get(code) or {}).get("name"),
                            board=(master.get(code) or {}).get("board"),
                            st_name=quote_names.get(code),
                            require_st_name=True)
        for code in targets
    ]
    return {"market": market, "items": items, "source_count": len(master),
            "st_name_source": "tencent-qt"}


# ── 港美股标的（N3）────────────────────────────────────────────────────────

def _universe_codes(market: str, limit: int) -> list[str]:
    """该市场的标的清单：`stock_universe`（线上有）→ 港股再回落港交所官方清单。

    美股没有本地清单可回落 —— 返回空由调用方如实说明（不猜代码）。
    """
    from app.services import database

    out: list[str] = []
    conn = None
    try:
        conn = database.get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT code FROM stock_universe WHERE market = %s ORDER BY code LIMIT %s",
                (market, limit),
            )
            for row in cur.fetchall():
                code = row.get("code") if isinstance(row, dict) else row[0]
                if code:
                    out.append(str(code).strip())
    except Exception as exc:  # noqa: BLE001 —— 清单读不到就返回空，调用方如实处理
        logger.warning("[internal.fin] 读 stock_universe({}) 失败：{}", market, exc)
    finally:
        if conn is not None:
            conn.close()

    if out or market != "hk":
        return out
    # 港股：线上的 stock_universe 为空时，回落港交所官方清单（仓库自带、零网络）。
    return fin_data.hk_universe_codes(limit)


def _intl_instruments(market: str, wanted: list[str], limit: int) -> dict:
    """港 / 美标的元数据。每手股数与交易所**必须有来源**，判不出该条 `available=false`。"""
    codes = list(wanted)
    universe = "codes"
    if not codes:
        codes = _universe_codes(market, limit)
        universe = "stock_universe" if codes else "none"
        if not codes and market == "hk":
            universe = "hkex_official_csv"
    if not codes:
        return {
            "market": market,
            "items": [],
            "source_count": 0,
            "universe": "none",
            "note": (f"没有可用的 {market.upper()} 标的清单（stock_universe 里没有该市场的行，"
                     "且未指定 codes）—— 传 codes 可显式同步"),
        }
    items = [fin_data.instrument_intl(c, market) for c in codes]
    return {
        "market": market,
        "items": items,
        "source_count": len(codes),
        "universe": universe,
        "lot_source": "hkex_listofsecurities" if market == "hk" else "us_lot_is_1",
    }


# ── DataSnapshot 对象（L03 · 技术方案 §10.2 / §10.1）────────────────────────
#
# 两个入口照 §10.1 的工具名：`data.snapshot_create`（POST）/ `data.snapshot_get`（GET）。
# 落在已有内网数据面（同一个 `X-Hunter-Internal-Key`），不另起一套。
#
# ⛔ 红线 5：拿不到真值的列一律 `NULL`（`revision_id` / `available_at` / `artifact_ref`）——
#    服务层不设默认值；`available_at` 若落在 now() 附近会被 `reject_forged_available_at` 拒掉。

class DataSnapshotIn(BaseModel):
    """`data.snapshot_create` 请求体。**只有必需的三项要必填，其余缺省即 `NULL`。**"""

    data_cutoff_at: str = ""            # ISO8601，带时区（数据截止时间）
    source: str = ""
    quality: str = "ok"
    revision_id: Optional[str] = None   # 数据源不给 → 省略
    artifact_ref: Optional[str] = None  # 没有落盘产物 → 省略
    available_at: Optional[str] = None  # 拿不到 → 省略（**不要填当前时间**）
    market: Optional[str] = None
    code: Optional[str] = None
    note: Optional[str] = None


def _parse_dt(value: Optional[str], field: str) -> Optional[datetime]:
    """ISO8601 → 带时区 datetime。空串 / None → None。非法 → 400（不猜）。"""
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, f"{field} 不是合法的 ISO8601 时间：{value!r}")
    if dt.tzinfo is None:
        raise HTTPException(400, f"{field} 必须带时区（TIMESTAMPTZ），不接受裸本地时间")
    return dt


@router.post("/data/snapshot")
def data_snapshot_create(request: Request, body: DataSnapshotIn) -> dict:
    """`data.snapshot_create`：落一张**不可变** `DataSnapshot`，返回落库后的行。

    只 `INSERT`（改 / 删由 `0048` 的触发器抛异常挡下）。`data_cutoff_at` / `source` 必填。
    """
    _auth(request)
    conn = ds_svc.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            row = ds_svc.create(
                cur,
                data_cutoff_at=_parse_dt(body.data_cutoff_at, "data_cutoff_at"),
                source=body.source,
                quality=body.quality,
                revision_id=body.revision_id,
                artifact_ref=body.artifact_ref,
                available_at=_parse_dt(body.available_at, "available_at"),
                market=body.market,
                code=body.code,
                note=body.note,
            )
        conn.commit()
    except ds_svc.SnapshotError as exc:
        conn.rollback()
        raise HTTPException(400, str(exc))
    finally:
        conn.close()
    return row


@router.get("/data/snapshot/{data_snapshot_id}")
def data_snapshot_get(request: Request, data_snapshot_id: str) -> dict:
    """`data.snapshot_get`：按编号取一张快照；没有 → 404。"""
    _auth(request)
    conn = ds_svc.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            row = ds_svc.get(cur, data_snapshot_id)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, f"没有这张数据快照：{data_snapshot_id}")
    return row


# ── 采集补齐（五期 L09 · 新闻 / 基本面 / 情绪）──────────────────────────────
#
# 三条链路都挂**已有**内网数据面（同一个 `X-Hunter-Internal-Key`），不另起一套：
#   · `POST /internal/fin/collect/news`         —— 新闻接进 fin 侧定时采集
#   · `POST /internal/fin/collect/fundamental`  —— 基本面接进 fin 侧定时采集
#   · `POST /internal/fin/sentiment`            —— 情绪**登记口**（写）
#   · `GET  /internal/fin/sentiment/{code}`     —— 读回；`GET /internal/fin/sentiment` = 新鲜度
#
# **调度权威仍是 Temporal**（`fin-worker` 的 `fin.news_collect` / `fin.fundamental_collect`
# 工作流打这里）。**数据源不通 → 记缺口（`fin_data_gap`），不写假行**（红线 5）。

class CollectNewsIn(BaseModel):
    market: str = "cn"
    limit: int = 30
    per_code: int = 20
    codes: Optional[list[str]] = None     # 显式标的（排障 / 测试）；省略 = 自选股 ∪ 系统标的


@router.post("/collect/news")
def collect_news(request: Request, body: CollectNewsIn) -> dict:
    """抓该市场的标的新闻进 `news` 表（增量、去重）。**数据源不通 → 记缺口，不写假行。**"""
    _auth(request)
    from app.services.fin import collection as coll
    return coll.collect_news(body.market, limit=body.limit, codes=body.codes,
                             per_code=body.per_code)


class CollectFundamentalIn(BaseModel):
    market: str = "cn"
    limit: int = 50
    keep_raw: bool = False
    codes: Optional[list[str]] = None


@router.post("/collect/fundamental")
def collect_fundamental(request: Request, body: CollectFundamentalIn) -> dict:
    """抓该市场标的的财报进 `financial_metric`。**只有 A 股有已接数据源**（见服务模块）。"""
    _auth(request)
    from app.services.fin import collection as coll
    return coll.collect_fundamentals(body.market, limit=body.limit, codes=body.codes,
                                     keep_raw=body.keep_raw)


class SentimentIn(BaseModel):
    """情绪登记口请求体。必填：`code` / `as_of` / `model_version` / `quality` / `source` /
    `generated_at`（§6.2）；`sentiment` 拿不到就省略（→ `NULL`，**别填 0**）。"""

    code: str = ""
    market: Optional[str] = None
    as_of: str = ""
    sentiment: Optional[float] = None
    sentiment_label: Optional[str] = None
    model_version: str = ""
    evidence_ref: Optional[str] = None
    generated_at: str = ""
    quality: str = ""
    source: str = ""
    note: Optional[str] = None


@router.post("/sentiment")
def sentiment_register(request: Request, body: SentimentIn) -> dict:
    """登记一条情绪（**只追加 · 留痕**）。入参不合法 → 400（不猜、不填默认值）。"""
    _auth(request)
    from app.services.fin import sentiment as sent
    try:
        return sent.register_sentiment(body.model_dump())
    except sent.SentimentError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/sentiment")
def sentiment_freshness(request: Request, limit: int = Query(1, ge=1, le=50)) -> dict:
    """情绪表新鲜度读数（行数 + 最新一行）。**没有行就如实返回 0 / null。**"""
    _auth(request)
    from app.services.fin import sentiment as sent
    return sent.freshness(limit=limit)


@router.get("/sentiment/{code}")
def sentiment_read(request: Request, code: str, limit: int = Query(20, ge=1, le=500)) -> dict:
    """按标的读回最近的情绪（`as_of` 倒序）。"""
    _auth(request)
    from app.services.fin import sentiment as sent
    try:
        rows = sent.read_sentiment(code, limit=limit)
    except sent.SentimentError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"code": code, "count": len(rows), "items": rows}
