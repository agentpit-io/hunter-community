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

import json
import logging
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from fastapi.responses import Response
from pydantic import BaseModel

from app.services.database import get_conn
from app.services.quant import factor_defs, strategy_engine, backtest_engine

log = logging.getLogger(__name__)

router = APIRouter(prefix="/quant", tags=["quant"])


# ═══════════════════════════════════════════════════════════════
# GET /factors
# ═══════════════════════════════════════════════════════════════

@router.get("/factors")
async def list_factors():
    """20 因子清单 · 启用状态(Phase A 只 3 个 enabled=true)"""
    return {
        "factors": [
            {
                "key": f.key, "cat": f.cat, "name": f.name,
                "icon": f.icon, "desc": f.desc,
                "reverse": f.reverse, "enabled": f.enabled,
            } for f in factor_defs.ALL_FACTORS
        ],
        "cat_order": factor_defs.CAT_ORDER,
        "enabled_count": len(factor_defs.enabled_factors()),
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
    return {"trade_date": trade_date.isoformat(), "picks": picks}


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
    cur.execute("SELECT id, metrics, nav_series, positions FROM backtest_result WHERE spec_hash=%s", (spec_hash,))
    hit = cur.fetchone()
    cur.close(); conn.close()
    if hit:
        return {"cached": True, "result_id": hit[0],
                "metrics": hit[1], "nav_series": hit[2], "positions": hit[3]}

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
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """INSERT INTO backtest_result (strategy_id, spec_hash, start_date, end_date,
             metrics, nav_series, positions, cost_used, duration_ms)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (spec_hash) DO UPDATE
             SET metrics = EXCLUDED.metrics, nav_series = EXCLUDED.nav_series,
                 positions = EXCLUDED.positions, cost_used = EXCLUDED.cost_used,
                 duration_ms = EXCLUDED.duration_ms
           RETURNING id""",
        (body_strategy_id, spec_hash,
         date.fromisoformat(result["start"]), date.fromisoformat(result["end"]),
         json.dumps(result["metrics"]), json.dumps(result["nav_series"]),
         json.dumps(result.get("positions", [])), result.get("cost_used", 0),
         result.get("duration_ms", 0)),
    )
    new_id = cur.fetchone()[0]
    conn.commit(); cur.close(); conn.close()
    return {"result_id": new_id, "spec_hash": spec_hash}


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
    cur.execute("SELECT id, metrics, nav_series, positions FROM backtest_result WHERE spec_hash=%s", (spec_hash,))
    hit = cur.fetchone()
    if hit:
        cur.close(); conn.close()
        return {"result_id": hit[0], "cached": True,
                "metrics": hit[1], "nav_series": hit[2], "positions": hit[3]}

    result = backtest_engine.run_backtest(strategy, start, end, str(uid) if uid else None)
    if "error" in result:
        cur.close(); conn.close()
        return {"error": result["error"], "message": result.get("message", "")}

    positions = result.get("positions", [])
    if positions:
        name_map = strategy_engine.fetch_stock_names([p["code"] for p in positions])
        for p in positions:
            p["name"] = name_map.get(p["code"], p["code"])

    cur.execute(
        """INSERT INTO backtest_result (strategy_id, spec_hash, start_date, end_date,
             metrics, nav_series, positions, cost_used, duration_ms)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (body.strategy_id, spec_hash, start, end,
         json.dumps(result["metrics"]), json.dumps(result["nav_series"]),
         json.dumps(positions), result["cost_used"], result["duration_ms"]),
    )
    new_id = cur.fetchone()[0]
    conn.commit(); cur.close(); conn.close()

    return {"result_id": new_id, "cached": False,
            "metrics": result["metrics"], "nav_series": result["nav_series"],
            "positions": positions, "duration_ms": result["duration_ms"]}


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

    orders = []
    for p in positions:
        code = p.get("code")
        weight = float(p.get("weight") or 0)
        price = price_map.get(code)
        if not price or price <= 0 or weight <= 0:
            continue
        amount = body.total_capital * weight
        qty = int(amount / price / 100) * 100
        if qty < 100:
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
