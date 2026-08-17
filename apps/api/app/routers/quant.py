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

from fastapi import APIRouter, HTTPException, Request
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
          AND (bt.end_date - bt.start_date) >= {period_days}
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


@router.post("/backtest/run")
async def run_backtest_ep(body: BacktestIn, request: Request):
    uid = getattr(request.state, "user_id", None)
    # 解 spec
    if body.strategy_id:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT factors, config FROM strategy WHERE id=%s", (body.strategy_id,))
        r = cur.fetchone()
        cur.close(); conn.close()
        if not r:
            raise HTTPException(404, f"strategy {body.strategy_id} not found")
        strategy = {"factors": r[0], "config": r[1]}
    elif body.factors and body.config:
        strategy = {"factors": body.factors, "config": body.config}
    else:
        raise HTTPException(400, "必须提供 strategy_id 或 factors+config")

    end = date.fromisoformat(body.end) if body.end else date.today()
    start = date.fromisoformat(body.start) if body.start else (end - timedelta(days=365))

    # 检 cache
    spec_hash = backtest_engine.compute_spec_hash(strategy, start, end)
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, metrics, nav_series, positions FROM backtest_result WHERE spec_hash=%s", (spec_hash,))
    hit = cur.fetchone()
    if hit:
        cur.close(); conn.close()
        return {"result_id": hit[0], "cached": True,
                "metrics": hit[1], "nav_series": hit[2], "positions": hit[3]}

    # 跑回测
    result = backtest_engine.run_backtest(strategy, start, end, str(uid) if uid else None)
    if "error" in result:
        cur.close(); conn.close()
        return {"error": result["error"], "message": result.get("message", "")}

    # 补 positions 里的 name(前端展示用)
    positions = result.get("positions", [])
    if positions:
        name_map = strategy_engine.fetch_stock_names([p["code"] for p in positions])
        for p in positions:
            p["name"] = name_map.get(p["code"], p["code"])

    # 落库
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
