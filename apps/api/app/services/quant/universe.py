"""E-4 · universe 解析 · 从 index_component 表拉真成分股
(Phase E · 2026-08-18)

数据流:
- 首次:AKShare `index_stock_cons_csindex(symbol='000300')` → seed index_component
- 每月:APScheduler 1 号 09:00 CST · reconcile diff · 加新 · 关闭旧
- 平时:strategy_engine._resolve_universe → _query_index_current(index_code)
"""
from __future__ import annotations

import logging
from datetime import date

from app.services.database import get_conn

log = logging.getLogger(__name__)


INDEX_MAP = {
    "hs300":  ("000300", "沪深 300"),
    "zz500":  ("000905", "中证 500"),
    "zz1000": ("000852", "中证 1000"),
}


def query_current(index_code: str) -> list[str]:
    """当前成分股 · effective_to IS NULL

    同 `query_active_at`:表不存在时返回空而不是抛 —— 让 `resolve()` 的
    fallback 分支能真的兜住。
    """
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            """SELECT stock_code FROM index_component
               WHERE index_code=%s AND effective_to IS NULL
               ORDER BY stock_code""",
            (index_code,),
        )
        codes = [r[0] for r in cur.fetchall()]
    except Exception as e:                                    # noqa: BLE001
        log.warning("[universe] 查当前成分失败(回落到 stocks 表): %s", e)
        codes = []
    finally:
        cur.close(); conn.close()
    return codes


def query_active_at(index_code: str, on_date: date) -> list[str]:
    """指定日期的成分股(生存者偏差防治)· effective_from <= on_date AND (to IS NULL OR to > on_date)

    **表不存在时返回空而不是抛异常。** `resolve()` 的注释写的是
    「未 seed 时 fallback」,但它只处理"查到空",不处理"表都还没建" ——
    于是任何没跑 `sql/20260818_index_component.sql` 的部署,
    一跑回测就 500(psycopg2.errors.UndefinedTable)。实测踩到。

    开源版用户 clone 下来第一次跑就会撞上这个,而错误信息是一句
    SQL 报错,他不会想到是缺一个迁移。
    """
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            """SELECT stock_code FROM index_component
               WHERE index_code=%s
                 AND effective_from <= %s
                 AND (effective_to IS NULL OR effective_to > %s)
               ORDER BY stock_code""",
            (index_code, on_date, on_date),
        )
        codes = [r[0] for r in cur.fetchall()]
    except Exception as e:                                    # noqa: BLE001
        log.warning("[universe] 查历史成分失败(回落到 stocks 表): %s", e)
        codes = []
    finally:
        cur.close(); conn.close()

    # ── 没有那一天的历史快照 → 回落到当前成分股 ──────────────
    #
    # 我们的 index_component 只有一个快照(首次 seed 那天)。
    # 回测按调仓日逐期回查历史成分,于是**一年前的调仓日查到 0 只** ——
    # 整个回测报 empty_universe,而表里明明有 300 行。
    #
    # 严格做法是攒够历史快照,但那要等时间。在此之前:
    #   回落到当前成分股,**并让调用方知道这份结果有生存者偏差**
    #   (用今天还在指数里的股票去回测过去,天然剔掉了退市和被调出的)
    #
    # **回落而不是报错**的理由:报错的话整个回测功能不可用;
    # 回落之后结果可用,只是要标注偏差。而**不标注地回落**是最糟的 ——
    # 用户会以为这是无偏的回测结果。标注在 backtest_engine 的 warn 字段里。
    if not codes:
        codes = query_current(index_code)
        if codes:
            log.info("[universe] %s 在 %s 无历史快照 · 回落当前成分股 %d 只 "
                     "(存在生存者偏差)", index_code, on_date, len(codes))
    return codes


def _cons_direct(index_code: str) -> list[str]:
    """直连 AKShare 拉成分股。慢(沪深300 实测 100+ 秒),但不依赖任何人。

    带硬超时:akshare 底层的 requests 没有超时,卡住就是永久卡住,
    而它是在 seed 流程里被调的 —— 卡住的表现是整个启动流程不动。
    """
    import concurrent.futures as cf
    import warnings
    try:
        warnings.filterwarnings("ignore")
        import akshare as ak
    except Exception as e:                                    # noqa: BLE001
        log.warning("[universe] akshare 不可用: %s", e)
        return []

    def _do():
        return ak.index_stock_cons_csindex(symbol=index_code)

    ex = cf.ThreadPoolExecutor(max_workers=1)
    try:
        df = ex.submit(_do).result(timeout=300)
        ex.shutdown(wait=False)
    except Exception as e:                                    # noqa: BLE001
        log.warning("[universe] 直连拉 %s 成分股失败: %s", index_code, type(e).__name__)
        return []
    try:
        recs = df.to_dict("records")
    except Exception:                                         # noqa: BLE001
        return []
    return _pick_codes(recs, index_code)


def _pick_codes(rows: list[dict], index_code: str) -> list[str]:
    """从返回记录里取出股票代码列。**找不到就返回空并报错,不猜** ——
    猜错了会把指数代码当成分股写进去,而那种错要等回测选出一堆
    不存在的票才会暴露。"""
    if not rows:
        return []
    key = next((k for k in ("成分券代码", "code", "constituent_code", "stock_code")
                if k in rows[0]), None)
    if not key:
        log.error("[universe] %s 找不到 code 列 · keys=%s", index_code, list(rows[0])[:10])
        return []
    return [str(x[key]).zfill(6) for x in rows if x.get(key)]


def _fetch_cons(index_code: str) -> list[str]:
    """拉指数成分股 —— **先直连 AKShare,代理只在用户显式配置时用**。

    原来这里只走我们自己的 AK 代理(139.199.221.232),地址和 token 都写死在
    默认值里 —— 开源用户装完就在用我们的服务器,不知情也没法不用。

    那条「容器直连会 RemoteDisconnected」的结论是在**生产 GCP 服务器**上得出的,
    海外 IP 被限。本地中国网络实测(2026-08-21):
    `index_stock_cons_csindex` 直连成功,300 行。所以代理对开源用户不是必需的。

    拿不到返回空列表 —— 上层据此跳过,而不是把空当成"这个指数没有成分股"
    写进库(那会把整个股票池清空)。
    """
    import os
    import requests

    # ① 直连 AKShare(中证指数官网源)
    rows = _cons_direct(index_code)
    if rows:
        return rows

    # ② 代理 —— 没配 AK_PROXY_URL 就到此为止,不再默认连我们的服务器
    base = os.getenv("AK_PROXY_URL", "").rstrip("/")
    token = os.getenv("AK_API_TOKEN", "")
    if not base:
        log.warning("[universe] %s 直连拿不到成分股,且未配置 AK_PROXY_URL", index_code)
        return []
    try:
        r = requests.post(
            f"{base}/call",
            json={"func": "index_stock_cons_csindex", "kwargs": {"symbol": index_code}},
            # 300 秒:代理那边是同步调 AKShare,沪深300 的 300 只实测要 100+ 秒,
            # 偶尔更久。超时太短的表现是"有时成功有时 seed 0 行",
            # 而 seed 0 行不报错,只是股票池悄悄回落到 stocks 表
            headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=300,
        )
        if r.status_code != 200:
            log.error("[universe] 代理拉 %s 失败 HTTP %s: %s",
                      index_code, r.status_code, r.text[:200])
            return []
        d = r.json()
    except Exception as e:                                    # noqa: BLE001
        log.error("[universe] 拉 %s 失败: %s", index_code, e)
        return []

    rows = d if isinstance(d, list) else (d.get("data") or d.get("records") or [])
    return _pick_codes(rows, index_code)


def seed_current(index_code: str) -> int:
    """拉当前成分 · 首次填 index_component · 返回入库行数"""
    codes = _fetch_cons(index_code)
    if not codes:
        return 0

    conn = get_conn(); cur = conn.cursor()
    n = 0
    for c in codes:
        cur.execute(
            """INSERT INTO index_component (index_code, stock_code, effective_from)
               VALUES (%s, %s, %s)
               ON CONFLICT (index_code, stock_code, effective_from) DO NOTHING""",
            (index_code, c, date.today()),
        )
        if cur.rowcount > 0:
            n += 1
    conn.commit(); cur.close(); conn.close()
    log.info(f"[universe] seeded {index_code} · +{n} rows")
    return n


def reconcile_current(index_code: str) -> dict:
    """diff 当前 vs AKShare 最新 · 加入变动 · 关闭旧
    返回 {added: [...], removed: [...]}
    """
    # 与 seed_current 共用 _fetch_cons —— 两处各写一份取数逻辑,
    # 改了一处忘另一处就会出现"首次能 seed、每月 reconcile 却一直失败"
    codes = _fetch_cons(index_code)
    if not codes:
        return {"error": "fetch_failed_or_empty", "index": index_code}
    new_set = set(codes)
    current = set(query_current(index_code))
    added = new_set - current
    removed = current - new_set

    # ⚠️ **拉到的成分数明显偏少时不动库**。
    # 上游偶发返回半截数据(比如只回 30 只),照单执行会把另外 270 只
    # 全部 effective_to=今天 —— 等于一次性伪造一场"指数大调整",
    # 而历史成分表是不可逆的:下个月 reconcile 会以为它们本来就不在。
    if current and len(new_set) < len(current) * 0.7:
        log.error("[universe] %s 只拉到 %d 只(库里 %d 只)· 疑似上游返回不全 · 本次不更新",
                  index_code, len(new_set), len(current))
        return {"error": "suspicious_shrink", "index": index_code,
                "fetched": len(new_set), "current": len(current)}
    today = date.today()

    conn = get_conn(); cur = conn.cursor()
    for c in added:
        cur.execute(
            """INSERT INTO index_component (index_code, stock_code, effective_from)
               VALUES (%s, %s, %s)
               ON CONFLICT (index_code, stock_code, effective_from) DO NOTHING""",
            (index_code, c, today),
        )
    for c in removed:
        cur.execute(
            """UPDATE index_component SET effective_to=%s
               WHERE index_code=%s AND stock_code=%s AND effective_to IS NULL""",
            (today, index_code, c),
        )
    conn.commit(); cur.close(); conn.close()
    log.info(f"[universe] reconcile {index_code} · +{len(added)} -{len(removed)}")
    return {"added": sorted(added), "removed": sorted(removed)}


# 目前**真正有数据支撑**的股票池。
#
# 之前 UI 给了六个选项(沪深300/中证500/沪深800/全A股/港股通/我的自选),
# 而后端只有前两个是真的 —— 其余四个都悄悄回落到 `stocks` 表的 301 只 A 股。
# 实测:选「港股通」返回的是 A 股,选「全A股」返回 301 只(全 A 有 5000+),
# 选「我的自选」也返回那 301 只,根本没读用户的自选。
#
# 而回落是**不吭声**的:用户以为自己在测全市场,实际测的是那 301 只。
# 这和回测空仓时照样出成绩单是同一类问题 —— 宁可说"这个池子还没有数据",
# 也不要给一份看不出是错的结果。
SUPPORTED_UNIVERSES = {"hs300", "zz500", "my_watchlist"}

# 定时任务和回填要覆盖的全集 —— **必须和 SUPPORTED_UNIVERSES 对得上**。
#
# 之前所有任务都写死 `query_current("000300")`,只给沪深300 算因子。
# 于是"中证500"这个选项:成分股是真的 500 只,但一只因子数据都没有,
# 用户选了得到 0 结果。池子是真的、因子是空的,比池子假的更难查。
#
# 「我的自选」不在这里:用户加的票可能是任意 A 股,不可能提前全算 ——
# 那种情况下 UI 要能说"你的自选里有 N 只还没有因子数据"。
COVERED_INDEXES = ["000300", "000905"]


def covered_codes() -> list[str]:
    """定时任务该更新哪些票 —— **以用户下过什么为准**。

    原来这里返回沪深300 ∪ 中证500(写死的 800 只),也就是说不管用户
    下没下、想不想要,定时任务每天都去更新这 800 只。老板的意见是
    「用户都不知道你就占用他的资源」—— 写死范围和开机自动跑是同一个毛病。

    现在读 `data_coverage`:用户在「数据」页下过哪些,就更新哪些。
    一只都没下过的实例,这里返回空列表,定时任务什么都不做 ——
    **这才是正确的默认行为**。
    """
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT DISTINCT code FROM data_coverage WHERE data_type='kline'")
        return [r[0] for r in cur.fetchall()]
    except Exception as e:                                    # noqa: BLE001
        log.warning("[universe] 读 data_coverage 失败: %s", e)
        return []
    finally:
        cur.close(); conn.close()


def covered_codes_financial() -> list[str]:
    """有财报覆盖的票 —— 每周任务只重算这些的基本面因子。"""
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT DISTINCT code FROM data_coverage WHERE data_type='financial'")
        return [r[0] for r in cur.fetchall()]
    except Exception as e:                                    # noqa: BLE001
        log.warning("[universe] 读 data_coverage(financial) 失败: %s", e)
        return []
    finally:
        cur.close(); conn.close()


def resolve(universe_key: str, on_date: date | None = None, user_id: str | None = None) -> list[str]:
    """统一入口 · **不支持的池子返回空,不静默回落**。

    · hs300 / zz500  从 index_component 拉
    · my_watchlist   从 stocks WHERE user_id 拉(用户在对话页「⭐ 自选股」加的)
    · 其余           返回 [],由调用方告诉用户"这个池子还没有数据"
    """
    # 前端历史上传过 my_watchlist,而这里判断的是 my_watch —— 永远匹配不上,
    # 于是"我的自选"一路走到兜底,返回全部 A 股。两个都认。
    if universe_key in ("my_watchlist", "my_watch"):
        if not user_id:
            log.warning("[universe] 我的自选需要登录用户")
            return []
        conn = get_conn(); cur = conn.cursor()
        try:
            cur.execute(
                "SELECT code FROM stocks WHERE user_id=%s AND enabled AND market='A'",
                (str(user_id),))
            return [r[0] for r in cur.fetchall()]
        finally:
            cur.close(); conn.close()

    if universe_key in INDEX_MAP:
        index_code, _ = INDEX_MAP[universe_key]
        codes = query_active_at(index_code, on_date) if on_date else query_current(index_code)
        if codes:
            return codes
        log.warning("[universe] %s 的成分股还没 seed", universe_key)
        return []

    log.warning("[universe] 不支持的股票池 %s(支持:%s)",
                universe_key, sorted(SUPPORTED_UNIVERSES))
    return []


def watchlist_gaps(codes: list[str]) -> list[str]:
    """自选里哪些票**还没有因子数据**。

    加自选时会按需补(on_demand.ensure_stock),但补失败的、
    这个功能上线之前就加进去的、以及新股历史不够算不出因子的,
    都会留在这里。不列出来的话用户只看到"10 只选出 3 只"。
    """
    if not codes:
        return []
    from app.services.quant.on_demand import has_factor_data
    have = has_factor_data(codes)
    return [c for c in codes if c not in have]


def describe_universe(universe_key: str, n: int, user_id: str | None = None) -> str:
    """池子为空时,**说清楚是为什么** —— 用户看到"选不出股票"时
    要能分辨是自选还没加、是池子不支持,还是成分股没 seed。"""
    if n:
        return ""
    if universe_key in ("my_watchlist", "my_watch"):
        # 文案指路必须跟着导航走:顶栏「自选」入口已在 2026-08-30 导航重构里删掉,
        # 现在自选股是对话页左侧的第二个 tab。指向一个不存在的入口比不指路更糟。
        return ("「我的自选」是空的 —— 先到对话页左侧「⭐ 自选股」加几只股票,"
                "或者换成沪深 300 / 中证 500。") if user_id else "「我的自选」需要先登录。"
    if universe_key in SUPPORTED_UNIVERSES:
        return f"{universe_key} 的成分股还没有同步 —— 稍后再试。"
    return (f"暂不支持「{universe_key}」这个股票池。"
            f"目前支持:沪深 300、中证 500、我的自选。")


def quality_at(universe_key: str, on_date: date) -> dict:
    """这个日期的股票池**成色**怎么样(`_17` §5)。

    `query_active_at()` 按 effective_from <= on_date 查历史成分,逻辑是对的。
    但 Phase E 的设计是「首次 seed 时 effective_from = today,之后每月累积变更」
    (`15_phase-e` §3.3)—— 所以**未来一到两年内查任何历史日期都返回空**,
    回落到 stocks 表(= 今天还活着的股票)。

    也就是说 `01` §10.3 要求的「不用今日成分套 3 年前」,现在恰恰在这么做。

    这不是 bug,是设计的必然阶段。但用户看不出来 —— 他拿到一个漂亮的回测,
    不知道里面藏着幸存者偏差。**让他知道成色,比修好它更急。**

    返回给回测结果带上,前端据此显示一句提示。
    """
    if universe_key not in INDEX_MAP:
        # 自选股/全 A —— 本来就没有历史成分的概念,不存在这个问题
        return {"survivorship_ok": True, "source": "stocks",
                "note": ""}

    index_code, label = INDEX_MAP[universe_key]
    hist = query_active_at(index_code, on_date)
    if hist:
        return {"survivorship_ok": True, "source": "index_component",
                "count": len(hist), "note": ""}

    cur_codes = query_current(index_code)
    return {
        "survivorship_ok": False,
        "source": "stocks_fallback",
        "count": len(cur_codes),
        "note": (
            f"{label} 在 {on_date} 没有历史成分记录,用的是**当前**股票池 —— "
            f"退市和被调出的股票不在里面,回测收益会偏高(幸存者偏差)。"
            f"历史成分表从 2026-08 起按月累积,届时这条会自动消失。"
        ),
    }
