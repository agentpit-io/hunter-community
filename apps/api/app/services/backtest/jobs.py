"""滚动回测三大作业。

每日收盘后依次执行:
  ① snapshot_job   —— 跑多因子预测, 完整留档(含8因子分值)
  ② backtest_job   —— 用今日真实收盘价, 给到期的历史预测打分
  ③ consistency_job—— 今日预测 vs 昨日预测 重叠对比 + 因子级归因

设计说明:
- 多因子预测原本只在用户访问时实时算(不落库), 本模块把它批量化并留档
- 归因原理: 综合评分 = Σ(因子分值 × 权重), 故两次预测的评分差可精确分解到各因子
"""
import asyncio
import logging
import os
from datetime import date, datetime, timedelta, timezone

import httpx
from app.services import saas_gateway as _gw

from app.services.backtest import store
from app.services.factor_engine import WEIGHTS, FACTOR_LABELS, compute_pro_prediction

log = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))   # 服务器系统时区是 UTC, 一律显式转北京时间
# 走 hunter 网关 · 同 kpred.py · 老 env KRONOS_URL 可回退直连
_CFG_CACHE: dict = {}                # 本轮运行的配置快照(供 _predict_one 内部读取)

# 以下为兜底默认值; 实际运行时从 backtest_config 表读取(admin 后台可改)
PRED_LEN = 5
CONCURRENCY = 12
MODEL_VER = "pro-v1"
FLAT_BAND = 0.5
REVERSAL_MIN = 1.0
STRENGTH_DELTA = 1.5


# ────────────────────────── ① 预测留档 ──────────────────────────

async def _predict_one(client: httpx.AsyncClient, code: str) -> list[dict] | None:
    """跑一只股票的多因子预测, 返回5条快照行。

    实测:Kronos 单次预测需 95-150 秒但支持并发, 故直接调服务保证数据新鲜。
    (不用 kronos_daily_pred 表:该表 8/3 批次 2397 条里仅 202 条指向未来,
     其余为陈旧数据, 且主键覆盖导致无历史快照)"""
    pred_len = int(_CFG_CACHE.get("pred_len") or PRED_LEN)
    retry = int(_CFG_CACHE.get("kronos_retry") or 0)
    kronos_result = None
    for attempt in range(retry + 1):
        try:
            r = await client.post(f"{_gw.kronos_url()}/predict",
                                  json={"symbol": code, "pred_len": pred_len},
                                  headers=_gw.kronos_headers())
            if r.status_code == 200:
                kronos_result = r.json()
                break
        except Exception as e:
            if attempt >= retry:
                log.debug("backtest kronos %s failed: %s", code, e)
        if attempt < retry:
            await asyncio.sleep(3)
    if not kronos_result:
        return None

    preds = kronos_result.get("predictions") or []
    if not preds:
        return None

    # 基准日 = Kronos 实际用到的最后一个交易日(它的 last_close 就是这天的收盘价)
    base_date = (kronos_result.get("last_date") or "").strip()
    if not base_date:
        log.debug("backtest %s: kronos 未返回 last_date, 跳过", code)
        return None

    # 一批任务必须用统一基准日: 收盘后日线数据陆续入库, Kronos 对不同股票可能
    # 返回不同 last_date(实测同一批里 22 只是 8/3、53 只是 8/4)。混在一起会
    # 被误当成"两次预测"做重叠对比, 产生假信号。不匹配的直接跳过。
    expect = _CFG_CACHE.get("_expect_base")
    if expect and base_date != expect:
        _CFG_CACHE.setdefault("_base_mismatch", []).append((code, base_date))
        return None

    # 只保留晚于基准日的预测:
    # Kronos 有时会把基准日本身也放进 predictions(等于"预测已知的那天"), 必须剔除,
    # 否则 T+1 会指向已经走完的交易日, 回测时用同一天的收盘价对比, 结果无意义。
    preds = [p for p in preds if (p.get("date") or "") > base_date]
    if not preds:
        log.debug("backtest %s: kronos 预测全部不晚于基准日 %s, 跳过", code, base_date)
        return None
    try:
        pro = await compute_pro_prediction(code, kronos_result, PRED_LEN)
    except Exception as e:
        log.debug("backtest factor %s failed: %s", code, e)
        return None

    # compute_pro_prediction 返回 {**kronos_result, predictions:[...], pro:{...}}
    meta = pro.get("pro") or {}
    # pro.factors 是明细列表 [{key,label,score,weight,contribution}], 转成 {key: score}
    factors = {f["key"]: f["score"] for f in (meta.get("factors") or []) if isinstance(f, dict) and "key" in f}
    last_close = float(kronos_result.get("last_close") or 0)
    if last_close <= 0:
        return None

    # confidence 可能是 "42%" 这类字符串, 转成数值
    conf = meta.get("confidence")
    if isinstance(conf, str):
        try:
            conf = float(conf.strip().rstrip("%"))
        except ValueError:
            conf = None

    run_date = date.today()
    # 因子调整后的 predictions 同样要按基准日过滤(保持与上面一致)
    adj = [p for p in (pro.get("predictions") or preds) if (p.get("date") or "") > base_date]
    rows = []
    for i, p in enumerate(adj[:PRED_LEN], start=1):
        pc = p.get("close")
        pdate = p.get("date")
        if pc is None or not pdate:
            continue
        chg = (float(pc) - last_close) / last_close * 100
        # P1 极端预测值处理(A股有涨跌停, 预测出 -16% 这类值多半是模型异常)
        from app.services.backtest.filters import clip_prediction
        chg, clipped = clip_prediction(chg, _CFG_CACHE)
        rows.append({
            "symbol": code, "run_date": run_date, "pred_date": pdate, "horizon": i,
            "base_date": base_date,   # 预测所基于的最后一个已收盘交易日
            "last_close": round(last_close, 4), "pred_close": round(float(pc), 4),
            "change_pct": round(chg, 4),
            "direction": "up" if chg > FLAT_BAND else ("down" if chg < -FLAT_BAND else "flat"),
            "score": meta.get("composite_score"), "signal": meta.get("rating"),
            "confidence": conf, "clipped": clipped,
            "factors": factors, "model_ver": MODEL_VER,
        })
    return rows or None


def _latest_trade_date() -> str:
    """日线库里的最新交易日(作为本批预测的期望基准日)"""
    try:
        from app.services.backtest.store import conn
        c = conn(); cur = c.cursor()
        # 用覆盖度足够的交易日, 避免刚开始入库时只有几只股票的日期被当成最新
        cur.execute("""SELECT trade_date FROM daily_close
                       GROUP BY trade_date HAVING count(*) > 500
                       ORDER BY trade_date DESC LIMIT 1""")
        r = cur.fetchone(); c.close()
        return r[0].isoformat() if r and r[0] else ""
    except Exception as e:
        log.warning("[backtest] 取最新交易日失败: %s", e)
        return ""


def trading_session_guard() -> str:
    """交易时段保护: 盘中不应跑预测。

    盘中 Kronos 拿到的"最新收盘价"其实是未走完的实时价, 会把它当成已收盘来预测,
    导致 T+1 指向当天、基准价随盘中波动跳变(实测中际旭创 902.50→974.99 造成假反转)。
    返回空串表示可以运行, 否则返回拒绝原因。
    """
    now = datetime.now(CST)
    if now.weekday() >= 5:
        return ""                       # 周末无行情变化, 允许补跑
    hhmm = now.hour * 100 + now.minute
    if 915 <= hhmm < 1500:
        return (f"当前 {now.strftime('%H:%M')} 为 A 股交易时段, 此时价格未定型, "
                f"预测会用到未走完的盘中价。请 15:00 收盘后再运行。")
    return ""


async def snapshot_job(symbols: list[str], progress: dict | None = None) -> dict:
    """对给定股票池跑多因子预测并留档。

    Kronos 部分复用 finance-data 已批量跑好的结果(读库),
    本任务只负责叠加8因子 + 完整留档。"""
    from app.services.backtest import filters
    from app.services.backtest.config import get_config
    cfg = get_config()
    _CFG_CACHE.clear(); _CFG_CACHE.update(cfg)

    # P0 入池过滤: 剔除次新股/低流动性/ST(这些股票的回测结果无参考价值)
    symbols, rejected = await asyncio.to_thread(filters.screen_pool, symbols, cfg)
    if rejected:
        log.info("[backtest] 入池过滤剔除 %d 只: %s", len(rejected),
                 list(rejected.items())[:5])
    if not symbols:
        return {"ok": 0, "fail": 0, "rows": 0, "rejected": rejected}

    # 本批期望的基准日 = 日线库里的最新交易日。要求所有股票的 Kronos 基准日与之一致,
    # 否则说明该股数据源尚未更新到最新, 跳过(宁可少跑, 不要混合基准日)
    expect_base = await asyncio.to_thread(_latest_trade_date)
    _CFG_CACHE["_expect_base"] = expect_base
    _CFG_CACHE["_base_mismatch"] = []
    log.info("[backtest] 本批期望基准日: %s", expect_base or "(未知, 不校验)")

    sem = asyncio.Semaphore(int(cfg.get("concurrency") or CONCURRENCY))
    all_rows: list[dict] = []
    ok = fail = 0
    total = len(symbols)
    if progress is not None:
        progress["total"] = total

    # Kronos 单次 95-150s 但支持并发, read 超时可配
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=float(cfg.get("kronos_timeout") or 300),
                              write=10.0, pool=5.0)
    ) as client:
        async def run(code: str, idx: int):
            nonlocal ok, fail
            async with sem:
                rows = await _predict_one(client, code)
                if rows:
                    all_rows.extend(rows); ok += 1
                else:
                    fail += 1
                if progress is not None:
                    progress["done"] = ok + fail
                    progress["ok"] = ok
                if (ok + fail) % 20 == 0:
                    log.info("[backtest] 进度 %d/%d (成功%d)", ok + fail, total, ok)
        await asyncio.gather(*[run(s, i) for i, s in enumerate(symbols)], return_exceptions=True)
    saved = await asyncio.to_thread(store.save_snapshot, all_rows)
    mism = _CFG_CACHE.get("_base_mismatch") or []
    if mism:
        log.warning("[backtest] %d 只因基准日不匹配被跳过(期望%s), 示例: %s",
                    len(mism), expect_base, mism[:3])
    log.info("[backtest] snapshot: %d只成功 %d只失败, 入池剔除%d只, 基准日不符%d只, 入库%d行",
             ok, fail, len(rejected), len(mism), saved)

    # 存完快照顺手发存证链接 —— 见 store.mint_tokens_for_run 的说明。
    # 存证的价值在于"预测作出的当时就有链接",事后补发证明力弱一层。
    # 失败不影响流水线(函数内部已吞异常),预测数据本身已经存好了。
    minted = 0
    if saved and expect_base:
        minted = await asyncio.to_thread(store.mint_tokens_for_run, expect_base)

    return {"ok": ok, "fail": fail, "rows": saved, "rejected": rejected,
            "base_date": expect_base, "base_mismatch": len(mism),
            "share_tokens": minted}


# ────────────────────────── ② 事后准确性回测 ──────────────────────────

def backtest_job(target: date | None = None) -> dict:
    """用 target 日的真实收盘价, 给所有以它为目标日的历史预测打分。

    两类命中:
      dir_hit  方向命中 —— 涨跌方向对不对(平盘带内双方都平也算对)
      amt_hit  幅度命中 —— 双阈值任一满足: 相对误差<=rel_err_pct 或 绝对误差<=abs_err_pp
               (单用相对误差在小涨跌时会失真: 预测0.5%实际0.1% 相对误差400%,
                但实际只差0.4个百分点, 不该判错)
    """
    from app.services.backtest import judge
    from app.services.backtest.config import get_config
    cfg = get_config()

    target = target or date.today()
    snaps = store.snapshots_for_target(target)
    if not snaps:
        log.info("[backtest] %s 无到期预测", target)
        return {"rows": 0}

    from app.services.backtest import filters
    syms = sorted({s["symbol"] for s in snaps})
    real_rows = filters.real_rows_for(syms, target)
    bench = filters.benchmark_change(target, cfg)   # 基准指数当日涨跌, 用于超额命中率
    rows = []
    skipped: dict[str, int] = {}
    for s in snaps:
        rr = real_rows.get(s["symbol"])
        # P0 数据质量过滤: 停牌/涨跌停的样本不计入统计(买不进卖不出, 预测再准也无意义)
        reason = filters.exclude_reason_for_backtest(s["symbol"], target, cfg, rr)
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        real_close = rr.get("close")
        base = s.get("last_close")
        if real_close is None or not base:
            skipped["无基准价"] = skipped.get("无基准价", 0) + 1
            continue
        real_chg = (real_close - base) / base * 100
        pred_chg = s.get("change_pct")
        if pred_chg is None:
            continue
        # 判定统一走 judge 模块(个人版查询时用的是同一套函数, 只是换成各人的参数)
        j = judge.judge_row(pred_chg, real_chg, cfg)
        rows.append({
            "symbol": s["symbol"], "run_date": s["run_date"], "pred_date": target,
            "horizon": s["horizon"], "pred_change": round(pred_chg, 4),
            "real_change": round(real_chg, 4), "abs_error": round(j["abs_error"], 4),
            "rel_error": round(j["rel_error"], 2), "amt_hit": j["amt_hit"],
            "dir_hit": j["dir_hit"], "signal": s.get("signal"),
            "base_date": s.get("base_date"),
            "factors": s.get("factors"), "model_ver": s.get("model_ver"),
        })
    n = store.save_backtest(rows)
    hit_rate = round(sum(1 for r in rows if r["dir_hit"]) / len(rows) * 100, 1) if rows else None
    amt_rate = round(sum(1 for r in rows if r["amt_hit"]) / len(rows) * 100, 1) if rows else None
    # 超额命中率: 扣掉"全部跟随大盘方向"也能猜对的部分, 才是模型真实价值
    excess = None
    if bench is not None and rows:
        naive = sum(1 for r in rows if (r["real_change"] or 0) * bench > 0) / len(rows) * 100
        if hit_rate is not None:
            excess = round(hit_rate - naive, 1)
    log.info("[backtest] %s 回测%d条(剔除%s), 方向命中%.1f%% 幅度命中%.1f%% 超额%s",
             target, n, skipped or "无", hit_rate or 0, amt_rate or 0, excess)
    return {"rows": n, "hit_rate": hit_rate, "amt_hit_rate": amt_rate,
            "excess_hit_rate": excess, "benchmark_change": bench,
            "skipped": skipped, "target": target.isoformat()}


# ────────────────────────── ③ 重叠一致性 + 归因 ──────────────────────────

def _attribute(prev_f: dict, curr_f: dict) -> tuple[dict, str | None, float | None]:
    """因子级归因: Δ贡献 = (当前分值 - 上次分值) × 权重
    返回 (各因子贡献变化, 最大贡献因子, 占比%)"""
    deltas: dict[str, float] = {}
    for k, w in WEIGHTS.items():
        a = float(prev_f.get(k) or 0)
        b = float(curr_f.get(k) or 0)
        deltas[k] = round((b - a) * w, 4)
    total = sum(abs(v) for v in deltas.values())
    if total <= 0:
        return deltas, None, None
    top = max(deltas.items(), key=lambda kv: abs(kv[1]))
    return deltas, FACTOR_LABELS.get(top[0], top[0]), round(abs(top[1]) / total * 100, 2)


def consistency_job(curr_run: date | None = None) -> dict:
    """对比最新基准日与上一个基准日的重叠预测(通常重叠4天)。

    注意用 base_date(预测基于哪天收盘)而非 run_date(哪天跑的):
    同一天可能跑多次且基准日不同, 用 run_date 会把同一天两次混为一谈。
    """
    from app.services.backtest import judge
    from app.services.backtest.config import get_config
    cfg = get_config()   # 原先 _judge 读的是模块常量, 忽略了 admin 配的 reversal_min/strength_delta
    curr_run = store.latest_base_date()
    if not curr_run:
        log.info("[backtest] 无预测数据, 跳过一致性对比")
        return {"rows": 0}
    prev_run = store.prev_run_date(curr_run)
    if not prev_run:
        log.info("[backtest] 只有一个基准日(%s), 尚无法做重叠对比", curr_run)
        return {"rows": 0}

    pairs = store.two_latest_runs(prev_run, curr_run)
    rows = []
    for (sym, pdate), runs in pairs.items():
        a = runs.get(prev_run); b = runs.get(curr_run)
        if not a or not b:
            continue            # 非重叠日
        pv, cv = a.get("change"), b.get("change")
        if pv is None or cv is None:
            continue
        vd = judge.verdict(pv, cv, cfg)
        fdelta, driver, share = _attribute(a.get("factors") or {}, b.get("factors") or {})
        rows.append({
            "symbol": sym, "pred_date": pdate, "prev_run": prev_run, "curr_run": curr_run,
            "prev_change": round(pv, 4), "curr_change": round(cv, 4),
            "delta": round(cv - pv, 4), "verdict": vd,
            "factor_delta": fdelta, "top_driver": driver, "driver_share": share,
        })
    n = store.save_consistency(rows)
    rev = sum(1 for r in rows if r["verdict"] == "reversal")
    log.info("[backtest] 一致性: 重叠%d条, 反转%d条 (%s vs %s)", n, rev, prev_run, curr_run)
    return {"rows": n, "reversals": rev, "prev_run": prev_run.isoformat(),
            "curr_run": curr_run.isoformat()}


# ────────────────────────── 每日总入口 ──────────────────────────

async def daily_pipeline(symbols: list[str], progress: dict | None = None) -> dict:
    """收盘后一条流水线: 预测留档 → 事后回测 → 重叠一致性 → 过期数据清理"""
    from app.services.backtest import filters
    from app.services.backtest.config import get_config
    snap = await snapshot_job(symbols, progress)
    bt = await asyncio.to_thread(backtest_job)
    cons = await asyncio.to_thread(consistency_job)
    purge = await asyncio.to_thread(filters.purge_old, get_config())
    # P2 命中率告警
    try:
        cfg = get_config()
        thr = float(cfg.get("alert_hit_rate") or 0)
        hr = bt.get("hit_rate")
        if thr and hr is not None and hr < thr:
            log.warning("[backtest] ⚠ 方向命中率 %.1f%% 低于告警线 %.1f%%, 模型可能异常", hr, thr)
    except Exception:
        pass
    return {"snapshot": snap, "backtest": bt, "consistency": cons, "purge": purge}
