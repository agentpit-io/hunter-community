import asyncio
import os
from typing import Optional
import httpx
from app.services import saas_gateway as _gw
from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from app.services.factor_engine import compute_pro_prediction, apply_daily_limit, WEIGHTS

router = APIRouter()


# ── 陈旧数据检测 & 自动重锚定 ────────────────────────────────────────────────

def _get_latest_close_finance_data(bare: str) -> tuple[float, str]:
    """finance-data 中台拉最新日线收盘 + 日期(内网稳定,优先路径)。
    返回 (close, YYYY-MM-DD) 或 (0.0, "")。"""
    try:
        from app.services.finance_data_client import get_kline
        # 兼容多种代码格式:传原始 bare(6 位数字)给中台
        bars = get_kline(bare, period="daily", limit=5)
        if not bars:
            return 0.0, ""
        last = bars[-1]
        close = float(last.get("close") or 0)
        ts = (last.get("ts") or "")[:10]
        if close > 0 and ts:
            return close, ts
    except Exception as e:
        logger.warning("finance-data latest close failed for {}: {}", bare, e)
    return 0.0, ""


def _get_latest_close_akshare(bare: str) -> tuple[float, str]:
    """akshare fallback(GCP 到大陆财经 API 常被拦, 已由 finance-data 优先兜底)。"""
    try:
        import akshare as ak
        from datetime import datetime, timedelta
        end = datetime.now()
        start = end - timedelta(days=14)
        df = ak.stock_zh_a_hist(
            symbol=bare, period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="qfq",
        )
        if df is None or df.empty:
            return 0.0, ""
        last = df.iloc[-1]
        close = float(str(last.get("收盘", 0) or 0).replace(",", ""))
        dt = str(last.get("日期", ""))[:10]
        return close, dt
    except Exception as e:
        logger.warning("akshare latest close failed for {}: {}", bare, e)
        return 0.0, ""


# Yahoo A 股符号后缀
def _yahoo_a_symbol(bare: str) -> str | None:
    """A 股 code → Yahoo symbol。北交所(43/83/87/88)Yahoo 支持差,返 None 让上层跳过。"""
    if bare.startswith(("60", "68", "11", "51", "52")):
        return f"{bare}.SS"  # 沪
    if bare.startswith(("43", "83", "87", "88")):
        return None          # 北交所跳过
    if bare.startswith(("00", "30")):
        return f"{bare}.SZ"  # 深/创业板
    return None


# Yahoo 拉取结果 Redis 缓存 5 分钟,避免同一冷门股在短时间内反复打 Yahoo
_YAHOO_CACHE_TTL = 300


def _get_latest_close_yahoo(bare: str) -> tuple[float, str]:
    """Yahoo Finance 拉最新日线收盘,GCP 可达,是 finance-data 陈旧时的关键兜底。
    诊断证实: A 股(如 300795/300506)Yahoo 有当日 EOD 数据,与官方源收盘价一致。"""
    yh_sym = _yahoo_a_symbol(bare)
    if not yh_sym:
        return 0.0, ""

    # 1) 尝试 Redis 缓存
    cache_key = f"kpred:yahoo:latest:{yh_sym}"
    try:
        from app.services.gm.yahoo_hk import _cache_get as _rd_get
        cached = _rd_get(cache_key)
        if cached and isinstance(cached, dict):
            return float(cached.get("close") or 0), str(cached.get("date") or "")
    except Exception:
        pass

    # 2) HTTP 请求
    try:
        import httpx as _httpx
        from datetime import datetime as _dt
        with _httpx.Client(timeout=6.0) as c:
            r = c.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{yh_sym}",
                params={"interval": "1d", "range": "5d"},
                headers={"User-Agent": "Mozilla/5.0"},
            )
        r.raise_for_status()
        d = r.json()
        result = ((d.get("chart") or {}).get("result") or [{}])[0]
        ts_list = result.get("timestamp") or []
        q = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        closes = q.get("close") or []
        # 从尾部往前找最后一根 close 非空的 bar
        for i in range(len(ts_list) - 1, -1, -1):
            if i < len(closes) and closes[i] is not None:
                date_str = _dt.utcfromtimestamp(ts_list[i]).strftime("%Y-%m-%d")
                close = round(float(closes[i]), 2)
                # 写回缓存(异步失败可忽略)
                try:
                    from app.services.gm.yahoo_hk import _cache_set as _rd_set
                    _rd_set(cache_key, {"close": close, "date": date_str}, _YAHOO_CACHE_TTL)
                except Exception:
                    pass
                logger.info("kpred {} yahoo hit: {} @ {}", bare, close, date_str)
                return close, date_str
    except Exception as e:
        logger.warning("kpred {} yahoo latest close failed: {}", bare, e)
    return 0.0, ""


def _get_latest_close(bare: str, min_date: str = "") -> tuple[float, str]:
    """三层降级拉最新日线收盘。
      1) finance-data 内网中台 (~50ms, 但对未订阅的冷门股会陈旧)
      2) Yahoo Finance 公网 (~1-3s, GCP 可达, A 股有当日 EOD)
      3) akshare 公网 (~2s, GCP 常被 RemoteDisconnected, 保留兜底)

    智能降级:若 min_date 有值(通常是 kronos 陈旧日期),仅接受"日期 > min_date"
    的结果;否则某源虽然返回但日期仍陈旧,就继续下一层。避免"finance-data
    返回 07-02 → 上层判定 fallback 成功但其实还是陈旧"的假成功。
    """
    def _is_fresh(dt: str) -> bool:
        return bool(dt) and (not min_date or dt > min_date)

    close, dt = _get_latest_close_finance_data(bare)
    if close > 0 and _is_fresh(dt):
        return close, dt
    if close > 0 and dt and not _is_fresh(dt):
        logger.info("kpred {}: finance-data 数据陈旧({} ≤ kronos {}), 尝试 Yahoo", bare, dt, min_date)
    else:
        logger.info("kpred {}: finance-data 无数据, 尝试 Yahoo", bare)

    close2, dt2 = _get_latest_close_yahoo(bare)
    if close2 > 0 and _is_fresh(dt2):
        return close2, dt2
    if close2 > 0 and dt2:
        logger.info("kpred {}: Yahoo 也陈旧({}), 尝试 akshare", bare, dt2)

    close3, dt3 = _get_latest_close_akshare(bare)
    if close3 > 0 and _is_fresh(dt3):
        return close3, dt3

    # 三层都没拉到更新的,只能退回最不差的(第一层非空结果),或全空
    if close > 0 and dt: return close, dt
    if close2 > 0 and dt2: return close2, dt2
    if close3 > 0 and dt3: return close3, dt3
    return 0.0, ""


async def _fix_stale_kronos_result(code: str, kronos_result: dict) -> tuple[dict, str | None]:
    """若 Kronos 返回的 last_date > 3 天前（非 watchlist 股票），
    立即用 akshare 拉最新收盘，按比例重新锚定预测日期和价格，
    并后台订阅 watchlist，确保下次调用时数据实时。"""
    from datetime import date, timedelta

    kronos_last_str = kronos_result.get("last_date", "")
    if not kronos_last_str:
        return kronos_result, None
    try:
        kronos_date = date.fromisoformat(kronos_last_str)
    except Exception:
        return kronos_result, None

    if (date.today() - kronos_date).days <= 3:
        return kronos_result, None  # 数据新鲜，无需处理

    logger.info("kpred {}: stale data (last_date={}), fetching fresh close from akshare", code, kronos_last_str)

    bare = code.split(".")[0] if "." in code else code
    loop = asyncio.get_event_loop()
    # 传 kronos_last_str 作为 min_date,三层降级只接受"更新的日期"
    actual_close, actual_date_str = await loop.run_in_executor(
        None, lambda: _get_latest_close(bare, min_date=kronos_last_str)
    )

    stale_days = (date.today() - kronos_date).days

    # 两种 fallback 数据都拉不到 → 至少告诉前端"数据陈旧"这个事实
    if not actual_close or not actual_date_str:
        logger.warning("kpred {}: fallback 数据源均无 300795 类小盘股数据, 返回原始陈旧结果", code)
        _bg_subscribe_stock(code)
        note = (f"该股不在活跃订阅池,数据截止 {kronos_last_str}(约 {stale_days} 天前),"
                "已启动订阅,下个交易日后自动更新")
        return kronos_result, note

    try:
        actual_date = date.fromisoformat(actual_date_str)
    except Exception:
        return kronos_result, None

    preds = kronos_result.get("predictions", [])
    if not preds:
        return kronos_result, None

    # 生成实际的未来交易日（跳过周末，不含节假日 — 足够精确）
    new_dates: list[str] = []
    cursor = actual_date + timedelta(days=1)
    while len(new_dates) < len(preds):
        if cursor.weekday() < 5:
            new_dates.append(cursor.isoformat())
        cursor += timedelta(days=1)

    # 按 actual_close / kronos_last_close 比例缩放预测价格
    kronos_last_close = kronos_result.get("last_close") or actual_close
    scale = actual_close / kronos_last_close if kronos_last_close > 0 else 1.0

    new_preds = []
    for pred, new_date in zip(preds, new_dates):
        new_preds.append({
            **pred,
            "date":  new_date,
            "open":  round((pred.get("open")  or kronos_last_close) * scale, 2),
            "high":  round((pred.get("high")  or kronos_last_close) * scale, 2),
            "low":   round((pred.get("low")   or kronos_last_close) * scale, 2),
            "close": round((pred.get("close") or kronos_last_close) * scale, 2),
        })

    # 后台订阅,下次采集周期后自动跟上
    _bg_subscribe_stock(bare)

    patched = {
        **kronos_result,
        "last_date":   actual_date_str,
        "last_close":  actual_close,
        "predictions": new_preds,
    }

    # 关键场景: fallback 拿到的日期与 Kronos 原日期相同(300795 米奥会展典型症状) —
    # 说明两个源都停在同一天,不在活跃订阅池。此时 patched 的日期没变,但仍要告诉前端
    # "该数据陈旧",UI 才能显示警告而不是当作"今日"。
    if actual_date_str == kronos_last_str:
        note = (
            f"该股不在活跃订阅池,数据截止 {actual_date_str}(约 {stale_days} 天前),"
            "已启动订阅,下个交易日后自动更新"
        )
    else:
        note = (
            f"基础数据已从 {kronos_last_str} 自动同步至 {actual_date_str}(来源:实时行情),"
            "已订阅自动更新,下次调用将更精确"
        )
    return patched, note


def _bg_subscribe_stock(code: str) -> None:
    """异步触发 finance-data 订阅,不阻塞主流程。code 可带 .SH/.SZ 后缀。"""
    bare = code.split(".")[0] if "." in code else code
    async def _do():
        try:
            from app.services.finance_data_client import subscribe
            if bare.startswith(("60", "68", "11", "51", "52")):
                exchange = "SH"
            elif bare.startswith(("43", "83", "87", "88")):
                exchange = "BJ"
            else:
                exchange = "SZ"
            subscribe(bare, bare, "A", exchange, "stock")
        except Exception:
            pass
    try:
        asyncio.create_task(_do())
    except RuntimeError:
        pass  # 无 event loop,忽略

# ── Kronos 未收录时的降级: 拉 Yahoo 历史 + 因子引擎独立跑 ────────────────────

def _get_history_yahoo(bare: str, limit: int = 80) -> list[dict]:
    """Yahoo 拉 A 股完整日线历史(OHLCV),用于 Kronos 未收录时的降级预测。"""
    yh_sym = _yahoo_a_symbol(bare)
    if not yh_sym:
        return []
    try:
        import httpx as _httpx
        from datetime import datetime as _dt
        with _httpx.Client(timeout=8.0) as c:
            r = c.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{yh_sym}",
                params={"interval": "1d", "range": "6mo"},   # 6 月约 120 交易日
                headers={"User-Agent": "Mozilla/5.0"},
            )
        r.raise_for_status()
        result = ((r.json().get("chart") or {}).get("result") or [{}])[0]
        ts_list = result.get("timestamp") or []
        q = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        opens  = q.get("open") or []
        highs  = q.get("high") or []
        lows   = q.get("low") or []
        closes = q.get("close") or []
        vols   = q.get("volume") or []
        bars: list[dict] = []
        for i, ts in enumerate(ts_list):
            if i >= len(closes) or closes[i] is None:
                continue
            c_ = float(closes[i])
            bars.append({
                "date":   _dt.utcfromtimestamp(ts).strftime("%Y-%m-%d"),
                "open":   float(opens[i])  if i < len(opens)  and opens[i]  is not None else c_,
                "high":   float(highs[i])  if i < len(highs)  and highs[i]  is not None else c_,
                "low":    float(lows[i])   if i < len(lows)   and lows[i]   is not None else c_,
                "close":  c_,
                "volume": int(vols[i])     if i < len(vols)   and vols[i]   is not None else 0,
            })
        return bars[-limit:]
    except Exception as e:
        logger.warning("kpred %s yahoo history failed: %s", bare, e)
        return []


def _synthesize_predictions(last_close: float, last_date: str, pred_len: int,
                            factor_return_pct: float) -> list[dict]:
    """因子引擎给出 pred_len 天预期收益率(百分数),线性合成 pred_len 根未来 K 线。
    形态: 每根按等比推进 open→close,±0.5% 高低波动。仅用于 Kronos 未收录时的替代展示。"""
    from datetime import date as _d, timedelta as _td
    try:
        start = _d.fromisoformat(last_date[:10])
    except Exception:
        start = _d.today()
    dates: list[str] = []
    cur = start + _td(days=1)
    while len(dates) < pred_len:
        if cur.weekday() < 5:
            dates.append(cur.isoformat())
        cur += _td(days=1)
    total_return = (factor_return_pct or 0.0) / 100.0
    step = total_return / max(pred_len, 1)
    preds: list[dict] = []
    for i, dt in enumerate(dates):
        prev_p = last_close * (1 + step * i)
        next_p = last_close * (1 + step * (i + 1))
        preds.append({
            "date":   dt,
            "open":   round(prev_p, 2),
            "close":  round(next_p, 2),
            "high":   round(max(prev_p, next_p) * 1.005, 2),
            "low":    round(min(prev_p, next_p) * 0.995, 2),
            "volume": 0,
        })
    return preds


async def _compute_no_kronos_prediction(
    code: str, pred_len: int, custom_weights: dict | None = None,
) -> dict:
    """Kronos 未收录该股(通常次新股/新股)时的降级方案:
      1) Yahoo 拉 6 个月历史(拿不到就 404 报错)
      2) 复用 factor_engine,把 kronos 因子权重归零,其他 7 因子归一化
      3) 依据 factor_return 合成一组线性预测 K 线
      4) 返回结构与 Pro 端点一致 + kronos_skipped=True + data_note 解释
    """
    bare = code.split(".")[0] if "." in code else code
    loop = asyncio.get_event_loop()
    history = await loop.run_in_executor(None, lambda: _get_history_yahoo(bare, 80))
    # 阈值放宽到 3 天: 极新股 (688825 长鑫科技 IPO 才 5-7 天) 也能靠 main_flow
    # (资金流) + pe (估值) + candle_5d 3 个短序列因子给出方向,其他长序列因子
    # (ma_align 60d / macd 35d / rsi 15d) 数据不足时得 0 分,不影响综合评级输出。
    if len(history) < 5:
        raise HTTPException(
            404,
            f"该股 Kronos 未收录, 且 Yahoo 历史仅 {len(history)} 天(至少 5 天), 暂无法分析",
        )

    last_close = float(history[-1]["close"])
    last_date  = str(history[-1]["date"])

    # 构造 fake kronos_result 供 compute_pro_prediction 使用(predictions 留空)
    fake_kronos = {
        "symbol": bare, "name": bare,
        "last_date": last_date, "last_close": last_close,
        "history": history, "predictions": [],
    }
    # kronos 权重归零,其他因子按原比例归一化到 1
    base_w = dict(custom_weights or WEIGHTS)
    base_w["kronos"] = 0.0
    total = sum(v for v in base_w.values() if v > 0)
    no_k_weights = {k: (v / total if v > 0 and total > 0 else 0.0)
                    for k, v in base_w.items()}

    pro_result = await compute_pro_prediction(bare, fake_kronos, pred_len, no_k_weights)

    # compute_pro_prediction 由于 predictions=[] 会返 predictions=[],
    # 用因子引擎给的 factor_return 手工合成
    factor_return_pct = pro_result.get("pro", {}).get("factor_return_pct", 0.0)
    synthetic = _synthesize_predictions(last_close, last_date, pred_len, factor_return_pct)
    synthetic = apply_daily_limit(synthetic, last_close, bare)
    pro_result["predictions"] = synthetic

    # 标记 + 说明,让前端能识别并展示黄色横幅
    pro_result["kronos_skipped"] = True
    pro_result["data_note"] = (
        "该股 Kronos 尚未收录(通常是次新股或数据缺失), 已跳过 Kronos 维度, "
        "基于 均线/MACD/RSI/主力资金/筹码/近5日K线/PE 7 个技术因子独立分析。"
        "未来预测走势为线性趋势合成, 请结合基本面参考。"
    )
    return pro_result


# 默认走 hunter 网关(裸 IP 对用户隐藏 · 一 key 通用)· 见 app/services/saas_gateway.py
# 老 env KRONOS_URL 仍然最高优先级,可用它一键回退直连。


def _optional_user_id(request: Request | None) -> str | None:
    """kpred 是公开路由，middleware 不验证 token，但前端仍会带 Authorization header。
    尝试解析 JWT 获取 user_id，失败返回 None（不影响预测结果）。"""
    if not request:
        return None
    try:
        from app.routers.auth import verify_jwt
        auth = request.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.startswith("Bearer ") else ""
        if not token:
            return None
        payload = verify_jwt(token)
        return payload.get("sub") if payload else None
    except Exception:
        return None


@router.get("/kpred/{code}")
async def get_kpred(code: str, days: int = 10, request: Request = None):
    if days < 1 or days > 30:
        raise HTTPException(400, "days 范围 1~30")
    # 被动健康观测 · 见 services/source_health.py:每次真实调用就是一次探测
    from app.services import source_health as _sh
    import time as _time
    _t0 = _time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=3.0, read=30.0, write=5.0, pool=3.0)) as client:
            r = await client.post(
                f"{_gw.kronos_url()}/predict",
                json={"symbol": code, "pred_len": days},
                headers=_gw.kronos_headers(),
            )
        _sh.record("global.kronos", r.status_code == 200,
                   (_time.perf_counter() - _t0) * 1000,
                   "" if r.status_code == 200 else f"HTTP {r.status_code}: {r.text[:120]}")
        if r.status_code != 200:
            raise HTTPException(502, f"Kronos 服务错误: {r.text[:200]}")
        result = r.json()
        # 陈旧检测：非 watchlist 股票 → akshare 重锚定
        result, _stale = await _fix_stale_kronos_result(code, result)
        last_close = result.get('last_close', 0)
        preds      = result.get('predictions', [])
        if preds and last_close:
            result['predictions'] = apply_daily_limit(preds, last_close, code)

        # 后台写记忆（可选，失败不影响返回）
        user_id = _optional_user_id(request)
        if user_id:
            stock_name = result.get("name", code)
            last_close_val = result.get("last_close") or 0
            preds_list = result.get("predictions", [])
            last_pred_close = preds_list[-1].get("close") if preds_list else None
            if last_pred_close and last_close_val:
                ret_pct = (last_pred_close - last_close_val) / last_close_val * 100
                kpred_conclusion = "BUY" if ret_pct > 1 else "SELL" if ret_pct < -1 else "HOLD"
                kpred_confidence = min(abs(ret_pct) / 10, 1.0)
            else:
                kpred_conclusion, kpred_confidence = None, None

            async def _save():
                try:
                    from app.services.database import save_stock_memory, update_user_portrait
                    await asyncio.to_thread(
                        save_stock_memory,
                        user_id, code, stock_name, "kpred",
                        kpred_conclusion, kpred_confidence, None,
                    )
                    await asyncio.to_thread(update_user_portrait, user_id)
                except Exception as e:
                    logger.warning("kpred memory save failed: {}", e)
            asyncio.create_task(_save())

        if _stale:
            result["data_note"] = _stale
        return result
    except httpx.ConnectError as e:
        _sh.record("global.kronos", False, (_time.perf_counter() - _t0) * 1000, f"ConnectError: {e}")
        raise HTTPException(503, "Kronos 预测服务暂不可用")
    except httpx.TimeoutException as e:
        _sh.record("global.kronos", False, (_time.perf_counter() - _t0) * 1000, f"Timeout: {e}")
        raise HTTPException(504, "Kronos 预测超时，请稍后重试")


@router.get("/kpred/{code}/pro")
async def get_kpred_pro(
    code: str,
    days: int = 10,
    w_kronos:    Optional[float] = None,
    w_ma_align:  Optional[float] = None,
    w_macd:      Optional[float] = None,
    w_rsi:       Optional[float] = None,
    w_main_flow: Optional[float] = None,
    w_flow_div:  Optional[float] = None,
    w_candle_5d: Optional[float] = None,
    w_pe:        Optional[float] = None,
    request:     Request         = None,
):
    """Pro 多因子 K 线预测：Kronos + 8 个技术/资金/估值因子加权合成。
    可通过 w_* 参数传入自定义权重（会自动归一化）。"""
    if days < 1 or days > 30:
        raise HTTPException(400, "days 范围 1~30")

    # 构建自定义权重（若传入则覆盖默认值，自动归一化）
    raw_weights = {
        'kronos':    w_kronos,
        'ma_align':  w_ma_align,
        'macd':      w_macd,
        'rsi':       w_rsi,
        'main_flow': w_main_flow,
        'flow_div':  w_flow_div,
        'candle_5d': w_candle_5d,
        'pe':        w_pe,
    }
    custom_weights = None
    if any(v is not None for v in raw_weights.values()):
        merged = {k: (v if v is not None else WEIGHTS[k]) for k, v in raw_weights.items()}
        total = sum(merged.values())
        if total <= 0:
            raise HTTPException(400, "权重之和必须大于 0")
        custom_weights = {k: v / total for k, v in merged.items()}

    # Step 1: 调用 Kronos
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=3.0, read=30.0, write=5.0, pool=3.0)) as client:
            r = await client.post(
                f"{_gw.kronos_url()}/predict",
                json={"symbol": code, "pred_len": days},
                headers=_gw.kronos_headers(),
            )
        if r.status_code != 200:
            body_text = r.text[:200]
            # 关键: Kronos 未收录该股(通常是次新股/新股)→ 走无 Kronos 因子降级
            if "未找到" in body_text and ("K 线" in body_text or "K线" in body_text):
                logger.info("kpred %s Kronos 未收录, 走无 Kronos 因子降级 (Pro)", code)
                try:
                    return await _compute_no_kronos_prediction(code, days, custom_weights)
                except HTTPException:
                    raise
                except Exception as fb_err:
                    logger.warning("kpred %s no-kronos fallback 失败: %s", code, fb_err)
                    raise HTTPException(
                        404, f"该股 Kronos 未收录, 降级分析失败: {str(fb_err)[:150]}")
            raise HTTPException(502, f"Kronos 服务错误: {body_text}")
        kronos_result = r.json()
    except HTTPException:
        raise
    except httpx.ConnectError:
        raise HTTPException(503, "Kronos 预测服务暂不可用")
    except httpx.TimeoutException:
        raise HTTPException(504, "Kronos 预测超时，请稍后重试")

    # Step 1.5: 陈旧检测 — 非 watchlist 股票数据可能落后，用 akshare 重锚定
    kronos_result, _stale_note = await _fix_stale_kronos_result(code, kronos_result)

    # Step 2: 多因子引擎增强（带可选自定义权重）
    try:
        pro_result = await compute_pro_prediction(code, kronos_result, days, custom_weights)
    except Exception as e:
        raise HTTPException(500, f"Pro 因子计算失败: {str(e)[:200]}")

    # 后台写记忆（用 factor_return_pct 判断方向，可选，失败不影响返回）
    user_id = _optional_user_id(request)
    if user_id:
        stock_name = pro_result.get("name", code)
        factor_ret = pro_result.get("pro", {}).get("factor_return_pct", 0) or 0
        pro_conclusion = "BUY" if factor_ret > 1 else "SELL" if factor_ret < -1 else "HOLD"
        pro_confidence = min(abs(factor_ret) / 10, 1.0)

        async def _save_pro():
            try:
                from app.services.database import save_stock_memory, update_user_portrait
                await asyncio.to_thread(
                    save_stock_memory,
                    user_id, code, stock_name, "kpred",
                    pro_conclusion, pro_confidence, None,
                )
                await asyncio.to_thread(update_user_portrait, user_id)
            except Exception as e:
                logger.warning("kpred pro memory save failed: {}", e)
        asyncio.create_task(_save_pro())

    if _stale_note:
        pro_result["data_note"] = _stale_note
    return pro_result
