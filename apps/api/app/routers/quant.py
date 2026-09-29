"""量化策略 API · Phase A
(见 doc/开源hunter-community/参考/11量化策略/quant-strategy-tech-plan.md §7)

6 端点(MVP):
  GET  /quant/factors                  · 因子清单
  POST /quant/scan                     · 按策略打分选 Top N(实时 · 快)
  GET  /quant/strategies/official      · 官方策略列表
  GET  /quant/strategies/mine          · 我的策略(简版 · community 单用户)
  POST /quant/strategies               · 创建策略
  POST /quant/backtest/run             · 同步跑回测(v1 简版 · 不用异步)
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from fastapi.responses import Response
from pydantic import BaseModel

from app.services.database import get_conn
from app.services.quant import factor_defs, strategy_engine, backtest_engine, factor_engine
from app.services.quant import screen_source
from app.services.quant.screen_dsl import ScreenError

try:
    from psycopg2.extras import execute_values
except Exception:  # pragma: no cover · psycopg2 缺失时逐笔落库降级为空
    execute_values = None


# 阶段 4 · 逐笔明细字段(与 backtest_trade 表列一一对应)
_TRADE_COLS = [
    "trade_date", "code", "side", "shares", "price", "turnover",
    "commission", "stamp_tax", "slippage", "other", "total_cost",
    "net_pnl", "slippage_model", "impact_bps_actual",
    "adv_20d", "order_value_to_adv_ratio",
]


def _persist_trades(cur, result_id: int, trades: list[dict]) -> int:
    """把回测返回的逐笔 trades 批量写入 backtest_trade · 返写入笔数。

    先删旧记录(ON CONFLICT 覆盖 result 时逐笔也要跟着刷新)· 再批量插。
    trades 为空或 execute_values 不可用时安静跳过(不阻断主流程)。
    """
    cur.execute("DELETE FROM backtest_trade WHERE result_id=%s", (result_id,))
    if not trades or execute_values is None:
        return 0
    rows = [
        [result_id] + [t.get(c) for c in _TRADE_COLS]
        for t in trades
    ]
    execute_values(
        cur,
        "INSERT INTO backtest_trade (result_id, " + ", ".join(_TRADE_COLS) + ") VALUES %s",
        rows,
    )
    return len(rows)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/quant", tags=["quant"])


# ═══════════════════════════════════════════════════════════════
# GET /factors
# ═══════════════════════════════════════════════════════════════

@router.get("/factors")
async def list_factors():
    """因子清单 · 带**这个因子到底有没有数据**。

    只报 enabled 是不够的:20 个因子里 enabled 的有 18 个,而
    `factor_value` 表里真正有数据的只有 3 个。用户在界面上看不出区别,
    随手选 5 个全是空的,回测就选不出票 —— 这正是 B1 的上游成因。

    `has_data` / `latest` 让前端能把没数据的标灰。
    """
    from app.services.database import get_conn
    stats: dict[str, tuple] = {}
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("""SELECT factor_key, count(*), max(trade_date),
                              count(DISTINCT code)
                         FROM factor_value GROUP BY factor_key""")
        stats = {r[0]: r for r in cur.fetchall()}
        cur.close(); conn.close()
    except Exception as e:                                    # noqa: BLE001
        # 查不到统计不该让整个因子列表 500 —— 那样界面直接白屏。
        # 降级成"不知道有没有数据",而不是假装都有
        log.warning("[quant] 因子数据统计查询失败: %s", e)
        stats = {}

    return {
        "factors": [
            {
                "key": f.key, "cat": f.cat, "name": f.name,
                "icon": f.icon, "desc": f.desc,
                "reverse": f.reverse, "enabled": f.enabled,
                "offline_reason": f.offline_reason,
                "has_data": f.key in stats,
                "rows": stats[f.key][1] if f.key in stats else 0,
                "codes": stats[f.key][3] if f.key in stats else 0,
                "latest": stats[f.key][2].isoformat() if f.key in stats else None,
                # 可调参数(RSI 周期/超买超卖线之类)· 没有就是空数组。
                # 评委问"你这个 RSI 实际参数是多少"要能在界面上直接指给他看,
                # 光有权重滑块答不出这个问题。
                "params": factor_engine.FACTOR_PARAMS.get(f.key, []),
            } for f in factor_defs.ALL_FACTORS
        ],
        "cat_order": factor_defs.CAT_ORDER,
        "enabled_count": len(factor_defs.enabled_factors()),
        "with_data_count": sum(1 for f in factor_defs.ALL_FACTORS if f.key in stats),
    }


# ═══════════════════════════════════════════════════════════════
# E-6 · 券商 interface(dry run · 未真接入)
# ═══════════════════════════════════════════════════════════════

class OrderIn(BaseModel):
    code: str
    side: str        # buy / sell
    qty: int
    price: float | None = None
    price_type: str = "market"


# broker 单例 · 内存持久(pm2 fork 单进程可用 · 重启清空)
_broker_instances: dict[str, object] = {}


def _get_broker(name: str = "dryrun"):
    if name not in _broker_instances:
        from app.services.quant.broker import get_broker
        _broker_instances[name] = get_broker(name)
    return _broker_instances[name]


@router.post("/broker/submit")
async def broker_submit(body: OrderIn, request: Request):
    """提交订单(当前只走 DryRunBroker · 不真下单)
    · Phase F 加入实际券商时 · 用户传 broker='xtp' 等
    """
    from app.services.quant.broker import Order
    b = _get_broker("dryrun")
    st = b.submit_order(Order(code=body.code, side=body.side, qty=body.qty,
                              price=body.price, price_type=body.price_type))
    return {"broker": b.name, "status": st.__dict__}


@router.get("/broker/positions")
async def broker_positions():
    b = _get_broker("dryrun")
    return {"broker": b.name, "positions": b.query_positions()}


@router.get("/broker/balance")
async def broker_balance():
    b = _get_broker("dryrun")
    return {"broker": b.name, "balance": b.query_balance()}


@router.get("/broker/presets")
async def broker_presets():
    """复赛 §3.B · 三市场默认 broker 参数(A股/港股/美股/零成本对比)
    · 供前端 backtest 页在跑之前让用户选一档
    · 结果里再展示"如果换另一档,毛/净差多少"
    """
    from app.services.quant.broker import defaults as _bd
    return {"presets": _bd.list_presets(), "default": _bd.DEFAULT_PRESET_KEY}


# ═══════════════════════════════════════════════════════════════
# E-1 · Prometheus metrics · 生产可观测
# ═══════════════════════════════════════════════════════════════

@router.get("/metrics")
async def prometheus_metrics():
    """Prometheus text format · Grafana 直接抓
    · factor_value 各因子 24h 新增
    · backtest_result 24h 新增
    · 公开策略数
    · factor_ic 覆盖
    """
    conn = get_conn(); cur = conn.cursor()

    cur.execute("""
        SELECT factor_key, COUNT(*) FROM factor_value
        WHERE updated_at >= NOW() - INTERVAL '24 hours'
        GROUP BY factor_key
    """)
    factor_rows = cur.fetchall()

    cur.execute("SELECT COUNT(*) FROM backtest_result WHERE created_at >= NOW() - INTERVAL '24 hours'")
    bt_24h = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM strategy WHERE is_public = TRUE")
    public_strategies = cur.fetchone()[0]

    cur.execute("SELECT COUNT(DISTINCT factor_key), COUNT(*) FROM factor_ic")
    ic_factors, ic_rows = cur.fetchone()

    cur.execute("SELECT COUNT(DISTINCT code) FROM klines WHERE period='daily'")
    kline_codes = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM index_component WHERE effective_to IS NULL")
    idx_current = cur.fetchone()[0]

    cur.close(); conn.close()

    lines = [
        "# HELP quant_factor_value_24h Factor value rows in last 24h (per factor)",
        "# TYPE quant_factor_value_24h gauge",
    ]
    for k, n in factor_rows:
        lines.append(f'quant_factor_value_24h{{factor="{k}"}} {n}')

    lines.extend([
        "# HELP quant_backtest_result_24h Backtest results created in 24h",
        "# TYPE quant_backtest_result_24h gauge",
        f"quant_backtest_result_24h {bt_24h}",
        "# HELP quant_strategies_public Public strategies count",
        "# TYPE quant_strategies_public gauge",
        f"quant_strategies_public {public_strategies}",
        "# HELP quant_factor_ic Factor IC coverage",
        "# TYPE quant_factor_ic gauge",
        f"quant_factor_ic_factors {ic_factors}",
        f"quant_factor_ic_rows {ic_rows}",
        "# HELP quant_klines K-line codes count",
        "# TYPE quant_klines gauge",
        f"quant_klines_codes {kline_codes}",
        "# HELP quant_index_component Current index components (all indexes)",
        "# TYPE quant_index_component gauge",
        f"quant_index_component_current {idx_current}",
    ])
    return Response(content="\n".join(lines) + "\n", media_type="text/plain; charset=utf-8")


# ═══════════════════════════════════════════════════════════════
# E-3 · 因子相关性矩阵
# ═══════════════════════════════════════════════════════════════

class CorrIn(BaseModel):
    factor_keys: list[str]
    universe: str = "hs300"
    method: str = "pearson"


@router.post("/factors/correlation")
async def factor_correlation(body: CorrIn):
    """N × N 因子相关矩阵 · pearson / spearman · 用于 workbench 热力图
    · 至少 2 因子 · 最多 15 · 覆盖 < 10 只时返 error
    """
    if len(body.factor_keys) < 2:
        raise HTTPException(400, "至少 2 个因子")
    if len(body.factor_keys) > 15:
        raise HTTPException(400, "最多 15 个因子(N^2 计算量限制)")
    for k in body.factor_keys:
        if factor_defs.get_factor(k) is None:
            raise HTTPException(404, f"factor {k} not found")
    from app.services.quant import correlation_engine as ce
    return ce.compute_pairwise_corr(body.factor_keys, body.universe, method=body.method)


# ═══════════════════════════════════════════════════════════════
# D-2 · IC 时间序列 + IC 排行
# ═══════════════════════════════════════════════════════════════

@router.get("/factors/{key}/ic")
async def factor_ic_series(
    key: str, universe: str = "hs300", horizon: int = 5, days: int = 60
):
    """近 N 日 IC 时间序列 · 用于因子广场"IC 走势"图"""
    if factor_defs.get_factor(key) is None:
        raise HTTPException(404, f"factor {key} not found")
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """SELECT trade_date, ic, ic_ir FROM factor_ic
           WHERE factor_key=%s AND universe=%s AND horizon_days=%s
             AND trade_date >= %s
           ORDER BY trade_date""",
        (key, universe, horizon, date.today() - timedelta(days=days)),
    )
    rows = cur.fetchall()
    cur.close(); conn.close()
    ic_values = [float(r[1]) for r in rows if r[1] is not None]
    ic_avg = sum(ic_values) / len(ic_values) if ic_values else None
    # 衰减警告:近 30 vs 全历史差异 > 30%
    warning = None
    if len(ic_values) >= 30:
        recent = ic_values[-30:]
        full_avg = ic_avg
        recent_avg = sum(recent) / len(recent)
        if full_avg is not None and abs(full_avg) > 1e-6:
            drift = abs(recent_avg - full_avg) / abs(full_avg)
            if drift > 0.3:
                direction = "增强" if abs(recent_avg) > abs(full_avg) else "衰减"
                warning = f"⚠ 近 30 日 IC 相对全历史{direction} {drift*100:.0f}% · 因子可能进入新阶段"
    return {
        "factor": key,
        "universe": universe,
        "horizon_days": horizon,
        "ic_series": [
            {"date": r[0].isoformat(),
             "ic": float(r[1]) if r[1] is not None else None,
             "ic_ir": float(r[2]) if r[2] is not None else None}
            for r in rows
        ],
        "ic_avg": ic_avg,
        "n_periods": len(rows),
        "warning": warning,
    }


@router.get("/factors/ic-ranking")
async def factor_ic_ranking(universe: str = "hs300", horizon: int = 5, days: int = 60):
    """所有启用因子按近 N 日 |IC 均值| 排序 · 因子广场首屏"""
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """SELECT factor_key, AVG(ic) AS ic_avg, AVG(ic_ir) AS ir_avg, COUNT(*) AS n
           FROM factor_ic
           WHERE universe=%s AND horizon_days=%s AND trade_date >= %s
             AND ic IS NOT NULL
           GROUP BY factor_key
           ORDER BY ABS(AVG(ic)) DESC""",
        (universe, horizon, date.today() - timedelta(days=days)),
    )
    rows = cur.fetchall()
    cur.close(); conn.close()
    return {
        "universe": universe,
        "horizon_days": horizon,
        "period_days": days,
        "ranking": [
            {"factor_key": r[0], "ic_avg": float(r[1]),
             "ic_ir": float(r[2]) if r[2] is not None else None,
             "periods": r[3]}
            for r in rows
        ],
    }


# ═══════════════════════════════════════════════════════════════
# C4.1 · GET /factors/{key}/quantile · 分档收益
# ═══════════════════════════════════════════════════════════════

@router.get("/factors/{key}/quantile")
async def factor_quantile(
    key: str,
    universe: str = "hs300",
    start: str | None = None,
    end: str | None = None,
    n_buckets: int = 10,
):
    """单因子分档年化 · Q1(低 z)→ Qn(高 z)· 用于证明因子有效性
    - Q10 > Q1 且单调 → 因子有效(前端加 ✅ 单调 标签)
    - 起止时间 · 默认近 1 年
    """
    from datetime import datetime as _dt
    d0 = _dt.strptime(start, "%Y-%m-%d").date() if start else None
    d1 = _dt.strptime(end, "%Y-%m-%d").date() if end else None
    if factor_defs.get_factor(key) is None:
        raise HTTPException(404, f"factor {key} not found")
    result = backtest_engine.compute_quantile_returns(
        key, universe, d0, d1, n_buckets=max(2, min(20, n_buckets))
    )
    # 单调性判断:Qn > Q1 且 top 3 avg > bottom 3 avg
    q = result.get("quantiles", {})
    monotonic = False
    q1 = q.get("q1")
    qn = q.get(f"q{result.get('n_buckets', 10)}")
    if q1 is not None and qn is not None and qn > q1:
        top3 = [v for k2, v in list(q.items())[-3:] if v is not None]
        bot3 = [v for k2, v in list(q.items())[:3] if v is not None]
        if top3 and bot3 and sum(top3)/len(top3) > sum(bot3)/len(bot3):
            monotonic = True
    result["monotonic"] = monotonic
    return result


@router.post("/init")
async def trigger_init():
    """手工触发一次数据同步。

    启动时库空会自动跑,但这些情况需要手工:成分股换了一批、
    某次同步跑到一半失败了、或者用户就是想立刻刷新一遍。

    **必须在 API 进程内跑** —— 进度存在进程内存里,
    用 `docker exec` 另起一个 python 跑的话,这边的
    /init-status 永远是空的(实测踩过)。
    """
    import asyncio as _aio
    from app.services.quant import init_state
    if init_state.snapshot()["running"]:
        return {"ok": False, "message": "已经在跑了", **init_state.snapshot()}
    from app.services.quant.scheduler import run_initial_setup
    _aio.create_task(_aio.to_thread(run_initial_setup))
    return {"ok": True, "message": "已开始 · 轮询 /api/quant/init-status 看进度"}


@router.get("/init-status")
async def init_status():
    """首次初始化进度 · 前端轮询这个。

    `needs_init` 是**查库**得出的,不是看"跑没跑过" —— 跑了一半被杀掉的
    情况实测发生过(重建容器把回填进程 SIGKILL 了),那时状态是"跑过"
    而数据依然是空的。
    """
    from app.services.quant import init_state
    s = init_state.snapshot()
    s["needs_init"] = init_state.needs_init()
    return s


# ═══════════════════════════════════════════════════════════════
# POST /scan · 按策略打分 · Top N
# ═══════════════════════════════════════════════════════════════

class ScanIn(BaseModel):
    factors: list[dict]           # [{key, weight_pct}]
    config: dict = {}             # {universe, top_n}
    trade_date: str | None = None


@router.post("/scan")
async def scan(body: ScanIn, request: Request):
    trade_date = date.fromisoformat(body.trade_date) if body.trade_date else date.today()
    uid = getattr(request.state, "user_id", None)
    # D-8 · scan 端点已加白名单 · 无 uid 时 strategy_engine._resolve_universe 自动 fallback hs300
    picks = strategy_engine.score_and_select(
        {"factors": body.factors, "config": body.config or {"top_n": 20, "universe": "hs300"}},
        trade_date, str(uid) if uid else None,
    )
    # 补股票名(前端展示用 · 从 stocks 表拿)
    if picks:
        name_map = strategy_engine.fetch_stock_names([p["code"] for p in picks])
        for p in picks:
            p["name"] = name_map.get(p["code"], p["code"])
        out = {"trade_date": trade_date.isoformat(), "picks": picks}
        # 自选池:选出来的可能只是一部分。**把没参与打分的票说出来** ——
        # 否则用户看到"自选 10 只只出了 3 只"会以为是权重配错了
        cfg = body.config or {}
        if cfg.get("universe") in ("my_watchlist", "my_watch") and uid:
            from app.services.quant import universe as _uv
            pool = _uv.resolve(cfg["universe"], trade_date, str(uid))
            gaps = _uv.watchlist_gaps(pool)
            if gaps:
                out["gaps"] = gaps
                out["gap_note"] = (f"自选 {len(pool)} 只里有 {len(gaps)} 只还没有因子数据"
                                   f",没参与打分:{'、'.join(gaps[:8])}"
                                   + ("…" if len(gaps) > 8 else ""))
        return out

    # 一只都没选出来时,**说清楚是哪一种空**。「股票池是空的」和
    # 「因子没数据」用户要做的事完全不同:前者去加自选或换池子,
    # 后者换因子。原来两种都只显示"无匹配",他只能瞎猜
    from app.services.quant import universe as _uv
    cfg = body.config or {}
    ukey = cfg.get("universe", "hs300")
    pool = _uv.resolve(ukey, trade_date, str(uid) if uid else None)
    if not pool:
        return {"trade_date": trade_date.isoformat(), "picks": [],
                "reason": _uv.describe_universe(ukey, 0, str(uid) if uid else None)}
    keys = [f["key"] for f in body.factors if f.get("weight_pct", 0) > 0]
    from app.services.quant.market import market_of_universe
    rep = backtest_engine.factor_data_report(keys, trade_date - timedelta(days=45), trade_date,
                                             market=market_of_universe(ukey))
    miss = [f["name"] for f in rep if not f["ok"]]
    return {"trade_date": trade_date.isoformat(), "picks": [],
            "reason": (f"股票池有 {len(pool)} 只,但这些因子没有数据:"
                       + "、".join(miss)) if miss else
                      f"股票池有 {len(pool)} 只,但没有一只满足因子覆盖要求"}


# ═══════════════════════════════════════════════════════════════
# 策略 CRUD(简版)
# ═══════════════════════════════════════════════════════════════

@router.get("/strategies/official")
async def list_official():
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """SELECT id, name, description, factors, config
           FROM strategy WHERE is_official = TRUE
           ORDER BY id"""
    )
    rows = cur.fetchall()
    cur.close(); conn.close()
    return {"strategies": [
        {"id": r[0], "name": r[1], "description": r[2], "factors": r[3], "config": r[4]}
        for r in rows
    ]}


@router.get("/strategies/mine")
async def list_mine(request: Request):
    uid = getattr(request.state, "user_id", None)
    if not uid:
        # community 单用户模式兜底 · 用固定 user_id
        uid = "46066ca9-bf34-4fad-a9d5-bda5beb74c11"
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """SELECT id, name, description, factors, config, created_at
           FROM strategy WHERE user_id = %s AND NOT is_official
           ORDER BY updated_at DESC""",
        (str(uid),),
    )
    rows = cur.fetchall()
    cur.close(); conn.close()
    return {"strategies": [
        {"id": r[0], "name": r[1], "description": r[2],
         "factors": r[3], "config": r[4], "created_at": r[5].isoformat()}
        for r in rows
    ]}


class StrategyIn(BaseModel):
    name: str
    description: str = ""
    factors: list[dict]
    config: dict


@router.post("/strategies")
async def create_strategy(body: StrategyIn, request: Request):
    uid = getattr(request.state, "user_id", None)
    if not uid:
        uid = "46066ca9-bf34-4fad-a9d5-bda5beb74c11"
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """INSERT INTO strategy (user_id, name, description, factors, config)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (str(uid), body.name, body.description, json.dumps(body.factors), json.dumps(body.config)),
    )
    new_id = cur.fetchone()[0]
    conn.commit(); cur.close(); conn.close()
    return {"id": new_id}


# ═══════════════════════════════════════════════════════════════
# C5 · 社区分享 + fork + leaderboard
# ═══════════════════════════════════════════════════════════════

class ShareIn(BaseModel):
    is_public: bool


@router.patch("/strategies/{sid}/share")
async def toggle_share(sid: int, body: ShareIn, request: Request):
    """开关 is_public · 只有 owner 可"""
    uid = getattr(request.state, "user_id", None)
    if not uid:
        uid = "46066ca9-bf34-4fad-a9d5-bda5beb74c11"
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT user_id, is_official FROM strategy WHERE id=%s", (sid,))
    r = cur.fetchone()
    if not r:
        cur.close(); conn.close()
        raise HTTPException(404, f"strategy {sid} not found")
    owner, is_off = r
    if is_off:
        cur.close(); conn.close()
        raise HTTPException(403, "官方策略不可切换分享")
    if str(owner) != str(uid):
        cur.close(); conn.close()
        raise HTTPException(403, "只有创建者可改分享状态")
    cur.execute("UPDATE strategy SET is_public=%s, updated_at=NOW() WHERE id=%s",
                (body.is_public, sid))
    conn.commit(); cur.close(); conn.close()
    return {"ok": True, "id": sid, "is_public": body.is_public}


@router.post("/strategies/{sid}/fork")
async def fork_strategy(sid: int, request: Request):
    """fork 别人的策略 · 派生一份到自己名下"""
    uid = getattr(request.state, "user_id", None)
    if not uid:
        uid = "46066ca9-bf34-4fad-a9d5-bda5beb74c11"
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        "SELECT user_id, name, description, factors, config, is_official, is_public FROM strategy WHERE id=%s",
        (sid,),
    )
    r = cur.fetchone()
    if not r:
        cur.close(); conn.close()
        raise HTTPException(404, f"strategy {sid} not found")
    owner, name, desc, factors, config, is_off, is_pub = r
    if not (is_off or is_pub or str(owner) == str(uid)):
        cur.close(); conn.close()
        raise HTTPException(403, "非公开策略无法 fork")
    new_name = f"{name} (fork)"
    new_desc = f"Fork from #{sid}\n\n{desc or ''}"
    cur.execute(
        """INSERT INTO strategy (user_id, name, description, factors, config, fork_from)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (str(uid), new_name[:64], new_desc, json.dumps(factors), json.dumps(config), sid),
    )
    new_id = cur.fetchone()[0]
    conn.commit(); cur.close(); conn.close()
    return {"id": new_id, "fork_from": sid, "name": new_name}


@router.get("/leaderboard")
async def leaderboard(
    period: str = "1y",     # 30d / 90d / 1y
    sort: str = "sharpe",   # sharpe / ann_ret / calmar
    limit: int = 20,
):
    """社区策略排行 · 只显示 is_public=TRUE + 有回测的
    join 最新 backtest_result(每 strategy 取最新)· 按 metrics 排序
    """
    period_days = {"30d": 30, "90d": 90, "1y": 365}.get(period, 365)
    sort_key = sort if sort in ("sharpe", "ann_ret", "calmar", "sortino") else "sharpe"
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        f"""
        SELECT s.id, s.name, s.description, s.factors, s.config, s.user_id, s.created_at, s.fork_from,
               bt.metrics, bt.start_date, bt.end_date
        FROM strategy s
        JOIN LATERAL (
          SELECT metrics, start_date, end_date FROM backtest_result
          WHERE strategy_id = s.id
          ORDER BY created_at DESC
          LIMIT 1
        ) bt ON TRUE
        WHERE s.is_public = TRUE
          AND (bt.end_date - bt.start_date) >= 30
          AND bt.end_date >= (CURRENT_DATE - INTERVAL '{period_days} days')
        ORDER BY COALESCE((bt.metrics->>%s)::FLOAT, -999) DESC NULLS LAST
        LIMIT %s
        """,
        (sort_key, limit),
    )
    rows = cur.fetchall()
    cur.close(); conn.close()
    return {"strategies": [
        {
            "id": r[0], "name": r[1], "description": r[2],
            "factors": r[3], "config": r[4],
            "author_id": str(r[5]) if r[5] else None,
            "created_at": r[6].isoformat() if r[6] else None,
            "fork_from": r[7],
            "metrics": r[8],
            "backtest_start": r[9].isoformat() if r[9] else None,
            "backtest_end": r[10].isoformat() if r[10] else None,
        } for r in rows
    ], "period": period, "sort": sort_key}


@router.delete("/strategies/{sid}")
async def delete_strategy(sid: int, request: Request):
    """C3 · 删除自己的策略 · 官方不许删"""
    uid = getattr(request.state, "user_id", None)
    if not uid:
        uid = "46066ca9-bf34-4fad-a9d5-bda5beb74c11"
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT user_id, is_official FROM strategy WHERE id=%s", (sid,))
    r = cur.fetchone()
    if not r:
        cur.close(); conn.close()
        raise HTTPException(404, f"strategy {sid} not found")
    owner, is_off = r
    if is_off:
        cur.close(); conn.close()
        raise HTTPException(403, "官方策略不可删")
    if str(owner) != str(uid):
        cur.close(); conn.close()
        raise HTTPException(403, "只有创建者可删")
    cur.execute("DELETE FROM strategy WHERE id=%s", (sid,))
    conn.commit(); cur.close(); conn.close()
    return {"ok": True, "id": sid}


# ═══════════════════════════════════════════════════════════════
# POST /backtest/run · 同步回测(MVP 简单)
# ═══════════════════════════════════════════════════════════════

class BacktestIn(BaseModel):
    strategy_id: int | None = None
    factors: list[dict] | None = None    # 支持不存策略直接跑
    config: dict | None = None
    start: str | None = None
    end: str | None = None


def _resolve_backtest_spec(body: BacktestIn):
    """把 BacktestIn 转成 (strategy, start, end)"""
    if body.strategy_id:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT factors, config FROM strategy WHERE id=%s", (body.strategy_id,))
        r = cur.fetchone()
        cur.close(); conn.close()
        if not r:
            raise HTTPException(404, f"strategy {body.strategy_id} not found")
        strategy = {"factors": r[0], "config": r[1], "id": body.strategy_id}
    elif body.factors and body.config:
        strategy = {"factors": body.factors, "config": body.config}
    else:
        raise HTTPException(400, "必须提供 strategy_id 或 factors+config")
    end = date.fromisoformat(body.end) if body.end else date.today()
    start = date.fromisoformat(body.start) if body.start else (end - timedelta(days=365))
    return strategy, start, end


@router.post("/backtest/run")
async def run_backtest_ep(body: BacktestIn, request: Request, bg: BackgroundTasks):
    """D-4 · 异步版 · 返 task_id · 前端轮询 /backtest/status/{task_id}
    · cache 命中直接返(不排队)
    · sync=1 强制同步(用于 backtest_result 首次批量预填)
    """
    uid = getattr(request.state, "user_id", None)
    strategy, start, end = _resolve_backtest_spec(body)

    # cache 命中
    spec_hash = backtest_engine.compute_spec_hash(strategy, start, end)
    conn = get_conn(); cur = conn.cursor()
    # 阶段 4 · 命中直接带上持久化的 trading_cost/gross_metrics + 前 200 笔逐笔
    cur.execute(
        "SELECT id, metrics, nav_series, positions, trading_cost, gross_metrics, positions_hist "
        "FROM backtest_result WHERE spec_hash=%s", (spec_hash,))
    hit = cur.fetchone()
    if hit:
        _rid = hit[0]
        _trades = []
        if hit[4] is not None:
            cur.execute(
                "SELECT " + ", ".join(_TRADE_COLS) + " FROM backtest_trade "
                "WHERE result_id=%s ORDER BY trade_date, id LIMIT 200", (_rid,))
            _trades = [
                {c: (v.isoformat() if hasattr(v, "isoformat") else v)
                 for c, v in zip(_TRADE_COLS, row)}
                for row in cur.fetchall()
            ]
        cur.close(); conn.close()
        return {"cached": True, "result_id": _rid,
                "metrics": hit[1], "nav_series": hit[2], "positions": hit[3],
                "trading_cost": hit[4], "gross_metrics": hit[5], "trades": _trades}
    cur.close(); conn.close()

    # 异步入队
    from app.services.quant import backtest_task
    task_id = backtest_task.submit(strategy, start, end, str(uid) if uid else None)
    bg.add_task(backtest_task.run_and_store, task_id, strategy, start, end, str(uid) if uid else None)
    return {"cached": False, "task_id": task_id, "status": "queued"}


@router.get("/backtest/status/{task_id}")
async def backtest_status(task_id: str):
    """D-4 · 轮询任务状态 · queued/running/done/error/not_found
    · done 时结果内嵌 result 字段
    · 客户端应在 done 后 · 若需持久化 · 调 /backtest/persist/{task_id}
    """
    from app.services.quant import backtest_task
    return backtest_task.get_status(task_id)


@router.post("/backtest/persist/{task_id}")
async def backtest_persist(task_id: str, request: Request):
    """D-4 · 把 done 状态的 task 结果落 backtest_result 表(用户主动调用)
    · 未 done 返 400
    """
    from app.services.quant import backtest_task
    st = backtest_task.get_status(task_id)
    if st.get("status") != "done":
        raise HTTPException(400, f"task 未完成 · status={st.get('status')}")
    result = st.get("result", {})
    if not result:
        raise HTTPException(400, "task 无结果")
    strategy_id = result.get("strategy_id")   # backtest_engine 未返 · 需从 st 拿
    # 从 st 里拿 strategy_id 需回溯 submit 时的原始 · 简化:客户端传 strategy_id
    body_strategy_id = None
    try:
        body = await request.json()
        body_strategy_id = body.get("strategy_id")
    except Exception:
        pass
    spec_hash = result.get("spec_hash") or backtest_engine.compute_spec_hash(
        {"factors": [], "config": {}}, date.today(), date.today()
    )
    _tc = result.get("trading_cost")
    _slip_model = (_tc or {}).get("slippage_model")
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """INSERT INTO backtest_result (strategy_id, spec_hash, start_date, end_date,
             metrics, nav_series, positions, cost_used, duration_ms,
             trading_cost, gross_metrics, slippage_model, positions_hist)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (spec_hash) DO UPDATE
             SET metrics = EXCLUDED.metrics, nav_series = EXCLUDED.nav_series,
                 positions = EXCLUDED.positions, cost_used = EXCLUDED.cost_used,
                 duration_ms = EXCLUDED.duration_ms,
                 trading_cost = EXCLUDED.trading_cost,
                 gross_metrics = EXCLUDED.gross_metrics,
                 slippage_model = EXCLUDED.slippage_model,
                 positions_hist = EXCLUDED.positions_hist
           RETURNING id""",
        (body_strategy_id, spec_hash,
         date.fromisoformat(result["start"]), date.fromisoformat(result["end"]),
         json.dumps(result["metrics"]), json.dumps(result["nav_series"]),
         json.dumps(result.get("positions", [])), result.get("cost_used", 0),
         result.get("duration_ms", 0),
         json.dumps(_tc) if _tc is not None else None,
         json.dumps(result.get("gross_metrics")) if result.get("gross_metrics") is not None else None,
         _slip_model,
         # 逐期持仓 —— 不存的话缓存命中时「持仓变化」是空的:
         # 同一个策略第一次有调仓记录、第二次没有,用户无法理解(迁移 0016)
         json.dumps(result.get("positions_hist", []))),
    )
    new_id = cur.fetchone()[0]
    # 阶段 4 · 逐笔全部落 backtest_trade
    _n_trades = _persist_trades(cur, new_id, result.get("trades", []))
    conn.commit(); cur.close(); conn.close()
    return {"result_id": new_id, "spec_hash": spec_hash, "trades_persisted": _n_trades}


# ═══════════════════════════════════════════════════════════════
# 阶段 4 · 逐笔明细查询 + 全量 CSV 导出(供前端「逐笔明细」面板)
# ═══════════════════════════════════════════════════════════════

# 前端表格取的字段(顺序即建表顺序 · 与 backtest_trade 列一一对应)
_TRADE_VIEW_COLS = [
    "id", "trade_date", "code", "side", "shares", "price", "turnover",
    "commission", "stamp_tax", "slippage", "other", "total_cost",
    "slippage_model", "impact_bps_actual", "adv_20d", "order_value_to_adv_ratio",
]


@router.get("/backtest/{result_id}/trades")
async def get_trades(result_id: int, limit: int = 200):
    """取某次回测的逐笔明细 · 默认前 200 笔 · 供前端表格显示。

    trade_date 转 ISO 字符串(JSON 可序列化);无记录时 trades 为 []。
    """
    limit = max(1, min(limit, 2000))
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """SELECT id, trade_date, code, side, shares, price, turnover,
                  commission, stamp_tax, slippage, other, total_cost,
                  slippage_model, impact_bps_actual, adv_20d, order_value_to_adv_ratio
             FROM backtest_trade WHERE result_id=%s
             ORDER BY trade_date, id LIMIT %s""",
        (result_id, limit),
    )
    rows = cur.fetchall()
    cur.close(); conn.close()

    trades = []
    for r in rows:
        d = dict(zip(_TRADE_VIEW_COLS, r))
        if d.get("trade_date") is not None:
            d["trade_date"] = d["trade_date"].isoformat()
        trades.append(d)
    return {"result_id": result_id, "count": len(trades), "trades": trades}


@router.get("/backtest/{result_id}/trades.csv")
async def download_trades_csv(result_id: int):
    """下载全量逐笔明细 CSV · UTF-8 BOM · Excel 可直接打开。"""
    import csv, io
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """SELECT trade_date, code, side, shares, price, turnover,
                  commission, stamp_tax, slippage, other, total_cost,
                  slippage_model, impact_bps_actual, adv_20d, order_value_to_adv_ratio
             FROM backtest_trade WHERE result_id=%s
             ORDER BY trade_date, id""",
        (result_id,),
    )
    rows = cur.fetchall()
    cur.close(); conn.close()

    buf = io.StringIO()
    buf.write("﻿")  # BOM · Excel 认 UTF-8
    w = csv.writer(buf)
    w.writerow([
        "交易日", "代码", "方向", "股数", "价格", "成交额",
        "佣金", "印花税", "滑点", "其他", "合计成本",
        "滑点模型", "冲击bps", "ADV20日", "单量占ADV",
    ])
    for r in rows:
        w.writerow(r)

    filename = f"backtest_{result_id}_trades.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ═══════════════════════════════════════════════════════════════
# D-5 · Bootstrap 稳健性检验(异步 · 100 次)
# ═══════════════════════════════════════════════════════════════

class BootstrapIn(BaseModel):
    strategy_id: int | None = None
    factors: list[dict] | None = None
    config: dict | None = None
    full_start: str | None = None
    full_end: str | None = None
    n_bootstrap: int = 100
    sub_period_days: int = 365


@router.post("/backtest/bootstrap")
async def bootstrap_ep(body: BootstrapIn, request: Request, bg: BackgroundTasks):
    """D-5 · Bootstrap 稳健性 · 异步 · 100 次 × 1 年窗口
    · 前端轮询 /backtest/status/{task_id} 同款
    · kronos 因子自动剔除(T-0 无历史意义)
    """
    uid = getattr(request.state, "user_id", None)
    # 复用 BacktestIn 解析(name 不同 · 手写)
    if body.strategy_id:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT factors, config FROM strategy WHERE id=%s", (body.strategy_id,))
        r = cur.fetchone()
        cur.close(); conn.close()
        if not r:
            raise HTTPException(404, f"strategy {body.strategy_id} not found")
        strategy = {"factors": r[0], "config": r[1], "id": body.strategy_id}
    elif body.factors and body.config:
        strategy = {"factors": body.factors, "config": body.config}
    else:
        raise HTTPException(400, "必须提供 strategy_id 或 factors+config")
    full_end = date.fromisoformat(body.full_end) if body.full_end else date.today()
    full_start = date.fromisoformat(body.full_start) if body.full_start else (full_end - timedelta(days=365 * 3))

    # 异步入队
    from app.services.quant import backtest_task
    from app.services import kpred_cache as rc
    import uuid, time
    task_id = uuid.uuid4().hex[:16]
    rc.set(f"quant:bt_task:{task_id}", {
        "status": "queued", "created_at": time.time(), "kind": "bootstrap",
    }, 3600)

    def _do():
        rc.set(f"quant:bt_task:{task_id}", {
            "status": "running", "started_at": time.time(), "kind": "bootstrap",
        }, 3600)
        try:
            result = backtest_engine.bootstrap_backtest(
                strategy, full_start, full_end,
                n_bootstrap=body.n_bootstrap, sub_period_days=body.sub_period_days,
                user_id=str(uid) if uid else None,
            )
            rc.set(f"quant:bt_task:{task_id}", {
                "status": "done" if "error" not in result else "error",
                "finished_at": time.time(), "kind": "bootstrap",
                "result": result,
            }, 3600)
        except Exception as e:
            rc.set(f"quant:bt_task:{task_id}", {
                "status": "error", "kind": "bootstrap",
                "error": type(e).__name__, "message": str(e)[:500],
            }, 3600)

    bg.add_task(_do)
    return {"task_id": task_id, "status": "queued", "kind": "bootstrap"}


@router.post("/backtest/run-sync")
async def run_backtest_sync(body: BacktestIn, request: Request):
    """同步版 · 保留供内部/管理员使用(6 官方策略预填)· 不推荐前端调用"""
    uid = getattr(request.state, "user_id", None)
    strategy, start, end = _resolve_backtest_spec(body)

    spec_hash = backtest_engine.compute_spec_hash(strategy, start, end)
    conn = get_conn(); cur = conn.cursor()
    # 阶段 4 · 一并取持久化的 trading_cost/gross_metrics · 不再靠 preset 现补兜底
    cur.execute(
        "SELECT id, metrics, nav_series, positions, trading_cost, gross_metrics, positions_hist "
        "FROM backtest_result WHERE spec_hash=%s", (spec_hash,))
    hit = cur.fetchone()
    if hit:
        _rid = hit[0]
        _tc, _gm = hit[4], hit[5]
        if _tc is not None:
            # 新记录:trading_cost 已落库 · 直接还原完整对比 + 前 200 笔逐笔
            cur.execute(
                "SELECT " + ", ".join(_TRADE_COLS) + " FROM backtest_trade "
                "WHERE result_id=%s ORDER BY trade_date, id LIMIT 200", (_rid,))
            _trows = cur.fetchall()
            cur.close(); conn.close()
            _trades = [
                {c: (v.isoformat() if hasattr(v, "isoformat") else v)
                 for c, v in zip(_TRADE_COLS, row)}
                for row in _trows
            ]
            # positions_hist 从表里读(迁移 0016 加的列)。
            # 不加这一列的话,同一个策略跑第二次(缓存命中)反而显示
            # "还没有调仓记录" —— 第一次有、第二次没有,用户完全无法理解。
            # 老结果这列是 NULL(它们确实没存过),按空处理。
            return {"result_id": _rid, "cached": True,
                    "metrics": hit[1], "nav_series": hit[2], "positions": hit[3],
                    "gross_metrics": _gm, "trading_cost": _tc, "trades": _trades,
                    "positions_hist": hit[6] or []}
        # 旧记录:trading_cost 从未存过 · 沿用 preset 现补兜底(毛/净留 null)
        cur.close(); conn.close()
        from app.services.quant.broker import defaults as _bd
        _pkey = strategy["config"].get("broker_preset")
        _preset = _bd.resolve(_pkey)
        return {"result_id": _rid, "cached": True,
                "metrics": hit[1], "nav_series": hit[2], "positions": hit[3],
                "trading_cost": {
                    "broker": _preset.to_dict(),
                    "cost_bps_used": _preset.total_bps_per_side,
                    "cached_note": "缓存记录 · 毛/净收益已丢 · 重跑一次拿完整对比",
                    "gross_total_return_pct": None,
                    "net_total_return_pct": None,
                    "cost_ratio_of_gross_pct": None,
                    "total_bps_consumed": None,
                    "breakdown": {k: {"bps": v, "share_pct": None, "cost_used": None}
                                  for k, v in _preset.breakdown_avg().items()},
                    "turnover_total": None,
                }}

    result = backtest_engine.run_backtest(strategy, start, end, str(uid) if uid else None)
    if "error" in result:
        cur.close(); conn.close()
        return {"error": result["error"], "message": result.get("message", "")}

    positions = result.get("positions", [])
    if positions:
        name_map = strategy_engine.fetch_stock_names([p["code"] for p in positions])
        for p in positions:
            p["name"] = name_map.get(p["code"], p["code"])

    _tc = result.get("trading_cost")
    _slip_model = (_tc or {}).get("slippage_model")
    cur.execute(
        """INSERT INTO backtest_result (strategy_id, spec_hash, start_date, end_date,
             metrics, nav_series, positions, cost_used, duration_ms,
             trading_cost, gross_metrics, slippage_model, positions_hist)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (spec_hash) DO UPDATE
             SET metrics = EXCLUDED.metrics, nav_series = EXCLUDED.nav_series,
                 positions = EXCLUDED.positions, cost_used = EXCLUDED.cost_used,
                 duration_ms = EXCLUDED.duration_ms,
                 trading_cost = EXCLUDED.trading_cost,
                 gross_metrics = EXCLUDED.gross_metrics,
                 slippage_model = EXCLUDED.slippage_model,
                 positions_hist = EXCLUDED.positions_hist
           RETURNING id""",
        (body.strategy_id, spec_hash, start, end,
         json.dumps(result["metrics"]), json.dumps(result["nav_series"]),
         json.dumps(positions), result["cost_used"], result["duration_ms"],
         json.dumps(_tc) if _tc is not None else None,
         json.dumps(result.get("gross_metrics")) if result.get("gross_metrics") is not None else None,
         _slip_model,
         # 逐期持仓 —— 不存的话缓存命中时「持仓变化」是空的:
         # 同一个策略第一次有调仓记录、第二次没有,用户无法理解(迁移 0016)
         json.dumps(result.get("positions_hist", []))),
    )
    new_id = cur.fetchone()[0]
    # 阶段 4 · 逐笔全部落 backtest_trade
    _n_trades = _persist_trades(cur, new_id, result.get("trades", []))
    conn.commit(); cur.close(); conn.close()

    # 复赛 §3.B · 新算的完整结构透传 · trading_cost + gross_metrics + nav_gross_series
    # 阶段 4 · trades 前 200 笔发前端(全部已落库)
    return {"result_id": new_id, "cached": False,
            "metrics": result["metrics"], "nav_series": result["nav_series"],
            "positions": positions, "duration_ms": result["duration_ms"],
            "gross_metrics": result.get("gross_metrics"),
            "nav_gross_series": result.get("nav_gross_series"),
            "trading_cost": result.get("trading_cost"),
            "trades": result.get("trades", [])[:200],
            "trades_persisted": _n_trades,
            # 逐期持仓 —— 「持仓变化」面板要用。
            # ⚠️ 这个 return 是**白名单式**的:引擎返回什么不重要,
            # 这里没列的字段一律到不了前端。加字段时容易只改引擎忘了改这里,
            # 表现是"本地直调有数据、走 API 就是空的",而且不报错。
            "positions_hist": result.get("positions_hist", [])}


# ═══════════════════════════════════════════════════════════════
# C6 · 半自动下单 CSV 导出
# ═══════════════════════════════════════════════════════════════

class OrdersGenIn(BaseModel):
    positions: list[dict]          # [{code, weight}]
    total_capital: float = 100000  # 元
    broker: str = "em"             # em / ht / generic
    price_type: str = "market"     # market / limit


@router.post("/orders/generate")
async def generate_orders(body: OrdersGenIn):
    """按 positions 生成券商 CSV 下单指令 · 3 格式:东财 / 华泰 / 通用
    - 每只股票分配 total_capital * weight → 元
    - 除以 close → 股数 · 向下取整到 100 手
    - 输出:CSV 文件 · 附下载头
    """
    positions = body.positions or []
    if not positions:
        raise HTTPException(400, "positions 不能为空")
    codes = [p["code"] for p in positions if p.get("code")]
    if not codes:
        raise HTTPException(400, "positions 无有效 code")

    # 拉最新 close · 从 klines 表
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """SELECT DISTINCT ON (code) code, close FROM klines
           WHERE code = ANY(%s) AND period='daily' AND close IS NOT NULL
           ORDER BY code, ts DESC""",
        (codes,),
    )
    price_map = {c: float(cl) for c, cl in cur.fetchall()}
    cur.close(); conn.close()

    name_map = strategy_engine.fetch_stock_names(codes)

    from app.services.quant.market import US, market_of_code
    orders = []
    for p in positions:
        code = p.get("code")
        weight = float(p.get("weight") or 0)
        price = price_map.get(code)
        if not price or price <= 0 or weight <= 0:
            continue
        amount = body.total_capital * weight
        # 一手股数:A 股 100 股,美股 1 股(2026-09-11)。按 100 取整的话,
        # 几百美元一股的票小资金直接被取整成 0 股、整笔消失
        lot = 1 if market_of_code(code) == US else 100
        qty = int(amount / price / lot) * lot
        if qty < lot:
            continue
        orders.append({
            "code": code,
            "name": name_map.get(code, code),
            "side": "买入",
            "qty": qty,
            "price": round(price, 2) if body.price_type == "limit" else "",
            "price_type": "限价" if body.price_type == "limit" else "市价",
        })

    if not orders:
        raise HTTPException(400, "无有效下单指令(检查 klines close 数据)")

    csv_str = _format_orders_csv(orders, body.broker)
    filename = f"orders_{date.today().isoformat()}_{body.broker}.csv"
    return Response(
        content=csv_str,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _format_orders_csv(orders: list[dict], broker: str) -> str:
    import csv, io
    buf = io.StringIO()
    w = csv.writer(buf)
    if broker == "em":
        # 东方财富格式
        w.writerow(["代码", "方向", "数量", "价格", "类型"])
        for o in orders:
            w.writerow([o["code"], o["side"], o["qty"], o["price"], o["price_type"]])
    elif broker == "ht":
        # 华泰涨乐财富通格式
        w.writerow(["证券代码", "证券名称", "买卖方向", "委托数量", "委托价格", "价格类型"])
        for o in orders:
            w.writerow([o["code"], o["name"], o["side"], o["qty"], o["price"], o["price_type"]])
    else:
        # 通用格式
        w.writerow(["code", "name", "side", "qty", "price", "price_type"])
        for o in orders:
            w.writerow([o["code"], o["name"], "buy", o["qty"], o["price"], o["price_type"]])
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════
# 全市场扫描筛选(外部扫描源通道)
#
#   GET  /quant/screener/meta            · 市场 / 预置脚本 / 语法说明
#   GET  /quant/screener/fields          · 字段搜索(3777 个,只能搜不能列)
#   POST /quant/screener/run             · 跑脚本
#   GET  /quant/screener/saved           · 我保存的扫描策略(要登录)
#   POST /quant/screener/saved           · 保存 · 同名覆盖(要登录)
#   DELETE /quant/screener/saved/{id}    · 删一个(只能删自己的)
#
# 端点名不能叫 /scan —— 那个已经是「按因子打分选 Top N」了,两件事:
# /scan 走站内 factor_value(A 股 · 有历史 · 能回测),
# /screener 走外部扫描源(全球 · 只有当前快照 · 不进回测)。
# 混在一起会让人以为扫描结果可以直接拿去跑回测。
# ═══════════════════════════════════════════════════════════════

class ScreenIn(BaseModel):
    script: str = ""
    preset: str | None = None
    market: str = "us"
    limit: int = 100
    sort_by: str | None = None
    descending: bool = True
    as_of: str | None = None       # 时间回溯:YYYY-MM-DD,空 = 今天的快照
    probe: bool = False            # 条件行上的「单条测试」:不扣扫描次数,只回命中数
    resort: bool = False           # 列头排序:按上一次扫描缓存的全部命中重排,不扣次数、不取数(screen_resort)


# ─── 会员校验与额度(2026-09-14 用户定)─────────────────────────────
# 规则与「为什么」在 services/quant/screen_quota.py 文件头。这里只做两件事:
#   · 扫描 / 生成 / 字段搜索 / 命中日 要登录。/api/quant/ 是免登录前缀,**只能在路由里拦**。
#   · 扫描、AI 识别扣次数,返回体带 quota,前端每用一次提示剩余。
# meta / history-range 仍然公开:页面骨架和示例要让没登录的人也看得到「这是什么」。
from app.services.quant import screen_quota

LOGIN_MSG = "魔法筛选器是会员功能,登录或注册后即可使用。"


def _member(request: Request) -> tuple[str, str | None]:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, {"message": LOGIN_MSG, "need_login": True})
    return uid, getattr(request.state, "user_role", None)


def _quota_http(e: Exception) -> HTTPException:
    if isinstance(e, screen_quota.TooFast):
        return HTTPException(429, {"message": str(e), "kind": "too_fast",
                                   "retry_after": round(e.wait_s, 1)})
    return HTTPException(429, {"message": str(e), "kind": "quota", "quota": e.info})


@router.get("/screener/quota")
async def screener_quota(request: Request):
    """今天还剩几次。没登录不报 401(页面启动就调,401 会在控制台刷红),回 login=false 让前端弹登录。"""
    uid = getattr(request.state, "user_id", None)
    if not uid:
        return {"login": False, "message": LOGIN_MSG}
    return await asyncio.to_thread(screen_quota.snapshot, uid, getattr(request.state, "user_role", None))


class HitDaysIn(BaseModel):
    script: str = ""
    market: str = "us"
    code: str = ""
    as_of: str | None = None       # 截到哪天(结果是回溯出来的就传回溯日),空 = 日线最新一天
    in_result: bool = False        # 这只票在这次实时扫描的结果表里 → 扫描当日以结果表为准(screen_hits 文件头)


@router.post("/screener/hit-days")
async def screener_hit_days(body: HitDaysIn, request: Request):
    """一只票过去 250 个交易日里,这份脚本哪些天会命中(悬停日K 的淡蓝色标记)。口径同时间回溯,见 screen_hits。"""
    _member(request)               # 不扣次数:快照 20 分钟缓存 + 自家日线,不打上游
    code = (body.code or "").strip()
    if not code:
        raise HTTPException(400, "要给股票代码")
    as_of = None
    if body.as_of:
        from datetime import date as _date
        try:
            as_of = _date.fromisoformat(body.as_of.strip())
        except ValueError:
            raise HTTPException(400, f"日期格式不对:{body.as_of!r},要 YYYY-MM-DD")
    from app.services.quant import screen_hits
    try:
        # 首次要拉快照(1~3s)+ 整窗日线(冷启动约 10s),同步代码,不能占着事件循环
        return await asyncio.to_thread(screen_hits.hit_days, body.script, body.market, code, as_of,
                                       screen_hits.DAYS, bool(body.in_result))
    except ScreenError as e:
        raise HTTPException(400, str(e))


@router.get("/screener/last-hit")
async def screener_last_hit(request: Request, market: str = "us", preset: str = ""):
    """官方示例最近一次命中的日子(扫描 0 命中时前端轮询)。只查缓存、不起新任务 —— 任务由 /screener/run 起。"""
    _member(request)
    from app.services.quant import screen_last_hit
    return await asyncio.to_thread(screen_last_hit.lookup, market, preset, False)


@router.get("/screener/history-range")
async def screener_history_range(market: str = "us"):
    """时间回溯能选的日期范围 —— 来自自家日线(rs_daily),前端据此限制日期框。"""
    from app.services.quant import screen_asof
    if market not in screen_source.MARKETS:
        raise HTTPException(400, f"不支持的市场:{market}")
    try:
        d = await asyncio.to_thread(screen_asof.history_range, market)
    except Exception:                                   # noqa: BLE001
        # 日线库连不上:原来裸 500,前端日期框拿不到范围也不知道为什么(执行用例 KK-004 发现)
        raise HTTPException(503, "日线库暂时连不上,时间回溯先用不了,请稍后再试。")
    d["market"] = market
    d["note"] = ("回溯用的是每晚落库的全市场日线,只保留约 320 个交易日;越往前,52 周高低、"
                 "精确 RS 评级这类长窗口字段越算不出。市值 / 财务字段没有历史值,回溯不了。")
    return d


@router.get("/screener/meta")
async def screener_meta():
    return {
        "markets": [
            {"key": k, "label": screen_source.MARKETS[k].label,
             "currency": screen_source.MARKETS[k].currency,
             "note": screen_source.MARKETS[k].note or None}
            for k in screen_source.MARKET_ORDER
        ],
        "presets": [
            {"key": p["key"], "name": p["name"], "market": p["market"],
             "desc": p["desc"], "script": p["script"]}
            for p in screen_source.PRESETS
        ],
        "source": "全市场扫描源(非官方接口 · 延迟 15 分钟)",
        "limits": [
            screen_source.DELAY_WARN,
            "只有当前快照,没有历史序列 —— 结果不能用于回测,也不写进因子库。",
            screen_source.MARKET_CAP_WARN,
        ],
    }


class ScreenParseIn(BaseModel):
    script: str = ""
    preset: str | None = None
    market: str = "us"
    # 默认 False:脚本解析和本地关键词匹配都不花钱,AI 要用户点了「AI 识别」才走。
    # 默认打开的话每一次手滑都在烧 token。
    allow_ai: bool = False
    # 追加模式下当前条件区的完整脚本:让「复制这一条」复制出的片段能引用原脚本的定义与参数(2026-09-17)
    context: str | None = None


@router.post("/screener/parse")
async def screener_parse(body: ScreenParseIn, request: Request):
    """脚本 → 可视化条件行。界面上点「生成」走这条,不拉行情。"""
    uid, role = _member(request)
    script = body.script or ""
    if body.preset and not script.strip():
        p = screen_source.preset(body.preset)
        if p is None:
            raise HTTPException(404, f"没有这个示例脚本:{body.preset}")
        script = p["script"]
    # AI 次数只在 parse_script 真要调模型的那一刻扣(on_ai),本地识别成功一次都不扣
    reserved: list[dict] = []

    def on_ai():
        reserved.append(screen_quota.reserve(uid, role, "ai"))

    try:
        # to_thread 不能省:自然语言那条分支要调 LLM(秒级、同步阻塞),
        # 直接在 async 路由里跑会把整个事件循环卡住,别的用户的请求全在排队。
        # user_id 还用来记「这条对照表是谁用 AI 学出来的」,不影响识别本身。
        d = await asyncio.to_thread(
            screen_source.parse_script, script, body.market, body.allow_ai, uid, on_ai, body.context)
    except screen_quota.QuotaExceeded as e:
        raise _quota_http(e)
    except screen_source.NeedsAI as e:
        # 结构化 detail —— 前端据此弹「AI 识别」按钮。
        # 让前端去匹配报错文本来判断"能不能试 AI"是一种迟早会断的耦合。
        # kind:text = 大白话没认出来 · script = 脚本编译不过(AI 只修报错那几处)
        raise HTTPException(400, {"message": str(e), "can_try_ai": True,
                                  "kind": getattr(e, "kind", "text")})
    except ScreenError as e:
        # 模型压根没调通(网络 / 网关错)= 没花 token,退回次数;
        # 模型答了但结果用不了 = token 已经花了,照计 —— 否则反复点失败的 AI 等于不限次调模型
        if reserved and str(e).startswith("调用模型失败"):
            await asyncio.to_thread(screen_quota.refund, uid, role, "ai")
            raise HTTPException(400, str(e) + "(这次没调通模型,不计入 AI 识别次数)")
        if reserved:
            q = reserved[-1]
            raise HTTPException(400, {"message": str(e), "quota": q,
                                      "quota_note": f"这次已经调用了模型,计 1 次 AI 识别,今天还剩 {q['remaining']} 次"
                                      if q.get("remaining") is not None else None})
        raise HTTPException(400, str(e))
    except Exception:
        if reserved:
            await asyncio.to_thread(screen_quota.refund, uid, role, "ai")
        raise
    if reserved:
        d["quota"] = reserved[-1]
    off = d.get("official_preset")
    if off:
        # 点「生成」时确认一下这个官方示例的最近命中日在算(平时由 main.py 的 prewarm_loop 定时预热,
        # 这里兜底服务刚重启、预热还没轮到的情况;已缓存 / 已排队的直接跳过)
        from app.services.quant import screen_last_hit
        try:
            await asyncio.to_thread(screen_last_hit.lookup, off["market"], off["key"])
        except Exception:                               # noqa: BLE001
            pass                                        # 预热失败不影响生成
    return d


@router.get("/screener/fields")
async def screener_fields(request: Request, market: str = "us", q: str = "", limit: int = 50):
    _member(request)
    try:
        return {"market": market,
                "fields": screen_source.field_search(market, q, max(1, min(limit, 200)))}
    except ScreenError as e:
        raise HTTPException(400, str(e))


@router.post("/screener/run")
async def screener_run(body: ScreenIn, request: Request):
    uid, role = _member(request)
    script = body.script or ""
    if body.preset and not script.strip():
        p = screen_source.preset(body.preset)
        if p is None:
            raise HTTPException(404, f"没有这个示例脚本:{body.preset}")
        script = p["script"]
    as_of = None
    if body.as_of:
        from datetime import date as _date
        try:
            as_of = _date.fromisoformat(body.as_of.strip())
        except ValueError:
            raise HTTPException(400, f"时间回溯的日期格式不对:{body.as_of!r},要 YYYY-MM-DD")
        # 按上海日期判「未来」:容器是 UTC,上海 0~8 点选「今天」原来会被当成未来拒掉(执行用例 GN-035 发现)
        if as_of > screen_quota.today_sh():
            raise HTTPException(400, f"时间回溯不能选未来的日期:{as_of}")
    from app.services.quant import screen_resort
    if body.resort:
        # 列头排序(2026-09-14 用户要求):不扣次数、不受 5 秒间隔、不取数。只能重排自己上一次跑出的那张表
        try:
            return screen_resort.resort(uid, screen_resort.key_of(script, body.market, as_of),
                                        body.sort_by, body.descending,
                                        100 if body.limit is None else body.limit)
        except screen_resort.Expired as e:
            raise HTTPException(409, {"kind": "resort_expired", "message": str(e)})
        except ValueError as e:
            raise HTTPException(400, str(e))
    # 原样运行官方示例不扣扫描次数(2026-09-14 用户要求),改过一点就照常扣 —— 判定口径见 screen_source.official_preset_of。
    # 单独记 preset 计数(每天上限宽松)防刷;5 秒间隔照旧,扫描后等 5 秒也照旧(那是保护上游,不是计费)
    official = None if body.probe else await asyncio.to_thread(
        screen_source.official_preset_of, script, body.market)
    kind = "probe" if body.probe else ("preset" if official else "scan")
    try:
        if not body.probe:
            screen_quota.check_gap(uid, role)
        # 先占后退:并发连点也不会超额;脚本报错 / 上游挂了退回,失败的扫描不吃次数
        q = await asyncio.to_thread(screen_quota.reserve, uid, role, kind)
    except (screen_quota.QuotaExceeded, screen_quota.TooFast) as e:
        raise _quota_http(e)
    except Exception:                                   # noqa: BLE001
        # 额度表连不上(数据库不可用):原来连管理员也裸 500(执行用例 KK-004 注入发现)。
        # 管理员本来就不限次数,照常扫;普通会员没法记账,不能放行,明说原因
        if screen_quota.tier_of(role) is not None:
            raise HTTPException(503, "额度服务暂时连不上(数据库不可用),扫描先用不了,请稍后再试。")
        q = screen_quota.info_of(kind, 0, None)
    try:
        # 同上 —— 拉全市场实测 1~3s,同步 httpx,不能占着事件循环
        out = await asyncio.to_thread(
            screen_source.run_script,
            script, body.market, 1 if body.probe else body.limit, body.sort_by, body.descending, as_of,
            not body.probe)
    except ScreenError as e:
        # 脚本写错、周期映射不了、上游挂了 —— 都是 400,message 直接给用户看。
        # 不要吞成 500 空结果:用户看到"0 只命中"会以为是市场里真的没有票满足条件。
        await asyncio.to_thread(screen_quota.refund, uid, role, kind)
        raise HTTPException(400, str(e) + ("" if body.probe else "(这次扫描没有成功,不计入次数)"))
    except Exception:
        await asyncio.to_thread(screen_quota.refund, uid, role, kind)
        raise
    finally:
        if not body.probe:
            screen_quota.mark_done(uid)
    all_picks = out.pop("_all_picks", None)
    if body.probe:
        # 单条测试只要命中数。不回结果行 —— 否则「测一条」就成了不扣次数的扫描
        return {"probe": True, "matched": out.get("matched"), "scanned": out.get("scanned"),
                "skipped_incomplete": out.get("skipped_incomplete")}
    if all_picks is not None:
        screen_resort.put(uid, screen_resort.key_of(script, body.market, as_of), out, all_picks)
    if official:
        # 不回 quota:前端拿到 quota 会提示「本次扫描计 1 次」,而这次没有计
        out["official_preset"] = official
        if as_of is None and out.get("matched") == 0:
            # 0 命中 → 告诉用户最近一次命中是哪天,可以时间回溯过去看(2026-09-16 用户要求,只做官方示例)。
            # 不阻塞:没算好就回 pending,前端轮询 /screener/last-hit
            from app.services.quant import screen_last_hit
            out["last_hit"] = await asyncio.to_thread(screen_last_hit.lookup, body.market, official["key"])
        return out
    out["quota"] = q
    return out


# ─── 用户保存的扫描策略 ─────────────────────────────────────────────
# 存储与校验在 services/screen_saved.py,那里的文件头写了为什么
# 脚本必须带停用状态、为什么同名即覆盖、为什么存后端不存 localStorage。
#
# 三个接口都**要登录**:/api/quant/ 不在免登录前缀里,这里再判一次 uid
# 是兜底 —— 万一哪天有人把 /api/quant/ 加进白名单,匿名请求也写不进来。
from app.services import screen_saved


class ScreenSaveIn(BaseModel):
    name: str
    market: str
    script: str
    sort_by: str | None = None
    sort_desc: bool = True


def _need_uid(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, "登录后才能保存和查看自己的扫描策略")
    return uid


@router.get("/screener/saved")
async def screener_saved_list(request: Request):
    uid = _need_uid(request)
    try:
        items = await asyncio.to_thread(screen_saved.list_for, uid)
    except Exception:                                   # noqa: BLE001
        raise HTTPException(503, "扫描策略暂时读不出来(数据库不可用),请稍后再试。")
    return {"items": items, "max": screen_saved.MAX_PER_USER}


@router.post("/screener/saved")
async def screener_saved_save(body: ScreenSaveIn, request: Request):
    uid = _need_uid(request)
    try:
        return await asyncio.to_thread(
            screen_saved.save, uid, body.name, body.market, body.script,
            body.sort_by, body.sort_desc, set(screen_source.MARKETS.keys()))
    except screen_saved.SavedError as e:
        raise HTTPException(400, str(e))


@router.delete("/screener/saved/{preset_id}")
async def screener_saved_delete(preset_id: int, request: Request):
    uid = _need_uid(request)
    ok = await asyncio.to_thread(screen_saved.delete, uid, preset_id)
    if not ok:
        # 不区分「不存在」和「不是你的」—— 区分的话等于告诉别人这个 id 存在
        raise HTTPException(404, "没有这个扫描策略(可能已经删过了)")
    return {"ok": True}


# ─── 对照表:忘掉一条学错的 ─────────────────────────────────────────
# 对照表全站共享(见 services/quant/screen_learned.py),学错一条会被所有人反复用。
# 所以命中时前端常驻显示「这条不对,忘掉它」。要登录 —— 匿名能一键清空全站的学习成果不合适。
# 软删:行留着备查,下次这句话会重新交给 AI,新结果会重新启用它。
@router.delete("/screener/learned/{entry_id}")
async def screener_learned_forget(entry_id: int, request: Request):
    uid = _need_uid(request)
    from app.services.quant import screen_learned
    ok = await asyncio.to_thread(screen_learned.forget, entry_id, uid)
    if not ok:
        raise HTTPException(404, "对照表里没有这条(可能已经被忘掉了)")
    return {"ok": True}


# ─── 小鹿智能体(单实例 · 纸上交易)────────────────────────────────
# 契约 docs/agent-dashboard-contract.md;实现 services/quant/agent_run.py。
# dashboard 免登录(和别的 /api/quant/ 读接口一样);三个控制接口要登录 ——
# 「立即跑一次」会产生交易记录,不可撤销,按日期幂等(同一天第二次返回已有结果)。
from app.services.quant import agent_run as _agent


@router.get("/agent/dashboard")
async def agent_dashboard(branch: str = "base"):
    # branch = agent_opt.BRANCHES 的键(base / buy / sell / c / donchian …);不认识的落回 base
    return await asyncio.to_thread(_agent.dashboard, branch)


@router.post("/agent/run")
async def agent_run_once(request: Request):
    _need_uid(request)
    try:
        return await asyncio.to_thread(_agent.run_latest)
    except Exception as e:                                    # noqa: BLE001
        raise HTTPException(400, f"这次没跑成:{e}")


@router.post("/agent/pause")
async def agent_pause(request: Request):
    _need_uid(request)
    return {"state": await asyncio.to_thread(_agent.set_state, "paused")}


@router.post("/agent/resume")
async def agent_resume(request: Request):
    _need_uid(request)
    return {"state": await asyncio.to_thread(_agent.set_state, "running")}


# 研究台(2026-09-13):研究线看板免登录;立项 / 封存 / 解除封存要登录。
# 淘汰线在立项时写定,**没有**修改接口 —— 防的是看过回测结果再回头改标准。
@router.get("/agent/research")
async def agent_research_board(request: Request):
    from app.services.quant.agent_builder import filter_research
    board = await asyncio.to_thread(_agent.research_board)
    return await asyncio.to_thread(filter_research, getattr(request.state, "user_id", None), board)


@router.post("/agent/research")
async def agent_research_create(request: Request):
    uid = _need_uid(request)
    try:
        body = await request.json()
    except Exception:                                         # noqa: BLE001
        raise HTTPException(400, "请求体不是合法 JSON")
    try:
        return await asyncio.to_thread(_agent.research_create, uid, body if isinstance(body, dict) else {})
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/agent/research/{key}/archive")
async def agent_research_archive(key: str, request: Request):
    uid = _need_uid(request)
    try:
        return await asyncio.to_thread(_agent.research_archive, key, True, uid)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/agent/research/{key}/unarchive")
async def agent_research_unarchive(key: str, request: Request):
    uid = _need_uid(request)
    try:
        return await asyncio.to_thread(_agent.research_archive, key, False, uid)
    except ValueError as e:
        raise HTTPException(400, str(e))

# 个人规则按登录账号隔离，回测不覆盖公共研究账本。
from app.services.quant import agent_manual as _manual
from app.routers.agent_builder import router as _builder_router
router.include_router(_builder_router)


@router.get("/agent/rules/{branch}")
async def agent_rules_get(branch: str, request: Request):
    uid = _need_uid(request)
    try:
        return await asyncio.to_thread(_manual.read, uid, branch)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/agent/rules/{branch}")
async def agent_rules_save(branch: str, request: Request):
    uid = _need_uid(request)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("请求体必须为对象")
        return await asyncio.to_thread(_manual.save, uid, branch, body)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/agent/rules/{branch}/backtest")
async def agent_rules_backtest(branch: str, request: Request, bg: BackgroundTasks):
    uid = _need_uid(request)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("请求体必须为对象")
        conn, key, run = await asyncio.to_thread(_manual.submit, uid, branch, body)
        bg.add_task(_manual.run_job, conn, key, run)
        return {"id": run["id"], "status": run["status"]}
    except ValueError as e:
        raise HTTPException(400, str(e))
