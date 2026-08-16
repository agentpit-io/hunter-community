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

    # 落库
    cur.execute(
        """INSERT INTO backtest_result (strategy_id, spec_hash, start_date, end_date,
             metrics, nav_series, positions, cost_used, duration_ms)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (body.strategy_id, spec_hash, start, end,
         json.dumps(result["metrics"]), json.dumps(result["nav_series"]),
         json.dumps(result["positions"]), result["cost_used"], result["duration_ms"]),
    )
    new_id = cur.fetchone()[0]
    conn.commit(); cur.close(); conn.close()

    return {"result_id": new_id, "cached": False,
            "metrics": result["metrics"], "nav_series": result["nav_series"],
            "positions": result["positions"], "duration_ms": result["duration_ms"]}
