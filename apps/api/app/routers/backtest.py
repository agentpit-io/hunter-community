"""预测回测 API —— /api/backtest/*

公开只读(市场统计数据, 无用户隐私):
  GET /api/backtest/accuracy      模型准确度(方向命中率/误差, 分horizon/分信号档)
  GET /api/backtest/consistency   稳定性(一致/强化/弱化/反转分布 + 反转主因排行)
  GET /api/backtest/reversals     预测反转清单(含因子级归因)
  GET /api/backtest/evolution/{code}  单股预测演变轨迹
需管理员:
  POST /api/backtest/run          手动触发一次每日流水线(测试用)
"""
import asyncio
import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.services.backtest import store, jobs
from app.services.factor_engine import FACTOR_LABELS

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/backtest/accuracy")
async def get_accuracy(days: int = Query(30, ge=1, le=365), symbol: str = ""):
    """事后准确性: 预测 vs 真实收盘"""
    try:
        return await asyncio.to_thread(store.accuracy_stats, days, symbol.strip())
    except Exception as e:
        log.warning("accuracy stats failed: %s", e)
        raise HTTPException(503, "回测数据暂不可用")


@router.get("/backtest/consistency")
async def get_consistency(days: int = Query(30, ge=1, le=365)):
    """重叠一致性: 相邻两次预测的稳定性分布 + 反转主因"""
    try:
        d = await asyncio.to_thread(store.consistency_stats, days)
        d["top_reversal_drivers"] = [
            {**x, "label": FACTOR_LABELS.get(x["factor"], x["factor"])}
            for x in d.get("top_reversal_drivers", [])
        ]
        return d
    except Exception as e:
        log.warning("consistency stats failed: %s", e)
        raise HTTPException(503, "回测数据暂不可用")


@router.get("/backtest/reversals")
async def get_reversals(run_date: str = "", limit: int = Query(20, ge=1, le=100)):
    """预测反转清单: 昨天看涨今天看跌(或反之)的股票 + 为什么变卦"""
    d = None
    if run_date:
        try:
            d = date.fromisoformat(run_date)
        except ValueError:
            raise HTTPException(400, "run_date 格式应为 YYYY-MM-DD")
    try:
        items = await asyncio.to_thread(store.reversals, d, limit)
    except Exception as e:
        log.warning("reversals failed: %s", e)
        raise HTTPException(503, "回测数据暂不可用")

    for it in items:
        drv = it.get("top_driver") or ""
        share = it.get("driver_share")
        pv, cv = it.get("prev_change"), it.get("curr_change")
        it["explain"] = (
            f"{it['prev_run'][5:]} 预测 {it['pred_date'][5:]} {pv:+.1f}%,"
            f"{it['curr_run'][5:]} 改判为 {cv:+.1f}%"
            + (f";主因 {drv}(贡献 {share:.0f}%)" if drv and share else "")
        ) if pv is not None and cv is not None else ""
    return {"items": items, "count": len(items)}


@router.get("/backtest/evolution/{code}")
async def get_evolution(code: str, limit: int = Query(40, ge=1, le=200)):
    """单股预测演变(轻量版)"""
    try:
        rows = await asyncio.to_thread(store.symbol_evolution, code.strip(), limit)
        acc = await asyncio.to_thread(store.accuracy_stats, 60, code.strip())
    except Exception as e:
        log.warning("evolution failed: %s", e)
        raise HTTPException(503, "回测数据暂不可用")
    return {"symbol": code, "evolution": rows, "accuracy": acc}


@router.get("/backtest/stock/{code}")
async def get_stock_detail(code: str, runs: int = Query(5, ge=1, le=20)):
    """单股回测详情: 近N次预测(每次5天) + 实际结果对照 + 8因子 + 反转记录"""
    code = code.strip()
    try:
        detail = await asyncio.to_thread(store.symbol_detail, code, runs)
        acc = await asyncio.to_thread(store.accuracy_stats, 60, code)
    except Exception as e:
        log.warning("stock detail failed: %s", e)
        raise HTTPException(503, "回测数据暂不可用")
    # 补名称(来自产业链定义或自定义池)
    name = ""
    try:
        from app.services.backtest.config import chain_stocks, custom_pool
        for s in chain_stocks(False) + custom_pool(False):
            if s.get("symbol") == code:
                name = s.get("name") or ""
                break
    except Exception:
        pass
    detail["name"] = name
    detail["accuracy"] = acc
    detail["factor_labels"] = FACTOR_LABELS
    return detail


# 手动运行的进度状态(单实例内存态, 够用)
_RUN_STATE: dict = {"running": False, "started_at": "", "total": 0,
                    "done": 0, "ok": 0, "result": None, "error": ""}


async def _run_pipeline_bg(symbols: list[str]):
    """后台跑流水线, 期间更新 _RUN_STATE 供前端轮询。
    (整个流程约 12 分钟, 远超 Nginx 120s 超时, 不能同步等待)"""
    from datetime import datetime
    _RUN_STATE.update({"running": True, "started_at": datetime.now().isoformat(timespec="seconds"),
                       "total": len(symbols), "done": 0, "ok": 0, "result": None, "error": ""})
    try:
        result = await jobs.daily_pipeline(symbols, progress=_RUN_STATE)
        _RUN_STATE["result"] = result
    except Exception as e:
        log.warning("backtest manual run failed: %s", e)
        _RUN_STATE["error"] = str(e)[:200]
    finally:
        _RUN_STATE["running"] = False


@router.post("/backtest/run")
async def trigger_run(request: Request):
    """手动触发一次回测流水线(异步, 立即返回; 用 /backtest/run/status 查进度)"""
    _require_admin(request)
    if _RUN_STATE.get("running"):
        return {"ok": False, "msg": "已有任务在运行中", "state": _RUN_STATE}
    block = jobs.trading_session_guard()
    if block:
        raise HTTPException(400, block)
    from app.services.backtest.config import resolve_pool
    symbols = await asyncio.to_thread(resolve_pool)
    if not symbols:
        raise HTTPException(400, "股票池为空")
    asyncio.create_task(_run_pipeline_bg(symbols))
    return {"ok": True, "msg": f"已在后台启动, 共 {len(symbols)} 只, 预计 {max(1, len(symbols)//6)} 分钟",
            "total": len(symbols)}


@router.get("/backtest/run/status")
async def run_status(request: Request):
    """查手动运行的进度"""
    _require_admin(request)
    return dict(_RUN_STATE)


# ────────────────────────── Admin 配置与股票池 ──────────────────────────

def _require_admin(request: Request) -> str:
    """回测配置属模型运维, 仅管理员可改。

    判定口径与展位后台(ax_event)、前端 Sidebar 保持一致:
    JWT 里 role == 'ADMIN' 或 email 在管理员白名单中。
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    try:
        import jwt as jwt_lib
        from app.routers.auth import JWT_SECRET
        from app.routers.ax_event import _ADMIN_EMAILS
        payload = jwt_lib.decode(auth[7:], JWT_SECRET, algorithms=["HS256"])
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "token 无效")
    email = (payload.get("email") or "").lower()
    if payload.get("role") != "ADMIN" and email not in _ADMIN_EMAILS:
        raise HTTPException(403, "仅管理员可操作回测配置")
    return payload.get("sub") or email


@router.get("/backtest/config")
async def get_cfg(request: Request):
    _require_admin(request)
    from app.services.backtest import config as bc
    cfg = await asyncio.to_thread(bc.get_config)
    pool = await asyncio.to_thread(bc.resolve_pool, cfg)
    return {"config": cfg, "pool_size": len(pool)}


class ConfigPatch(BaseModel):
    pool_mode: str | None = None
    pred_len: int | None = None
    run_hour: int | None = None
    run_minute: int | None = None
    concurrency: int | None = None
    enabled: bool | None = None
    flat_band: float | None = None
    strict_dir: bool | None = None
    rel_err_pct: float | None = None
    abs_err_pp: float | None = None
    reversal_min: float | None = None
    strength_delta: float | None = None
    driver_min_share: float | None = None
    # P0 数据质量
    skip_suspended: bool | None = None
    skip_limit: bool | None = None
    skip_st: bool | None = None
    min_list_days: int | None = None
    min_amount_wan: int | None = None
    adjust_mode: str | None = None
    # P1 基准与极端值
    benchmark_code: str | None = None
    max_pred_pct: float | None = None
    outlier_mode: str | None = None
    # P2 运维
    retain_days: int | None = None
    kronos_retry: int | None = None
    kronos_timeout: int | None = None
    alert_hit_rate: float | None = None


@router.put("/backtest/config")
async def put_cfg(body: ConfigPatch, request: Request):
    uid = _require_admin(request)
    from app.services.backtest import config as bc
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if patch.get("pool_mode") and patch["pool_mode"] not in ("core", "chain_all", "watchlist", "custom"):
        raise HTTPException(400, "pool_mode 取值: core/chain_all/watchlist/custom")
    cfg = await asyncio.to_thread(bc.save_config, patch, str(uid))
    pool = await asyncio.to_thread(bc.resolve_pool, cfg)
    return {"config": cfg, "pool_size": len(pool)}


@router.get("/backtest/pool")
async def get_pool(request: Request):
    """当前生效的股票池 + 自定义池明细"""
    _require_admin(request)
    from app.services.backtest import config as bc
    cfg = await asyncio.to_thread(bc.get_config)
    codes = await asyncio.to_thread(bc.resolve_pool, cfg)
    custom = await asyncio.to_thread(bc.custom_pool, False)
    chains = await asyncio.to_thread(bc.chain_stocks, True)
    name_map = {s["symbol"]: s for s in chains}
    for s in custom:
        name_map.setdefault(s["symbol"], s)
    # 个人版上线后, 生效池 = 全局池 ∪ 全部用户池。标出哪些是用户加的, 避免 admin 看到
    # 池子里冒出不认识的股票以为配错了。
    from app.services.backtest.user_store import all_user_symbols
    user_syms = set(await asyncio.to_thread(all_user_symbols))
    global_codes = set(await asyncio.to_thread(bc.resolve_pool, cfg, False))
    return {
        "pool_mode": cfg.get("pool_mode"),
        "effective": [{"symbol": c, "name": name_map.get(c, {}).get("name", ""),
                       "chain": name_map.get(c, {}).get("chain", ""),
                       "from_user": c in user_syms and c not in global_codes} for c in codes],
        "custom": custom,
        "count": len(codes),
        "global_count": len(global_codes),
        "user_added": len(user_syms - global_codes),
    }


class PoolItem(BaseModel):
    symbol: str
    name: str = ""


@router.post("/backtest/pool")
async def add_pool(body: PoolItem, request: Request):
    uid = _require_admin(request)
    from app.services.backtest import config as bc
    code = body.symbol.strip()
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(400, "股票代码应为6位数字")
    await asyncio.to_thread(bc.add_to_pool, code, body.name, "", "manual", str(uid))
    return {"ok": True, "symbol": code}


@router.delete("/backtest/pool/{symbol}")
async def del_pool(symbol: str, request: Request, hard: bool = False):
    _require_admin(request)
    from app.services.backtest import config as bc
    ok = await asyncio.to_thread(bc.remove_from_pool, symbol, hard)
    return {"ok": ok, "symbol": symbol}


@router.post("/backtest/pool/import-chains")
async def import_chains(request: Request, rep_only: bool = True):
    """一键把29条产业链的股票导入自定义池"""
    uid = _require_admin(request)
    from app.services.backtest import config as bc
    n = await asyncio.to_thread(bc.import_chains_to_pool, rep_only, str(uid))
    return {"ok": True, "imported": n}


@router.get("/backtest/parity")
async def get_parity(request: Request, days: int = Query(90, ge=1, le=365)):
    """验收自检: 库里存的命中判定 vs 现在按同一套全局参数算出来的, 必须逐行一致。

    判定逻辑从 jobs.py 抽到 judge.py 供个人版复用, 这个接口用来证明搬家没搬丢东西。
    """
    _require_admin(request)
    from app.services.backtest import user_store as us
    return await asyncio.to_thread(us.parity_check, days)


# ────────────────────────── 个人版(登录用户) ──────────────────────────
# 每人一个股票池 + 一套判定参数。预测数据是公用的, 差异只在"用谁的参数解读"。

def _uid(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, "需要登录")
    return str(uid)


@router.get("/backtest/my/summary")
async def my_summary(request: Request, days: int = Query(30, ge=1, le=365)):
    """我的回测成绩单: 总命中率 + 分预测天数衰减 + 每只股票表现"""
    uid = _uid(request)
    from app.services.backtest import user_store as us
    try:
        return await asyncio.to_thread(us.user_summary, uid, days)
    except Exception as e:
        log.warning("my summary failed: %s", e)
        raise HTTPException(503, "回测数据暂不可用")


@router.get("/backtest/my/pool")
async def my_pool(request: Request):
    uid = _uid(request)
    from app.services.backtest import user_store as us
    items = await asyncio.to_thread(us.list_user_pool, uid)
    q = await asyncio.to_thread(us.quota_for, uid)
    return {"items": items, "count": len(items), **q}


class MyPoolItem(BaseModel):
    symbol: str
    name: str = ""


@router.post("/backtest/my/pool")
async def my_pool_add(body: MyPoolItem, request: Request):
    uid = _uid(request)
    from app.services.backtest import user_store as us
    ok, msg = await asyncio.to_thread(us.add_user_pool, uid, body.symbol, body.name)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "symbol": body.symbol.strip(), "msg": msg}


@router.delete("/backtest/my/pool/{symbol}")
async def my_pool_del(symbol: str, request: Request):
    uid = _uid(request)
    from app.services.backtest import user_store as us
    ok = await asyncio.to_thread(us.remove_user_pool, uid, symbol)
    return {"ok": ok, "symbol": symbol}


@router.get("/backtest/my/config")
async def my_config(request: Request):
    uid = _uid(request)
    from app.services.backtest import user_store as us
    cfg = await asyncio.to_thread(us.get_user_config, uid)
    return {"config": cfg, "fields": list(us.USER_FIELDS), "defaults": us.USER_DEFAULTS}


class MyConfigPatch(BaseModel):
    pred_len: int | None = None
    flat_band: float | None = None
    rel_err_pct: float | None = None
    abs_err_pp: float | None = None
    reversal_min: float | None = None
    strength_delta: float | None = None
    skip_limit: bool | None = None
    skip_st: bool | None = None


# 参数有合理区间, 越界会让统计失去意义(如平盘带设 20% 则全部算"看平")
_RANGES = {
    "pred_len": (1, 10), "flat_band": (0.0, 5.0), "rel_err_pct": (1.0, 200.0),
    "abs_err_pp": (0.1, 10.0), "reversal_min": (0.0, 10.0), "strength_delta": (0.1, 10.0),
}


@router.put("/backtest/my/config")
async def my_config_save(body: MyConfigPatch, request: Request):
    uid = _uid(request)
    from app.services.backtest import user_store as us
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    for k, (lo, hi) in _RANGES.items():
        if k in patch and not (lo <= float(patch[k]) <= hi):
            raise HTTPException(400, f"{k} 应在 {lo}~{hi} 之间")
    cfg = await asyncio.to_thread(us.save_user_config, uid, patch)
    return {"config": cfg, "msg": "参数已保存 · 历史统计已按新口径重新计算"}


@router.delete("/backtest/my/config")
async def my_config_reset(request: Request):
    uid = _uid(request)
    from app.services.backtest import user_store as us
    cfg = await asyncio.to_thread(us.reset_user_config, uid)
    return {"config": cfg, "msg": "已恢复默认"}


@router.get("/backtest/my/stock/{code}")
async def my_stock(code: str, request: Request, runs: int = Query(5, ge=1, le=20)):
    """单股详情(按我的参数判定): 近N次预测 + 到期结果对照 + 反转记录"""
    uid = _uid(request)
    from app.services.backtest import user_store as us
    try:
        d = await asyncio.to_thread(us.user_stock_detail, uid, code, runs)
    except Exception as e:
        log.warning("my stock detail failed: %s", e)
        raise HTTPException(503, "回测数据暂不可用")
    d["factor_labels"] = FACTOR_LABELS
    return d
